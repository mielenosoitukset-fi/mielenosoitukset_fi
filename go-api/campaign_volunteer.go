package main

// POST /kampanja/api/volunteers: the campaign volunteer signup (form v2, #1181),
// moved from Flask to Go and shipped dark (docs/go-campaign-volunteers.md). The
// route exists only when CAMPAIGN_VOLUNTEERS_GO_ENABLED=true and the read-only
// auth bridge is on; otherwise requests fall through exactly as before. Apache
// is not changed here: the browser keeps posting to Flask until an operator adds
// the rule from the document.
//
// Parity with Flask's add_volunteer() in mielenosoitukset_fi/kampanja/routes.py,
// in the same order:
//   - the campaign publication gate (private: only a signed-in Flask account
//     may sign up, asked through the auth bridge; a failed read is private);
//   - field rules: name and email required, no phone number (unknown keys are
//     ignored and never stored), role chips, area kept only with stickers,
//     format characters stripped, the same length limits and email shape;
//   - the same hashed rate-limit documents in volunteer_signup_attempts
//     (5 per client and hour; 2 per address and hour, 3 per day, silent 201);
//   - the same volunteers document, with only a hash of the link token;
//   - the same best-effort volunteer_signup analytics counter;
//   - the welcome mail rendered like kampanja/welcome.html and queued in
//     email_queue for Flask's worker. Go never sends mail.
//
// Success is reported only after the mail job insert was acknowledged.
//
// confirmCampaignVolunteer at the end is the Go side of the confirm link. It has
// no route here: GET /kampanja/confirm/<token> belongs to the token pages work.

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"html"
	"io"
	"log"
	"mime"
	"net/http"
	"os"
	"regexp"
	"strings"
	"text/template"
	"time"
	"unicode"
	"unicode/utf8"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
)

const (
	campaignVolunteerPath     = "/kampanja/api/volunteers"
	campaignVolunteerMaxBody  = 16 << 10
	campaignVolunteerInflight = 8
	campaignConfirmBase       = "https://mielenosoitukset.fi/kampanja/confirm/"
	campaignWelcomeSubject    = "Kiitos ilmoittautumisesta! Vahvista sähköpostisi"

	campaignLimitPerIP       = 5
	campaignLimitPerEmail    = 2
	campaignLimitPerEmailDay = 3
	campaignUnconfirmedKeep  = 7 * 24 * time.Hour
	campaignLinkValid        = 24 * time.Hour
)

var (
	campaignVolunteerRoles = []string{"stickers", "pride_banner", "events", "communications"}
	campaignFieldLimits    = []struct {
		key   string
		limit int
	}{{"name", 120}, {"city", 100}, {"area", 200}, {"notes", 2000}}
	campaignConfirmToken    = regexp.MustCompile(`^[A-Za-z0-9_-]{1,256}$`)
	campaignErrRequired     = "Nimi ja sähköpostiosoite ovat pakollisia."
	campaignErrFormat       = "Tarkista korostetut tiedot ja yritä uudelleen."
	campaignErrRate         = "Yhteys palveluun epäonnistui. Yritä myöhemmin uudelleen."
	campaignErrPrivate      = "Kirjaudu sisään nähdäksesi valmisteilla olevan kampanjan."
	campaignErrUnavailable  = "Yritä uudelleen hetken kuluttua."
	campaignErrTooLarge     = "Pyyntö on liian suuri."
	campaignWelcomeTemplate = template.Must(template.New("welcome").Funcs(template.FuncMap{"esc": html.EscapeString}).Parse(
		`<p>Hei {{esc .Name}}!</p>
<p>Kiitos, että ilmoittauduit vapaaehtoiseksi. Vahvista sähköpostisi klikkaamalla alla olevaa linkkiä:</p>
<p><a href="{{esc .Link}}">Vahvista ilmoittautuminen</a></p>
<p>Linkki on voimassa 24 tuntia.</p>`))
)

// campaignText is _text() in routes.py: a string only, format characters
// (right-to-left overrides, zero-width spaces) removed, then stripped like
// Python's str.strip().
func campaignText(data map[string]any, key string) string {
	value, ok := data[key].(string)
	if !ok {
		return ""
	}
	value = strings.Map(func(r rune) rune {
		if unicode.Is(unicode.Cf, r) {
			return -1
		}
		return r
	}, value)
	return analyticsTrim(value)
}

// campaignEmailValid is ^[^@\s]+@[^@\s]+\.[^@\s]+$ (Python's \s, Unicode aware).
func campaignEmailValid(email string) bool {
	space := func(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }
	local, domain, found := strings.Cut(email, "@")
	if !found || local == "" || strings.ContainsAny(domain, "@") ||
		strings.IndexFunc(local, space) >= 0 || strings.IndexFunc(domain, space) >= 0 {
		return false
	}
	runes := []rune(domain)
	for i := 1; i < len(runes)-1; i++ {
		if runes[i] == '.' {
			return true
		}
	}
	return false
}

type campaignFields struct {
	Name, Email, City, Area, Notes string
	Roles                          []string
}

// campaignParse is _volunteer_fields(): it returns the fields or the Finnish
// error message Flask would send with 400.
func campaignParse(data map[string]any) (campaignFields, string) {
	name, email := campaignText(data, "name"), campaignText(data, "email")
	if name == "" || email == "" {
		return campaignFields{}, campaignErrRequired
	}
	var chosen map[string]bool
	rolesOK := true
	switch raw := data["roles"].(type) {
	case nil:
		// A missing key is an empty list; an explicit null is not a list.
		if _, present := data["roles"]; present {
			rolesOK = false
		}
	case []any:
		chosen = map[string]bool{}
		for _, item := range raw {
			role, isString := item.(string)
			known := false
			for _, allowed := range campaignVolunteerRoles {
				known = known || (isString && role == allowed)
			}
			if !known {
				rolesOK = false
				break
			}
			chosen[role] = true
		}
	default:
		rolesOK = false
	}
	if !rolesOK || !campaignEmailValid(email) || utf8.RuneCountInString(email) > 254 {
		return campaignFields{}, campaignErrFormat
	}
	for _, field := range campaignFieldLimits {
		if utf8.RuneCountInString(campaignText(data, field.key)) > field.limit {
			return campaignFields{}, campaignErrFormat
		}
	}
	fields := campaignFields{Name: name, Email: email, City: campaignText(data, "city"), Notes: campaignText(data, "notes"), Roles: []string{}}
	for _, role := range campaignVolunteerRoles {
		if chosen[role] {
			fields.Roles = append(fields.Roles, role)
		}
	}
	if chosen["stickers"] {
		fields.Area = campaignText(data, "area")
	}
	return fields, ""
}

// campaignJSONBody mirrors request.get_json(silent=True): anything that is not
// a JSON object with a JSON content type is "no data", which fails the
// required-fields check.
func campaignJSONBody(r *http.Request, body []byte) map[string]any {
	mediaType, _, _ := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if mediaType != "application/json" && !(strings.HasPrefix(mediaType, "application/") && strings.HasSuffix(mediaType, "+json")) {
		return nil
	}
	if !utf8.Valid(body) {
		return nil
	}
	var data map[string]any
	if err := json.Unmarshal(body, &data); err != nil {
		return nil
	}
	return data
}

func campaignDigest(value string) string {
	sum := sha256.Sum256([]byte(value))
	return hex.EncodeToString(sum[:])
}

// campaignToken is secrets.token_urlsafe(16): 16 random bytes, unpadded URL-safe base64.
func campaignToken() (string, error) {
	buffer := make([]byte, 16)
	if _, err := rand.Read(buffer); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(buffer), nil
}

func campaignWelcomeBody(name, link string) (string, error) {
	var out bytes.Buffer
	err := campaignWelcomeTemplate.Execute(&out, struct{ Name, Link string }{name, link})
	return out.String(), err
}

// campaignVolunteerStore is everything the handler reads or writes in MongoDB.
type campaignVolunteerStore interface {
	// Published is the campaign publication flag; an error counts as private.
	Published(ctx context.Context) (bool, error)
	// PurgeAttempts drops rate-limit documents older than a day.
	PurgeAttempts(ctx context.Context, now time.Time) error
	// Attempt records one attempt under key and returns how many earlier
	// attempts fell inside the window. Recording happens before counting, so
	// concurrent requests can only over-count, never let extra ones through.
	Attempt(ctx context.Context, key string, now time.Time, window time.Duration) (int64, error)
	// PurgeUnconfirmed deletes signups whose link expired a week ago.
	PurgeUnconfirmed(ctx context.Context, before time.Time) error
	Insert(ctx context.Context, doc bson.D) (any, error)
	Delete(ctx context.Context, id any) error
	// Enqueue inserts one `email_queue` job and returns only once it is acknowledged.
	Enqueue(ctx context.Context, job bson.D) error
	// Count adds one to an aggregate site_analytics counter.
	Count(ctx context.Context, key bson.M) error
}

type mongoCampaignVolunteerStore struct{ db *mongo.Database }

func (s mongoCampaignVolunteerStore) Published(ctx context.Context) (bool, error) {
	return readCampaignPublication(s.db)(ctx)
}

func (s mongoCampaignVolunteerStore) PurgeAttempts(ctx context.Context, now time.Time) error {
	_, err := s.db.Collection("volunteer_signup_attempts").DeleteMany(ctx, bson.M{"at": bson.M{"$lt": now.Add(-24 * time.Hour)}})
	return err
}

func (s mongoCampaignVolunteerStore) Attempt(ctx context.Context, key string, now time.Time, window time.Duration) (int64, error) {
	attempts := s.db.Collection("volunteer_signup_attempts")
	if _, err := attempts.InsertOne(ctx, bson.D{{Key: "key", Value: key}, {Key: "at", Value: now}}); err != nil {
		return 0, err
	}
	total, err := attempts.CountDocuments(ctx, bson.M{"key": key, "at": bson.M{"$gte": now.Add(-window)}})
	if err != nil {
		return 0, err
	}
	return total - 1, nil
}

func (s mongoCampaignVolunteerStore) PurgeUnconfirmed(ctx context.Context, before time.Time) error {
	_, err := s.db.Collection("volunteers").DeleteMany(ctx, bson.M{
		"confirmed": bson.M{"$ne": true}, "confirmation_expires": bson.M{"$lt": before},
	})
	return err
}

func (s mongoCampaignVolunteerStore) Insert(ctx context.Context, doc bson.D) (any, error) {
	result, err := s.db.Collection("volunteers").InsertOne(ctx, doc)
	if err != nil {
		return nil, err
	}
	if !result.Acknowledged {
		return nil, errors.New("volunteer insert was not acknowledged")
	}
	return result.InsertedID, nil
}

func (s mongoCampaignVolunteerStore) Delete(ctx context.Context, id any) error {
	_, err := s.db.Collection("volunteers").DeleteOne(ctx, bson.M{"_id": id})
	return err
}

func (s mongoCampaignVolunteerStore) Enqueue(ctx context.Context, job bson.D) error {
	return mongoContactStore{db: s.db}.Enqueue(ctx, job)
}

func (s mongoCampaignVolunteerStore) Count(ctx context.Context, key bson.M) error {
	return mongoContactStore{db: s.db}.Count(ctx, key)
}

// campaignWelcomeJob has the shape of EmailJob.to_dict() (extra_headers empty).
func campaignWelcomeJob(recipient, body, instanceID string) bson.D {
	return bson.D{
		{Key: "subject", Value: campaignWelcomeSubject},
		{Key: "recipients", Value: bson.A{recipient}},
		{Key: "body", Value: body},
		{Key: "html", Value: body},
		{Key: "sender", Value: nil},
		{Key: "attachments", Value: nil},
		{Key: "extra_headers", Value: bson.D{}},
		{Key: "instance_id", Value: instanceID},
		{Key: "status", Value: "pending"},
		{Key: "attempts", Value: int32(0)},
	}
}

// campaignEventKey is record_event_for_request("volunteer_signup", city or None).
func campaignEventKey(r *http.Request, now time.Time, city string) bson.M {
	key := contactEventKey(r, now)
	if key == nil {
		return nil
	}
	key["event"] = "volunteer_signup"
	if city != "" {
		if runes := []rune(city); len(runes) > 200 {
			city = string(runes[:200])
		}
		key["resource_id"] = city
	}
	return key
}

type campaignVolunteerConfig struct {
	Enabled   bool
	Analytics bool
	Auth      authBridgeConfig
}

// campaignVolunteerConfigFromEnv is off unless CAMPAIGN_VOLUNTEERS_GO_ENABLED=true.
// It also needs the auth bridge, which answers who may sign up while the
// campaign is still private.
func campaignVolunteerConfigFromEnv(auth authBridgeConfig) (campaignVolunteerConfig, error) {
	c := campaignVolunteerConfig{Enabled: os.Getenv("CAMPAIGN_VOLUNTEERS_GO_ENABLED") == "true", Auth: auth, Analytics: analyticsEnabledFromEnv()}
	if c.Enabled && !auth.Enabled {
		return c, errors.New("campaign volunteers need the auth bridge")
	}
	return c, nil
}

type campaignVolunteerDeps struct {
	store     campaignVolunteerStore
	principal func(ctx context.Context, r *http.Request) (Principal, error)
	now       func() time.Time
	token     func() (string, error)
	instance  string
}

func campaignJSON(w http.ResponseWriter, status int, success bool, message string) {
	body := map[string]any{"success": success}
	if message != "" {
		body["error"] = message
	}
	writeJSON(w, status, body)
}

func campaignVolunteerHandler(next http.Handler, config campaignVolunteerConfig, deps campaignVolunteerDeps) http.Handler {
	inflight := make(chan struct{}, campaignVolunteerInflight)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != campaignVolunteerPath {
			next.ServeHTTP(w, r)
			return
		}
		w.Header().Set("X-Content-Type-Options", "nosniff")
		if r.Method != http.MethodPost {
			w.Header().Set("Allow", "POST")
			w.Header().Set("Cache-Control", "no-store")
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		select {
		case inflight <- struct{}{}:
			defer func() { <-inflight }()
		default:
			w.Header().Set("Cache-Control", "no-store")
			w.Header().Set("Retry-After", "5")
			writeError(w, http.StatusServiceUnavailable, "service busy")
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 8*time.Second)
		defer cancel()
		// The outer public gate already refuses every request while a global or
		// security panic record is active, as Flask's before_request hook does.

		// Publication gate: only an explicit published: true is public. A failed
		// read is private, as in Flask.
		published, err := deps.store.Published(ctx)
		published = err == nil && published
		w.Header().Set("Cache-Control", "private, no-store")
		w.Header().Add("Vary", "Cookie")
		if !published {
			w.Header().Set("X-Robots-Tag", "noindex, nofollow")
			principal, err := deps.principal(ctx, r)
			if err != nil {
				log.Print("Campaign volunteer session check unavailable")
				campaignJSON(w, http.StatusServiceUnavailable, false, campaignErrUnavailable)
				return
			}
			if !principal.Authenticated {
				campaignJSON(w, http.StatusUnauthorized, false, campaignErrPrivate)
				return
			}
		}

		if r.ContentLength > campaignVolunteerMaxBody {
			campaignJSON(w, http.StatusRequestEntityTooLarge, false, campaignErrTooLarge)
			return
		}
		body, err := io.ReadAll(io.LimitReader(r.Body, campaignVolunteerMaxBody+1))
		if err != nil || len(body) > campaignVolunteerMaxBody {
			campaignJSON(w, http.StatusRequestEntityTooLarge, false, campaignErrTooLarge)
			return
		}
		fields, problem := campaignParse(campaignJSONBody(r, body))
		if problem != "" {
			campaignJSON(w, http.StatusBadRequest, false, problem)
			return
		}

		now := deps.now().UTC()
		unavailable := func(what string, err error) {
			log.Printf("Campaign volunteer signup: %s (%s)", what, dbErrorKind(err))
			campaignJSON(w, http.StatusServiceUnavailable, false, campaignErrUnavailable)
		}
		if err := deps.store.PurgeAttempts(ctx, now); err != nil {
			unavailable("rate limit storage", err)
			return
		}
		over := func(kind, value string, limit int64, window time.Duration) (bool, error) {
			recent, err := deps.store.Attempt(ctx, kind+":"+campaignDigest(strings.ToLower(value)), now, window)
			return recent >= limit, err
		}
		limited, err := over("ip", contactClientKey(r), campaignLimitPerIP, time.Hour)
		if err != nil {
			unavailable("rate limit storage", err)
			return
		}
		if limited {
			campaignJSON(w, http.StatusTooManyRequests, false, campaignErrRate)
			return
		}
		// Do not reveal or act on repeats: the same address gets one pending mail, silently.
		repeat, err := over("email", fields.Email, campaignLimitPerEmail, time.Hour)
		if err == nil && !repeat {
			repeat, err = over("email-day", fields.Email, campaignLimitPerEmailDay, 24*time.Hour)
		}
		if err != nil {
			unavailable("rate limit storage", err)
			return
		}
		if repeat {
			campaignJSON(w, http.StatusCreated, true, "")
			return
		}
		if err := deps.store.PurgeUnconfirmed(ctx, now.Add(-campaignUnconfirmedKeep)); err != nil {
			unavailable("unconfirmed purge", err)
			return
		}

		token, err := deps.token()
		if err != nil {
			unavailable("token", err)
			return
		}
		id, err := deps.store.Insert(ctx, bson.D{
			{Key: "name", Value: fields.Name}, {Key: "email", Value: fields.Email}, {Key: "city", Value: fields.City},
			{Key: "roles", Value: fields.Roles}, {Key: "area", Value: fields.Area}, {Key: "notes", Value: fields.Notes},
			{Key: "confirmed", Value: false},
			// Only a hash of the link token is stored.
			{Key: "confirmation_token_hash", Value: campaignDigest(token)},
			{Key: "confirmation_expires", Value: now.Add(campaignLinkValid)},
		})
		if err != nil {
			unavailable("volunteer insert", err)
			return
		}
		if config.Analytics {
			if key := campaignEventKey(r, now, fields.City); key != nil {
				countContext, stop := context.WithTimeout(r.Context(), time.Second)
				if deps.store.Count(countContext, key) != nil {
					log.Print("Campaign volunteer analytics counter unavailable")
				}
				stop()
			}
		}
		mail, err := campaignWelcomeBody(fields.Name, campaignConfirmBase+token)
		if err == nil {
			err = deps.store.Enqueue(ctx, campaignWelcomeJob(fields.Email, mail, deps.instance))
		}
		if err != nil {
			// Without a queued mail the stored signup is unreachable: remove it so
			// a retry starts clean. An ambiguous queue write can still leave a mail.
			cleanup, stop := context.WithTimeout(context.WithoutCancel(ctx), 2*time.Second)
			_ = deps.store.Delete(cleanup, id)
			stop()
			unavailable("mail queue", err)
			return
		}
		campaignJSON(w, http.StatusCreated, true, "")
	})
}

// campaignVolunteerHandlerFromEnv wires the route in main. A missing or invalid
// setting leaves the route off; it never stops the public API from starting.
func campaignVolunteerHandlerFromEnv(next http.Handler, db *mongo.Database, auth authBridgeConfig) http.Handler {
	config, err := campaignVolunteerConfigFromEnv(auth)
	if err != nil {
		log.Print("Campaign volunteer route disabled: ", err)
		return next
	}
	if !config.Enabled {
		return next
	}
	resolver, err := newSessionResolver(auth)
	if err != nil {
		log.Print("Campaign volunteer route disabled: invalid session bridge")
		return next
	}
	return campaignVolunteerHandler(next, config, campaignVolunteerDeps{
		store: mongoCampaignVolunteerStore{db: db},
		principal: func(ctx context.Context, r *http.Request) (Principal, error) {
			return resolver.Resolve(ctx, r, authScope{})
		},
		now:      time.Now,
		token:    campaignToken,
		instance: contactUUID(),
	})
}

// ---- Confirm link (Go side only; no route) ----

type campaignConfirmStatus string

const (
	campaignConfirmed campaignConfirmStatus = "success"
	campaignInvalid   campaignConfirmStatus = "invalid"
	campaignExpired   campaignConfirmStatus = "expired"
)

// campaignConfirmStore is what confirmCampaignVolunteer needs.
type campaignConfirmStore interface {
	// ConfirmUnexpired atomically confirms the volunteer whose token hash (or
	// legacy plain token) matches and whose link has not expired, removing the
	// token fields, and reports whether one document changed.
	ConfirmUnexpired(ctx context.Context, hash, plain string, now time.Time) (bool, error)
	// TokenKnown reports whether any volunteer still carries this token.
	TokenKnown(ctx context.Context, hash, plain string) (bool, error)
}

// confirmCampaignVolunteer is confirm_volunteer() without the HTML. A token not
// in the issued shape is invalid without a query. A link is valid up to and
// including its expiry instant (Flask expires only when strictly before now).
// The conditional update makes two simultaneous clicks confirm once: the loser
// finds no token and gets "invalid", as a sequential replay does in Flask. The
// caller must keep the token out of logs and analytics.
func confirmCampaignVolunteer(ctx context.Context, store campaignConfirmStore, token string, now time.Time) (campaignConfirmStatus, error) {
	if !campaignConfirmToken.MatchString(token) {
		return campaignInvalid, nil
	}
	hash := campaignDigest(token)
	changed, err := store.ConfirmUnexpired(ctx, hash, token, now.UTC())
	if err != nil {
		return "", err
	}
	if changed {
		return campaignConfirmed, nil
	}
	known, err := store.TokenKnown(ctx, hash, token)
	if err != nil {
		return "", err
	}
	if known {
		return campaignExpired, nil
	}
	return campaignInvalid, nil
}

func (s mongoCampaignVolunteerStore) tokenFilter(hash, plain string) bson.M {
	return bson.M{"$or": bson.A{bson.M{"confirmation_token_hash": hash}, bson.M{"confirmation_token": plain}}}
}

func (s mongoCampaignVolunteerStore) ConfirmUnexpired(ctx context.Context, hash, plain string, now time.Time) (bool, error) {
	filter := s.tokenFilter(hash, plain)
	filter["confirmation_expires"] = bson.M{"$gte": now}
	result, err := s.db.Collection("volunteers").UpdateOne(ctx, filter, bson.M{
		"$set":   bson.M{"confirmed": true},
		"$unset": bson.M{"confirmation_token": "", "confirmation_token_hash": "", "confirmation_expires": ""},
	})
	if err != nil {
		return false, err
	}
	return result.ModifiedCount == 1, nil
}

func (s mongoCampaignVolunteerStore) TokenKnown(ctx context.Context, hash, plain string) (bool, error) {
	err := s.db.Collection("volunteers").FindOne(ctx, s.tokenFilter(hash, plain)).Err()
	if errors.Is(err, mongo.ErrNoDocuments) {
		return false, nil
	}
	return err == nil, err
}
