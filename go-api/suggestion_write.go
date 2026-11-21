package main

// POST /organization/{id}/save_suggestion and POST /suggest_change/{demo_id}
// moved from Flask (docs/go-public-api.md, "Suggestion forms"). Shipped dark:
// the routes answer only when SUGGESTIONS_GO_ENABLED=true and public
// submissions are configured, and Apache does not route to them yet.
//
// Parity rules: same stored documents, same case record, the same rendered
// moderator mail in email_queue (Flask's worker sends it; Go never sends mail),
// same 302 redirects. Go cannot set Flask's flash cookie, so the visitor gets
// the redirect without the flash message (open question in the pull request).

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"log"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strings"
	"sync"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

const (
	// Werkzeug's default max_form_memory_size, which Flask applies today.
	suggestionMaxBody = 500_000
	// Safety bound, not in Flask: the Markdown port is quadratic on hostile input.
	suggestionMaxMarkdown = 50_000
	suggestionRecipient   = "tuki@mielenosoitukset.fi"
	caseStartingNumber    = 100_001
)

var (
	suggestionOrgPath  = regexp.MustCompile(`^/organization/([^/]+)/save_suggestion$`)
	suggestionDemoPath = regexp.MustCompile(`^/suggest_change/([^/]+)$`)
	suggestionObjectID = regexp.MustCompile(`^[0-9a-fA-F]{24}$`)
)

func suggestionsEnabledFromEnv() bool {
	return strings.EqualFold(strings.TrimSpace(os.Getenv("SUGGESTIONS_GO_ENABLED")), "true")
}

type suggestionStore interface {
	FindOrganizationName(ctx context.Context, id bson.ObjectID) (any, bool, error)
	FindDemonstration(ctx context.Context, id bson.ObjectID) (bson.D, bool, error)
	InsertOrgSuggestion(ctx context.Context, doc bson.D) error
	CreateCase(ctx context.Context, build func(runningNumber int64) bson.D) error
	InsertDemoSuggestion(ctx context.Context, doc bson.D) error
	QueueMail(ctx context.Context, doc bson.D) error
}

type suggestionWrites struct {
	store    suggestionStore
	origin   string
	actor    func(*http.Request) (bson.ObjectID, error)
	now      func() time.Time
	instance string
}

type mongoSuggestionStore struct{ db *mongo.Database }

func (s mongoSuggestionStore) FindOrganizationName(ctx context.Context, id bson.ObjectID) (any, bool, error) {
	var doc bson.D
	err := s.db.Collection("organizations").FindOne(ctx, bson.M{"_id": id}, options.FindOne().SetProjection(bson.M{"name": 1})).Decode(&doc)
	if errors.Is(err, mongo.ErrNoDocuments) {
		return nil, false, nil
	}
	name, _ := documentValue(doc, "name")
	return name, err == nil, err
}

func (s mongoSuggestionStore) FindDemonstration(ctx context.Context, id bson.ObjectID) (bson.D, bool, error) {
	var doc bson.D
	err := s.db.Collection("demonstrations").FindOne(ctx, bson.M{"_id": id}).Decode(&doc)
	if errors.Is(err, mongo.ErrNoDocuments) {
		return nil, false, nil
	}
	return doc, err == nil, err
}

func (s mongoSuggestionStore) InsertOrgSuggestion(ctx context.Context, doc bson.D) error {
	_, err := s.db.Collection("org_edit_suggestions").InsertOne(ctx, doc)
	return err
}

func (s mongoSuggestionStore) InsertDemoSuggestion(ctx context.Context, doc bson.D) error {
	_, err := s.db.Collection("demo_suggestions").InsertOne(ctx, doc)
	return err
}

func (s mongoSuggestionStore) QueueMail(ctx context.Context, doc bson.D) error {
	_, err := s.db.Collection("email_queue").InsertOne(ctx, doc)
	return err
}

// CreateCase repeats Case.create_new: read the highest running number, then
// upsert the case by _id. The read-then-write race is Flask's too.
func (s mongoSuggestionStore) CreateCase(ctx context.Context, build func(int64) bson.D) error {
	next := int64(caseStartingNumber)
	var last bson.D
	err := s.db.Collection("cases").FindOne(ctx, bson.M{}, options.FindOne().SetSort(bson.D{{Key: "running_num", Value: -1}})).Decode(&last)
	if err == nil {
		current := int64(caseStartingNumber - 1)
		if value, ok := documentValue(last, "running_num"); ok {
			switch typed := value.(type) {
			case int32:
				current = int64(typed)
			case int64:
				current = typed
			case float64:
				current = int64(typed)
			}
		}
		next = current + 1
	} else if !errors.Is(err, mongo.ErrNoDocuments) {
		return err
	}
	doc := build(next)
	var id bson.ObjectID
	set := bson.D{}
	for _, item := range doc {
		if item.Key == "_id" {
			id, _ = item.Value.(bson.ObjectID)
		}
		set = append(set, item)
	}
	_, err = s.db.Collection("cases").UpdateOne(ctx, bson.M{"_id": id}, bson.M{"$set": set}, options.UpdateOne().SetUpsert(true))
	return err
}

// ---------------------------------------------------------------- HTTP

func suggestionHandler(next http.Handler, db *mongo.Database, config submissionConfig, enabled bool) http.Handler {
	if !enabled || config.Origin == "" || config.Secret == "" {
		return next
	}
	writes := &suggestionWrites{
		store: mongoSuggestionStore{db}, origin: config.Origin, now: time.Now,
		instance: newInstanceID(),
		actor: func(r *http.Request) (bson.ObjectID, error) {
			ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
			defer cancel()
			return submissionActor(ctx, r, config)
		},
	}
	var inflight = make(chan struct{}, 8)
	var once sync.Once
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		orgMatch := suggestionOrgPath.FindStringSubmatch(r.URL.Path)
		demoMatch := suggestionDemoPath.FindStringSubmatch(r.URL.Path)
		if r.Method != http.MethodPost || (orgMatch == nil && demoMatch == nil) {
			next.ServeHTTP(w, r)
			return
		}
		once.Do(func() { log.Print("Go suggestion routes active") })
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		if r.Header.Get("Sec-Fetch-Site") == "cross-site" || (r.Header.Get("Origin") != "" && !submissionOriginAllowed(r.Header.Get("Origin"), config.Origin)) {
			writeError(w, http.StatusForbidden, "forbidden")
			return
		}
		select {
		case inflight <- struct{}{}:
			defer func() { <-inflight }()
		default:
			w.Header().Set("Retry-After", "5")
			writeError(w, http.StatusServiceUnavailable, "busy")
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 8*time.Second)
		defer cancel()
		cursor := db.Collection("panic").FindOne(ctx, bson.M{"name": bson.M{"$in": bson.A{"global", "security"}}, "panic": true})
		if err := cursor.Err(); err == nil || !errors.Is(err, mongo.ErrNoDocuments) {
			w.Header().Set("Retry-After", "60")
			writeError(w, http.StatusServiceUnavailable, "service unavailable")
			return
		}
		// Flask's shared defaults: 10 per second, 3600 per hour, per client.
		allowed, err := windowedRate(ctx, db, "suggestion:"+submissionClient(r, config.Secret), 600, 3600)
		if err != nil {
			writeError(w, http.StatusServiceUnavailable, "service unavailable")
			return
		}
		if !allowed {
			w.Header().Set("Retry-After", "60")
			writeError(w, http.StatusTooManyRequests, "too many requests")
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, suggestionMaxBody)
		if err := parseSuggestionForm(r); err != nil {
			status := http.StatusBadRequest
			var tooLarge *http.MaxBytesError
			if errors.As(err, &tooLarge) {
				status = http.StatusRequestEntityTooLarge
			}
			writeError(w, status, "invalid form")
			return
		}
		client := submissionClientAddress(r)
		if client == "" {
			client = "0.0.0.0"
		}
		if orgMatch != nil {
			writes.saveOrganization(ctx, w, r, orgMatch[1], client)
		} else {
			writes.saveDemonstration(ctx, w, r, demoMatch[1], client)
		}
	})
}

func newInstanceID() string {
	raw := make([]byte, 16)
	if _, err := rand.Read(raw); err != nil {
		panic(err)
	}
	raw[6], raw[8] = raw[6]&0x0f|0x40, raw[8]&0x3f|0x80
	text := hex.EncodeToString(raw)
	return text[:8] + "-" + text[8:12] + "-" + text[12:16] + "-" + text[16:20] + "-" + text[20:]
}

func parseSuggestionForm(r *http.Request) error {
	if strings.HasPrefix(r.Header.Get("Content-Type"), "multipart/form-data") {
		if err := r.ParseMultipartForm(suggestionMaxBody); err != nil {
			return err
		}
		if r.MultipartForm != nil {
			for key, values := range r.MultipartForm.Value {
				r.PostForm[key] = values
			}
		}
		return nil
	}
	return r.ParseForm()
}

func formValues(r *http.Request, key string) []string {
	values := r.PostForm[key]
	out := make([]string, len(values))
	for index, value := range values {
		out[index] = strings.ToValidUTF8(value, "�")
	}
	return out
}

func formValue(r *http.Request, key string) string {
	if values := formValues(r, key); len(values) > 0 {
		return values[0]
	}
	return ""
}

func redirectTo(w http.ResponseWriter, location string) {
	w.Header().Set("Location", location)
	w.WriteHeader(http.StatusFound)
}

// formURL sends the visitor back to the form with a notice flag the React
// page turns into a message, since Go cannot set Flask's flash cookie.
func (s *suggestionWrites) formURL(rawID, notice string) string {
	return s.origin + "/suggest_change/" + url.PathEscape(rawID) + "?suggestion=" + notice
}

func (s *suggestionWrites) mailJob(subject, html string) bson.D {
	return bson.D{
		{Key: "subject", Value: subject}, {Key: "recipients", Value: bson.A{suggestionRecipient}},
		{Key: "body", Value: html}, {Key: "html", Value: html}, {Key: "sender", Value: nil},
		{Key: "attachments", Value: bson.A{}}, {Key: "extra_headers", Value: bson.D{}},
		{Key: "instance_id", Value: s.instance}, {Key: "status", Value: "pending"}, {Key: "attempts", Value: 0},
	}
}

func userAgentValue(r *http.Request) any {
	values := r.Header.Values("User-Agent")
	if len(values) == 0 {
		return nil
	}
	return strings.ToValidUTF8(values[0], "�")
}

// ---------------------------------------------------------------- organization

func (s *suggestionWrites) saveOrganization(ctx context.Context, w http.ResponseWriter, r *http.Request, rawID, client string) {
	if !suggestionObjectID.MatchString(rawID) {
		writeError(w, http.StatusBadRequest, "invalid organization ID")
		return
	}
	id, _ := bson.ObjectIDFromHex(rawID)
	name, found, err := s.store.FindOrganizationName(ctx, id)
	if err != nil {
		log.Printf("Organization suggestion lookup failed (%s)", dbErrorKind(err))
		writeError(w, http.StatusServiceUnavailable, "service unavailable")
		return
	}
	if !found {
		redirectTo(w, "/")
		return
	}
	fields := bson.D{}
	mailFields := []orgMailField{}
	for _, field := range []string{"name", "description", "website", "email", "logo"} {
		if value := analyticsTrim(formValue(r, field)); value != "" {
			fields = append(fields, bson.E{Key: field, Value: value})
			mailFields = append(mailFields, orgMailField{field, value})
		}
	}
	platforms, urls := formValues(r, "social_platform[]"), formValues(r, "social_url[]")
	socials := bson.D{}
	for index := 0; index < len(platforms) && index < len(urls); index++ {
		platform, link := analyticsTrim(platforms[index]), analyticsTrim(urls[index])
		if platform == "" || link == "" {
			continue
		}
		replaced := false
		for position := range socials {
			if socials[position].Key == platform {
				socials[position].Value, replaced = link, true
			}
		}
		if !replaced {
			socials = append(socials, bson.E{Key: platform, Value: link})
		}
	}
	if len(socials) > 0 {
		fields = append(fields, bson.E{Key: "social_media_links", Value: socials})
		mailFields = append(mailFields, orgMailField{"social_media_links", socials})
	}
	actor := bson.NilObjectID
	if r.Header.Get("Cookie") != "" && s.actor != nil {
		if actor, err = s.actor(r); forcedStepPath(err) != "" {
			writeError(w, http.StatusForbidden, "account step required")
			return
		} else if err != nil {
			writeError(w, http.StatusServiceUnavailable, "session unavailable")
			return
		}
	}
	now := s.now().UTC().Truncate(time.Millisecond)
	suggestionID := bson.NewObjectID()
	agent := userAgentValue(r)
	var submitterEmail any
	if values := formValues(r, "email"); len(values) > 0 {
		submitterEmail = values[0]
	}
	body := func(withID bool) bson.D {
		doc := bson.D{}
		if withID {
			doc = append(doc, bson.E{Key: "_id", Value: suggestionID})
		}
		doc = append(doc,
			bson.E{Key: "organization_id", Value: id}, bson.E{Key: "timestamp", Value: bson.NewDateTimeFromTime(now)},
			bson.E{Key: "created_at", Value: bson.NewDateTimeFromTime(now)}, bson.E{Key: "fields", Value: fields},
			bson.E{Key: "meta", Value: bson.D{{Key: "ip", Value: client}, {Key: "user_agent", Value: agent}, {Key: "submitter_email", Value: submitterEmail}}},
			bson.E{Key: "status", Value: bson.D{{Key: "state", Value: "pending"}, {Key: "updated_at", Value: bson.NewDateTimeFromTime(now)}, {Key: "updated_by", Value: nil}, {Key: "notes", Value: nil}}},
			bson.E{Key: "audit_log", Value: bson.A{bson.D{{Key: "action", Value: "created"}, {Key: "timestamp", Value: bson.NewDateTimeFromTime(now)}, {Key: "user", Value: nil}}}},
		)
		return doc
	}
	if err := s.store.InsertOrgSuggestion(ctx, body(true)); err != nil {
		log.Printf("Organization suggestion save failed (%s)", dbErrorKind(err))
		writeError(w, http.StatusServiceUnavailable, "service unavailable")
		return
	}
	var submitter any
	if !actor.IsZero() {
		submitter = actor
	}
	// The embedded copy carries _id last, as pymongo's in-place insert leaves it.
	embedded := append(body(false), bson.E{Key: "_id", Value: suggestionID})
	err = s.store.CreateCase(ctx, func(running int64) bson.D {
		return bson.D{
			{Key: "_id", Value: bson.NewObjectID()}, {Key: "type", Value: "organization_edit_suggestion"},
			{Key: "demo_id", Value: nil}, {Key: "organization_id", Value: nil}, {Key: "submitter_id", Value: submitter},
			{Key: "submitter", Value: nil}, {Key: "error_report", Value: bson.D{}}, {Key: "suggestion", Value: embedded},
			{Key: "meta", Value: bson.D{{Key: "urgency", Value: "high"}}}, {Key: "action_logs", Value: bson.A{}},
			{Key: "case_history", Value: bson.A{}}, {Key: "running_num", Value: running},
			{Key: "created_at", Value: bson.NewDateTimeFromTime(now)}, {Key: "updated_at", Value: bson.NewDateTimeFromTime(now)},
		}
	})
	if err != nil {
		log.Printf("Organization suggestion case failed (%s)", dbErrorKind(err))
		writeError(w, http.StatusServiceUnavailable, "service unavailable")
		return
	}
	review := s.origin + "/admin/organization/" + rawID + "/suggestion/" + suggestionID.Hex()
	html := renderOrgSuggestionMail(name, mailFields, review, now, client, agent)
	if err := s.store.QueueMail(ctx, s.mailJob("Uusi organisaatiotietojen muokkauspyyntö: "+pyStr(name), html)); err != nil {
		// Flask's non-strict queue_email logs and still reports success.
		log.Printf("Moderator mail not queued (%s)", dbErrorKind(err))
	}
	redirectTo(w, "/organization/"+rawID+"?suggestion=saved")
}

// ---------------------------------------------------------------- demonstration

func demoField(doc bson.D, key string) (any, bool) { return documentValue(doc, key) }

func (s *suggestionWrites) saveDemonstration(ctx context.Context, w http.ResponseWriter, r *http.Request, rawID, client string) {
	var demo bson.D
	found := false
	if suggestionObjectID.MatchString(rawID) {
		id, _ := bson.ObjectIDFromHex(rawID)
		var err error
		demo, found, err = s.store.FindDemonstration(ctx, id)
		if err != nil {
			// Flask treats any lookup exception as "not found".
			log.Printf("Demonstration suggestion lookup failed (%s)", dbErrorKind(err))
			found = false
		}
	}
	if !found {
		redirectTo(w, "/")
		return
	}
	suggested, original := bson.D{}, bson.D{}
	record := func(field string, value any) {
		suggested = append(suggested, bson.E{Key: field, Value: value})
		stored, _ := demoField(demo, field)
		original = append(original, bson.E{Key: field, Value: stored})
	}
	for _, field := range []string{"title", "date", "start_time", "end_time", "city", "address", "facebook", "tags", "route", "organizers"} {
		value := analyticsTrim(formValue(r, field))
		if lower := strings.ToLower(value); lower == "none" || lower == "null" {
			value = ""
		}
		stored, _ := demoField(demo, field)
		switch field {
		case "tags":
			list := normalizeTagText(value)
			var comparable []string
			if items, ok := listItems(stored); ok || isBSONArray(stored) {
				if !ok {
					items = bsonArrayItems(stored)
				}
				comparable = normalizeTagList(items)
				if value != "" && len(list) > 0 && !equalStrings(list, comparable) {
					record(field, stringsToArray(list))
				}
			} else if value != "" && len(list) > 0 {
				record(field, stringsToArray(list))
			}
		case "route":
			if value == "" {
				continue
			}
			list := normalizeRoutePoints(value)
			if len(list) > 0 && !equalStrings(list, normalizeRoutePoints(stored)) {
				record(field, stringsToArray(list))
			}
		case "organizers":
			if value == "" {
				continue
			}
			entries := normalizeOrganizerText(value)
			if len(entries) > 0 && !equalOrganizers(entries, normalizeStoredOrganizers(stored)) {
				array := bson.A{}
				for _, entry := range entries {
					array = append(array, entry.document())
				}
				record(field, array)
			}
		case "start_time", "end_time":
			if value != "" && !sameClock(value, scalarComparable(stored)) {
				record(field, value)
			}
		default:
			if value != "" && value != scalarComparable(stored) {
				record(field, value)
			}
		}
	}
	description := analyticsTrim(formValue(r, "description_markdown"))
	if len(description) > suggestionMaxMarkdown {
		redirectTo(w, s.formURL(rawID, "too_long"))
		return
	}
	storedDescription, _ := demoField(demo, "description")
	originalDescription := ""
	if pyTruthy(storedDescription) {
		originalDescription = pyStr(storedDescription)
	}
	if description != "" && description != analyticsTrim(descriptionMarkdown(originalDescription)) {
		if html := markdownToHTML(description); html != "" && html != originalDescription {
			record("description", html)
		}
	}
	comment, reporterMail := analyticsTrim(formValue(r, "reporter_comment")), analyticsTrim(formValue(r, "reporter_email"))
	if len(suggested) == 0 && comment == "" {
		redirectTo(w, s.formURL(rawID, "empty"))
		return
	}
	now := s.now().UTC().Truncate(time.Millisecond)
	agent := userAgentValue(r)
	var commentValue, mailValue any
	if comment != "" {
		commentValue = comment
	}
	if reporterMail != "" {
		mailValue = reporterMail
	}
	doc := bson.D{
		{Key: "demo_id", Value: rawID}, {Key: "suggested_fields", Value: suggested}, {Key: "original_values", Value: original},
		{Key: "reporter_comment", Value: commentValue}, {Key: "reporter_email", Value: mailValue},
		{Key: "created_at", Value: bson.NewDateTimeFromTime(now)}, {Key: "ip", Value: client},
		{Key: "user_agent", Value: agent}, {Key: "status", Value: "new"},
	}
	if err := s.store.InsertDemoSuggestion(ctx, doc); err != nil {
		log.Printf("Demonstration suggestion save failed (%s)", dbErrorKind(err))
		redirectTo(w, s.formURL(rawID, "failed"))
		return
	}
	changes := []demoMailChange{}
	for _, item := range suggested {
		stored, _ := documentValue(original, item.Key)
		changes = append(changes, demoMailChange{Field: item.Key, Value: item.Value, Original: stored, HasOrig: stored != nil})
	}
	html := renderDemoSuggestionMail(demoMailInput{
		Demo: func(key string) (any, bool) { return demoField(demo, key) }, Changes: changes,
		Comment: comment, ReporterMail: reporterMail, IP: client, UserAgent: agent, CreatedAt: now,
	})
	title, present := demoField(demo, "title")
	if err := s.store.QueueMail(ctx, s.mailJob("Uusi ehdotus mielenosoitukselle: "+pyStrIfPresentNone(title, present), html)); err != nil {
		log.Printf("Moderator mail not queued (%s)", dbErrorKind(err))
	}
	redirectTo(w, "/demonstration/"+url.PathEscape(rawID)+"?suggestion=sent")
}

// pyStrIfPresentNone is demo_doc.get("title") inside an f-string: a missing key prints None.
func pyStrIfPresentNone(value any, present bool) string {
	if !present {
		return "None"
	}
	return pyStr(value)
}

func scalarComparable(value any) string {
	if !pyTruthy(value) {
		return ""
	}
	return analyticsTrim(pyStr(value))
}

func isBSONArray(value any) bool { _, ok := value.(bson.A); return ok }

func bsonArrayItems(value any) []any { return []any(value.(bson.A)) }

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for index := range a {
		if a[index] != b[index] {
			return false
		}
	}
	return true
}

func equalOrganizers(a, b []organizerEntry) bool {
	if len(a) != len(b) {
		return false
	}
	for index := range a {
		if a[index] != b[index] {
			return false
		}
	}
	return true
}

func stringsToArray(values []string) bson.A {
	out := make(bson.A, len(values))
	for index, value := range values {
		out[index] = value
	}
	return out
}
