package main

// POST /contact: the support contact form, moved from Flask to Go and shipped
// dark (docs/go-contact.md). The route exists only when CONTACT_GO_ENABLED=true
// and the read-only auth bridge is on; otherwise requests fall through exactly
// as before. Apache is not changed here: the browser keeps posting to Flask
// until an operator adds the routing rule from the document.
//
// Parity with Flask's handler (mielenosoitukset_fi/basic_routes.py, contact):
//   - the same Flask-Limiter counters (database "limits", collection
//     "counters", same keys), so a switch in either direction keeps the quotas;
//   - the one-time token is checked read-only in Flask's session through
//     /api/session and consumed once in MongoDB by its SHA-256 hash;
//   - the same field rules, honeypot and mail, queued as a rendered
//     `email_queue` job that Flask's worker sends. Go never sends mail.
//
// Success is reported only after the mail job insert was acknowledged.

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"io"
	"log"
	"mime"
	"net/http"
	"net/netip"
	"net/url"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"text/template"
	"time"
	"unicode/utf8"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
	"golang.org/x/text/cases"
)

const (
	contactPath           = "/contact"
	contactSupportAddress = "tuki@mielenosoitukset.fi"
	contactMailSubject    = "Uusi tukipyyntö!"
	contactTokenPattern   = `^[A-Za-z0-9_-]{1,256}$`
	contactDefaultBody    = 16384
	contactInflight       = 8
)

var contactSubjects = map[string]bool{"Tuki": true, "Yleinen kysymys": true, "Palautetta": true, "Muu": true}

// Python's email check in utils/validators.py, ASCII only.
var contactEmailPattern = regexp.MustCompile(`^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$`)
var contactToken = regexp.MustCompile(contactTokenPattern)

// contactLimit is one Flask-Limiter rate such as "3 per 10 minutes".
type contactLimit struct {
	Amount, Multiples int64
	Granularity       string
	Seconds           int64
}

func (l contactLimit) window() time.Duration {
	return time.Duration(l.Seconds*l.Multiples) * time.Second
}

// key is the suffix Flask-Limiter (limits library) appends to its identifiers.
func (l contactLimit) suffix() string {
	return fmt.Sprintf("%d/%d/%s", l.Amount, l.Multiples, l.Granularity)
}

var contactGranularities = map[string]int64{"second": 1, "minute": 60, "hour": 3600, "day": 86400, "month": 86400 * 30, "year": 86400 * 30 * 12}
var contactLimitPart = regexp.MustCompile(`(?i)^\s*([0-9]+)\s*(?:/|\s*per\s*)\s*([0-9]+)?\s*([a-z]+?)s?\s*$`)

// parseContactLimits accepts the same strings Flask's config does, for example
// "3 per 10 minutes; 10 per day". Anything else is an error: a typo must not
// silently remove a quota.
func parseContactLimits(value string) ([]contactLimit, error) {
	var limits []contactLimit
	for _, part := range regexp.MustCompile(`[,;|]`).Split(value, -1) {
		match := contactLimitPart.FindStringSubmatch(part)
		if match == nil {
			return nil, errors.New("invalid contact rate limit")
		}
		seconds, ok := contactGranularities[strings.ToLower(match[3])]
		if !ok {
			return nil, errors.New("invalid contact rate limit")
		}
		amount, _ := strconv.ParseInt(match[1], 10, 64)
		multiples := int64(1)
		if match[2] != "" {
			multiples, _ = strconv.ParseInt(match[2], 10, 64)
		}
		if amount < 1 || multiples < 1 || multiples > 1000 || amount > 1_000_000 {
			return nil, errors.New("invalid contact rate limit")
		}
		limits = append(limits, contactLimit{Amount: amount, Multiples: multiples, Granularity: strings.ToLower(match[3]), Seconds: seconds})
	}
	return limits, nil
}

type contactConfig struct {
	Enabled   bool
	MaxBody   int64
	PerIP     []contactLimit
	Global    []contactLimit
	LimiterDB string
	Analytics bool
	Auth      authBridgeConfig
}

// contactConfigFromEnv is off unless CONTACT_GO_ENABLED=true. It also needs the
// auth bridge: the one-time token lives in Flask's signed session, which Go
// never reads itself.
func contactConfigFromEnv(auth authBridgeConfig) (contactConfig, error) {
	c := contactConfig{Enabled: os.Getenv("CONTACT_GO_ENABLED") == "true", Auth: auth, MaxBody: contactDefaultBody,
		LimiterDB: env("CONTACT_LIMITER_DATABASE", "limits"), Analytics: analyticsEnabledFromEnv()}
	if !c.Enabled {
		return c, nil
	}
	if !auth.Enabled {
		return c, errors.New("contact needs the auth bridge")
	}
	var err error
	if c.PerIP, err = parseContactLimits(env("CONTACT_PER_IP_LIMIT", "3 per 10 minutes; 10 per day")); err != nil {
		return c, err
	}
	if c.Global, err = parseContactLimits(env("CONTACT_GLOBAL_LIMIT", "25 per 10 minutes; 100 per day")); err != nil {
		return c, err
	}
	if raw := os.Getenv("CONTACT_MAX_REQUEST_BYTES"); raw != "" {
		c.MaxBody, err = strconv.ParseInt(raw, 10, 64)
		if err != nil || c.MaxBody < 1 || c.MaxBody > 1<<20 {
			return c, errors.New("invalid contact body limit")
		}
	}
	if c.LimiterDB == "" || strings.ContainsAny(c.LimiterDB, "/\\. \"$\x00") {
		return c, errors.New("invalid contact limiter database")
	}
	return c, nil
}

// contactAnswer is Flask's read-only answer from /api/session (header
// X-Contact-Form-Token). Flask owns the session and its configuration.
type contactAnswer struct {
	Valid, Enabled         bool
	MaxAge                 int64
	NameMax, EmailMax      int
	MessageMin, MessageMax int
}

// contactAnswer asks Flask whether the token is outstanding in the caller's
// session. It changes nothing in the session and never sets a cookie. Any error
// means "unknown": the caller must not accept the form.
func (r *sessionResolver) contactAnswer(ctx context.Context, request *http.Request, token string) (contactAnswer, error) {
	call, err := http.NewRequestWithContext(ctx, http.MethodGet, "http://"+r.host+"/api/session", nil)
	if err != nil {
		return contactAnswer{}, errAuthUnavailable
	}
	cookie := request.Header.Get("Cookie")
	if len(cookie) > authBridgeMaxCookieBytes {
		return contactAnswer{}, errAuthUnavailable
	}
	if cookie != "" {
		call.Header.Set("Cookie", cookie)
	}
	call.Header.Set("Accept", "application/json")
	call.Header.Set("X-Forwarded-Proto", r.scheme)
	call.Header.Set("X-Contact-Form-Token", token)
	if agent := request.Header.Get("User-Agent"); agent != "" && len(agent) <= 512 {
		call.Header.Set("User-Agent", agent)
	}
	if address := submissionClientAddress(request); address != "" {
		if _, err := netip.ParseAddr(address); err == nil {
			call.Header.Set("X-Forwarded-For", address)
		}
	}
	response, err := r.client.Do(call)
	if err != nil {
		return contactAnswer{}, errAuthUnavailable
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return contactAnswer{}, errAuthUnavailable
	}
	var raw struct {
		ContactToken *struct {
			Valid   *bool `json:"valid"`
			Enabled *bool `json:"enabled"`
			MaxAge  int64 `json:"max_age_seconds"`
			Limits  struct {
				NameMax    int `json:"name_max"`
				EmailMax   int `json:"email_max"`
				MessageMin int `json:"message_min"`
				MessageMax int `json:"message_max"`
			} `json:"limits"`
		} `json:"contact_token"`
	}
	if err := json.NewDecoder(io.LimitReader(response.Body, 64<<10)).Decode(&raw); err != nil || raw.ContactToken == nil ||
		raw.ContactToken.Valid == nil || raw.ContactToken.Enabled == nil {
		return contactAnswer{}, errAuthUnavailable
	}
	t := raw.ContactToken
	answer := contactAnswer{Valid: *t.Valid, Enabled: *t.Enabled, MaxAge: t.MaxAge,
		NameMax: t.Limits.NameMax, EmailMax: t.Limits.EmailMax, MessageMin: t.Limits.MessageMin, MessageMax: t.Limits.MessageMax}
	// Fail closed on a nonsense configuration rather than guess limits.
	if answer.MaxAge < 1 || answer.MaxAge > 7*86400 || answer.NameMax < 1 || answer.EmailMax < 1 ||
		answer.MessageMin < 0 || answer.MessageMax < 1 || answer.MessageMin > answer.MessageMax {
		return contactAnswer{}, errAuthUnavailable
	}
	return answer, nil
}

// contactStore is everything the handler writes or reads in MongoDB.
type contactStore interface {
	// Hit increments a Flask-Limiter counter and returns its new value.
	Hit(ctx context.Context, key string, window time.Duration) (int64, error)
	// ConsumeToken inserts the token hash; false means it was already consumed.
	ConsumeToken(ctx context.Context, hash string, expires time.Time) (bool, error)
	// Enqueue inserts one `email_queue` job and returns only once it is acknowledged.
	Enqueue(ctx context.Context, job bson.D) error
	// Count adds one to an aggregate site_analytics counter.
	Count(ctx context.Context, key bson.M) error
}

type mongoContactStore struct{ db, limiter *mongo.Database }

func (s mongoContactStore) Hit(ctx context.Context, key string, window time.Duration) (int64, error) {
	expiration := time.Now().UTC().Add(window)
	// The same aggregation-pipeline update the limits library runs: reset an
	// expired window, otherwise count up. A missing document sorts before
	// $$NOW, so an upsert starts a fresh window.
	expired := bson.M{"$lt": bson.A{"$expireAt", "$$NOW"}}
	update := mongo.Pipeline{{{Key: "$set", Value: bson.D{
		{Key: "count", Value: bson.M{"$cond": bson.M{"if": expired, "then": 1, "else": bson.M{"$add": bson.A{"$count", 1}}}}},
		{Key: "expireAt", Value: bson.M{"$cond": bson.M{"if": expired, "then": expiration, "else": "$expireAt"}}},
	}}}}
	var counter struct {
		Count int64 `bson:"count"`
	}
	err := s.limiter.Collection("counters").FindOneAndUpdate(ctx, bson.M{"_id": key}, update,
		options.FindOneAndUpdate().SetUpsert(true).SetProjection(bson.M{"count": 1}).SetReturnDocument(options.After)).Decode(&counter)
	return counter.Count, err
}

func (s mongoContactStore) ConsumeToken(ctx context.Context, hash string, expires time.Time) (bool, error) {
	_, err := s.db.Collection("contact_form_tokens").InsertOne(ctx, bson.D{{Key: "_id", Value: hash}, {Key: "expires_at", Value: expires}})
	if mongo.IsDuplicateKeyError(err) {
		return false, nil
	}
	return err == nil, err
}

func (s mongoContactStore) Enqueue(ctx context.Context, job bson.D) error {
	result, err := s.db.Collection("email_queue").InsertOne(ctx, job)
	if err == nil && !result.Acknowledged {
		return errors.New("email queue insert was not acknowledged")
	}
	return err
}

func (s mongoContactStore) Count(ctx context.Context, key bson.M) error {
	_, err := s.db.Collection("site_analytics").UpdateOne(ctx, key, bson.M{"$inc": bson.M{"count": 1}}, options.UpdateOne().SetUpsert(true))
	return err
}

// contactMailTemplate is emails/customer_support/new_ticket.html with Jinja's
// autoescape written as esc. html.EscapeString produces the same entities as
// markupsafe (&amp; &lt; &gt; &#34; &#39;). Golden fixtures in testdata pin the
// output byte for byte; scripts/generate_contact_mail_fixtures.py makes them.
var contactMailTemplate = template.Must(template.New("mail").Funcs(template.FuncMap{"esc": html.EscapeString}).Parse(
	`<!DOCTYPE html>
<html lang="fi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Uusi tukipyyntö</title>
    <style>
        body {
            background: #f4f4f4;
            color: #333333;
            margin: 0;
            padding: 0;
        }
        .container {
            width: 100%;
            max-width: 600px;
            padding: 20px;
            background: #ffffff;
            margin: 0 auto;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
        }
        .header {
            text-align: center;
            padding: 10px 0;
            background: #0033A0;
            color: #ffffff;
            border-radius: 8px 8px 0 0;
        }
        .content {
            margin: 20px;
        }
        .btn {
            display: inline-block;
            padding: 10px 20px;
            background: #0033A0;
            color: #ffffff;
            text-decoration: none;
            border-radius: 5px;
            text-align: center;
        }
        .btn:hover {
            background: #002a6d;
        }
        .footer {
            margin-top: 20px;
            text-align: center;
            font-size: 0.9em;
            color: #888888;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Uusi yhteydenotto</h1>
        </div>
        <div class="content">
            <p><strong>Viestin lähettäjä:</strong> {{esc .Name}} &lt;{{esc .Email}}&gt;</p>
            <p><strong>Aihe:</strong> {{esc .Subject}}</p>
            <p><strong>Viesti:</strong></p>
            <p>{{esc .Message}}</p>
        </div>
        <div class="footer">
            <p>Tämä on automaattinen viesti, älä vastaa tähän sähköpostiin.</p>
            <p>Pyyntötunnus: {{esc .RequestID}}</p>
        </div>
    </div>
</body>
</html>`))

type contactMail struct{ Name, Email, Subject, Message, RequestID string }

func renderContactMail(m contactMail) (string, error) {
	var out bytes.Buffer
	err := contactMailTemplate.Execute(&out, m)
	return out.String(), err
}

// contactJob has the shape of EmailJob.to_dict(): the worker reads these fields
// and claims jobs by status, regardless of the producer.
func contactJob(body, requestID, instanceID string) bson.D {
	return bson.D{
		{Key: "subject", Value: contactMailSubject},
		{Key: "recipients", Value: bson.A{contactSupportAddress}},
		{Key: "body", Value: body},
		{Key: "html", Value: body},
		{Key: "sender", Value: nil},
		{Key: "attachments", Value: bson.A{}},
		{Key: "extra_headers", Value: bson.D{{Key: "X-MF-Request-ID", Value: requestID}}},
		{Key: "instance_id", Value: instanceID},
		{Key: "status", Value: "pending"},
		{Key: "attempts", Value: int32(0)},
	}
}

// pyText mirrors how Flask reads a form value: invalid UTF-8 bytes become
// U+FFFD one by one, then str.strip() (analyticsTrim has the same whitespace set).
func pyText(value string) string {
	if !utf8.ValidString(value) {
		var out strings.Builder
		for len(value) > 0 {
			r, size := utf8.DecodeRuneInString(value)
			if r == utf8.RuneError && size == 1 {
				out.WriteRune(utf8.RuneError)
			} else {
				out.WriteString(value[:size])
			}
			value = value[size:]
		}
		value = out.String()
	}
	return value
}

func contactCasefold(value string) string { return cases.Fold().String(value) }

func contactRequestID() string {
	buffer := make([]byte, 16)
	if _, err := rand.Read(buffer); err != nil {
		panic("random source unavailable")
	}
	return hex.EncodeToString(buffer)
}

// contactUUID is a version 4 UUID, the producer id Flask's mailer also uses.
func contactUUID() string {
	buffer := make([]byte, 16)
	if _, err := rand.Read(buffer); err != nil {
		panic("random source unavailable")
	}
	buffer[6], buffer[8] = buffer[6]&0x0f|0x40, buffer[8]&0x3f|0x80
	h := hex.EncodeToString(buffer)
	return h[0:8] + "-" + h[8:12] + "-" + h[12:16] + "-" + h[16:20] + "-" + h[20:]
}

// contactClientKey is Flask's get_client_ip as a limiter identifier.
func contactClientKey(r *http.Request) string {
	if address, err := netip.ParseAddr(submissionClientAddress(r)); err == nil {
		return address.String()
	}
	return "0.0.0.0"
}

var contactReferrerRules = [][2]string{{"google", "google"}, {"facebook", "facebook"}, {"instagram", "instagram"}, {"tiktok", "tiktok"},
	{"x.com", "x-twitter"}, {"twitter.com", "x-twitter"}, {"t.co", "x-twitter"}, {"linkedin", "linkedin"}, {"bing", "bing"},
	{"duckduckgo", "duckduckgo"}, {"youtube", "youtube"}, {"reddit", "reddit"}, {"bluesky", "bluesky"}}

// contactReferrer is classify_referrer from utils/site_analytics.py.
func contactReferrer(referrer, currentHost string) string {
	referrer = strings.TrimSpace(referrer)
	if referrer == "" {
		return "direct"
	}
	parsed, err := url.Parse(referrer)
	if err != nil {
		return "other"
	}
	host := strings.ToLower(parsed.Hostname())
	if host == "" {
		return "direct"
	}
	current := strings.Split(currentHost, ":")[0]
	current = strings.ToLower(current[strings.LastIndex(current, "@")+1:])
	if host == current {
		return "internal"
	}
	host = strings.TrimPrefix(host, "www.")
	for _, rule := range contactReferrerRules {
		if strings.Contains(host, rule[0]) {
			return rule[1]
		}
	}
	return "other"
}

// contactEventKey is record_event_for_request("contact_message"): nil for bots
// and Do-Not-Track, with the request's device and referrer buckets. The public
// site is Finnish-only today, so the language bucket is "fi" as for the beacons.
func contactEventKey(r *http.Request, now time.Time) bson.M {
	if r.Header.Get("DNT") == "1" {
		return nil
	}
	agent := strings.TrimSpace(r.Header.Get("User-Agent"))
	device := "other"
	switch {
	case agent == "":
	case analyticsBots.MatchString(agent):
		return nil
	case analyticsMobile.MatchString(agent):
		device = "mobile"
	case analyticsTablet.MatchString(agent):
		device = "tablet"
	case analyticsDesktop.MatchString(agent):
		device = "desktop"
	}
	host, _ := analyticsRequestHost(r)
	local := now.In(analyticsLocation)
	return bson.M{"date": local.Format("2006-01-02"), "hour": local.Hour(), "event": "contact_message", "language": "fi",
		"device": device, "referrer": contactReferrer(r.Header.Get("Referer"), host)}
}

// contactForm reads request.form: urlencoded or multipart, first value wins,
// query parameters are not part of it.
func contactForm(r *http.Request, body []byte) (map[string]string, error) {
	mediaType, params, _ := mime.ParseMediaType(r.Header.Get("Content-Type"))
	values := map[string]string{}
	switch mediaType {
	case "application/x-www-form-urlencoded":
		parsed, err := url.ParseQuery(string(body))
		if err != nil {
			return nil, err
		}
		for key, list := range parsed {
			if len(list) > 0 {
				values[key] = list[0]
			}
		}
	case "multipart/form-data":
		if params["boundary"] == "" {
			return nil, errors.New("missing boundary")
		}
		r.Body = io.NopCloser(bytes.NewReader(body))
		if err := r.ParseMultipartForm(int64(len(body)) + 1); err != nil {
			return nil, err
		}
		defer r.MultipartForm.RemoveAll()
		for key, list := range r.MultipartForm.Value {
			if len(list) > 0 {
				values[key] = list[0]
			}
		}
	}
	return values, nil
}

// contactDecision is what the form's fields amount to.
type contactDecision int

const (
	contactAccepted contactDecision = iota
	contactHoneypot
	contactInvalid
)

// contactCheck applies the field rules in Flask's order: honeypot first (it
// gets a fake success), then normalization and validation.
func contactCheck(form map[string]string, limits contactAnswer) (contactDecision, contactMail) {
	if analyticsTrim(pyText(form["website"])) != "" {
		return contactHoneypot, contactMail{}
	}
	mail := contactMail{
		Name:    analyticsTrim(pyText(form["name"])),
		Email:   contactCasefold(analyticsTrim(pyText(form["email"]))),
		Subject: analyticsTrim(pyText(form["subject"])),
		Message: analyticsTrim(pyText(form["message"])),
	}
	messageLength := utf8.RuneCountInString(mail.Message)
	valid := mail.Name != "" && mail.Email != "" && mail.Message != "" &&
		utf8.RuneCountInString(mail.Name) <= limits.NameMax &&
		utf8.RuneCountInString(mail.Email) <= limits.EmailMax &&
		contactEmailPattern.MatchString(mail.Email) &&
		contactSubjects[mail.Subject] &&
		limits.MessageMin <= messageLength && messageLength <= limits.MessageMax
	if !valid {
		return contactInvalid, contactMail{}
	}
	return contactAccepted, mail
}

// contactRedirect answers like Flask's _contact_redirect. Flask stores a flash
// message in its session; Go cannot write that session, so the outcome travels
// in the query string for the React page to show (docs/go-contact.md).
func contactRedirect(w http.ResponseWriter, outcome, requestID string) {
	w.Header().Set("X-MF-Request-ID", requestID)
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Location", contactPath+"?contact="+outcome)
	w.WriteHeader(http.StatusFound)
}

type contactHandlerDeps struct {
	store    contactStore
	answer   func(ctx context.Context, r *http.Request, token string) (contactAnswer, error)
	now      func() time.Time
	requests func() string
	instance string
}

func contactHandler(next http.Handler, config contactConfig, deps contactHandlerDeps) http.Handler {
	inflight := make(chan struct{}, contactInflight)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != contactPath {
			next.ServeHTTP(w, r)
			return
		}
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		if r.Method != http.MethodPost {
			w.Header().Set("Allow", "POST")
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		select {
		case inflight <- struct{}{}:
			defer func() { <-inflight }()
		default:
			w.Header().Set("Retry-After", "5")
			writeError(w, http.StatusServiceUnavailable, "service busy")
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 8*time.Second)
		defer cancel()
		// The outer public gate already refuses every request while a global or
		// security panic record is active, as Flask's before_request hook does.

		// Quotas first, as Flask-Limiter runs before the view. Same order: shorter
		// windows first, global before per-address within one window size, and
		// the first breach stops the check.
		type counter struct {
			key   string
			limit contactLimit
		}
		var counters []counter
		for _, limit := range config.Global {
			counters = append(counters, counter{"LIMITER/global/public-contact-ingress/" + limit.suffix(), limit})
		}
		client := contactClientKey(r)
		for _, limit := range config.PerIP {
			counters = append(counters, counter{"LIMITER/" + client + "/contact/" + limit.suffix(), limit})
		}
		sort.SliceStable(counters, func(i, j int) bool { return counters[i].limit.Seconds < counters[j].limit.Seconds })
		for _, c := range counters {
			count, err := deps.store.Hit(ctx, c.key, c.limit.window())
			if err != nil {
				log.Print("Contact rate limit storage unavailable")
				writeError(w, http.StatusServiceUnavailable, "service unavailable")
				return
			}
			if count > c.limit.Amount {
				writeError(w, http.StatusTooManyRequests, "too many requests")
				return
			}
		}

		requestID := deps.requests()
		declared := r.ContentLength
		if declared > config.MaxBody {
			log.Printf("Rejected oversized contact form request_id=%s", requestID)
			writeError(w, http.StatusRequestEntityTooLarge, "request too large")
			return
		}
		body, err := io.ReadAll(io.LimitReader(r.Body, config.MaxBody+1))
		if err != nil || int64(len(body)) > config.MaxBody || (declared <= 0 && int64(len(body)) >= config.MaxBody) {
			log.Printf("Rejected oversized contact form request_id=%s", requestID)
			writeError(w, http.StatusRequestEntityTooLarge, "request too large")
			return
		}
		form, err := contactForm(r, body)
		if err != nil {
			writeError(w, http.StatusBadRequest, "invalid form")
			return
		}

		token := form["csrf_token"]
		header := ""
		if contactToken.MatchString(token) {
			header = token
		}
		answer, err := deps.answer(ctx, r, header)
		if err != nil {
			log.Print("Contact session check unavailable")
			writeError(w, http.StatusServiceUnavailable, "session unavailable")
			return
		}
		if !answer.Enabled {
			log.Printf("Contact form disabled request_id=%s", requestID)
			contactRedirect(w, "disabled", requestID)
			return
		}
		valid := header != "" && answer.Valid
		if valid {
			sum := sha256.Sum256([]byte(token))
			valid, err = deps.store.ConsumeToken(ctx, hex.EncodeToString(sum[:]), deps.now().UTC().Add(time.Duration(answer.MaxAge)*time.Second))
			if err != nil {
				log.Print("Contact token storage unavailable")
				writeError(w, http.StatusServiceUnavailable, "service unavailable")
				return
			}
		}
		if !valid {
			log.Printf("Rejected contact form token request_id=%s", requestID)
			writeError(w, http.StatusBadRequest, "invalid form token")
			return
		}

		decision, mail := contactCheck(form, answer)
		switch decision {
		case contactHoneypot:
			log.Printf("Suppressed contact form honeypot request_id=%s", requestID)
			contactRedirect(w, "sent", requestID)
			return
		case contactInvalid:
			log.Printf("Rejected invalid contact form request_id=%s", requestID)
			contactRedirect(w, "invalid", requestID)
			return
		}
		mail.RequestID = requestID
		rendered, err := renderContactMail(mail)
		if err == nil {
			err = deps.store.Enqueue(ctx, contactJob(rendered, requestID, deps.instance))
		}
		if err != nil {
			// The token stays consumed, so the visitor reloads the form for a new one.
			log.Printf("Contact mail could not be queued request_id=%s (%s)", requestID, dbErrorKind(err))
			writeError(w, http.StatusServiceUnavailable, "message could not be queued")
			return
		}
		log.Printf("Accepted contact form request_id=%s", requestID)
		if config.Analytics {
			if key := contactEventKey(r, deps.now()); key != nil {
				countContext, stop := context.WithTimeout(r.Context(), time.Second)
				if deps.store.Count(countContext, key) != nil {
					log.Print("Contact analytics counter unavailable")
				}
				stop()
			}
		}
		contactRedirect(w, "sent", requestID)
	})
}

// contactHandlerFromEnv wires the route in main. A missing or invalid setting
// leaves the route off; it never stops the public API from starting.
func contactHandlerFromEnv(next http.Handler, db *mongo.Database, auth authBridgeConfig) http.Handler {
	config, err := contactConfigFromEnv(auth)
	if err != nil {
		log.Print("Contact route disabled: ", err)
		return next
	}
	if !config.Enabled {
		return next
	}
	resolver, err := newSessionResolver(auth)
	if err != nil {
		log.Print("Contact route disabled: invalid session bridge")
		return next
	}
	return contactHandler(next, config, contactHandlerDeps{
		store:    mongoContactStore{db: db, limiter: db.Client().Database(config.LimiterDB)},
		answer:   resolver.contactAnswer,
		now:      time.Now,
		requests: contactRequestID,
		instance: contactUUID(),
	})
}
