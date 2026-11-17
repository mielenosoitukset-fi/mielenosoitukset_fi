package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"regexp"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

// Tests for the dark Go POST /contact (docs/go-contact.md). No test sends mail:
// the store is a fake, or a disposable MongoDB that only receives queue rows.

const contactTestToken = "tok_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-1"

type fakeContactStore struct {
	mu       sync.Mutex
	hits     []string
	counters map[string]int64
	tokens   map[string]time.Time
	jobs     []bson.D
	events   []bson.M
	hitErr   error
	tokenErr error
	queueErr error
}

func newFakeContactStore() *fakeContactStore {
	return &fakeContactStore{counters: map[string]int64{}, tokens: map[string]time.Time{}}
}

func (s *fakeContactStore) Hit(_ context.Context, key string, _ time.Duration) (int64, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.hitErr != nil {
		return 0, s.hitErr
	}
	s.hits = append(s.hits, key)
	s.counters[key]++
	return s.counters[key], nil
}

func (s *fakeContactStore) ConsumeToken(_ context.Context, hash string, expires time.Time) (bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.tokenErr != nil {
		return false, s.tokenErr
	}
	if _, used := s.tokens[hash]; used {
		return false, nil
	}
	s.tokens[hash] = expires
	return true, nil
}

func (s *fakeContactStore) Enqueue(_ context.Context, job bson.D) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.queueErr != nil {
		return s.queueErr
	}
	s.jobs = append(s.jobs, job)
	return nil
}

func (s *fakeContactStore) Count(_ context.Context, key bson.M) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.events = append(s.events, key)
	return nil
}

func testContactConfig(t *testing.T) contactConfig {
	t.Helper()
	t.Setenv("CONTACT_GO_ENABLED", "true")
	t.Setenv("SITE_ANALYTICS_ENABLED", "true")
	config, err := contactConfigFromEnv(authBridgeConfig{Enabled: true, Origin: "https://example.test", Socket: "/x"})
	if err != nil || !config.Enabled {
		t.Fatalf("config: %v %+v", err, config)
	}
	return config
}

var defaultAnswer = contactAnswer{Valid: true, Enabled: true, MaxAge: 7200, NameMax: 120, EmailMax: 254, MessageMin: 10, MessageMax: 5000}

type contactRig struct {
	handler http.Handler
	store   *fakeContactStore
	answer  *contactAnswer
	asked   *[]string
	nextHit *int32
}

func newContactRig(t *testing.T) *contactRig {
	t.Helper()
	config := testContactConfig(t)
	store := newFakeContactStore()
	answer := defaultAnswer
	asked := []string{}
	var nextHit int32
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { atomic.AddInt32(&nextHit, 1); w.WriteHeader(299) })
	handler := contactHandler(next, config, contactHandlerDeps{
		store: store,
		answer: func(_ context.Context, _ *http.Request, token string) (contactAnswer, error) {
			asked = append(asked, token)
			return answer, nil
		},
		now:      func() time.Time { return time.Date(2026, 11, 17, 12, 30, 0, 0, time.UTC) },
		requests: func() string { return "0123456789abcdef0123456789abcdef" },
		instance: "11111111-2222-4333-8444-555555555555",
	})
	return &contactRig{handler: handler, store: store, answer: &answer, asked: &asked, nextHit: &nextHit}
}

func validForm() url.Values {
	return url.Values{"csrf_token": {contactTestToken}, "website": {""}, "name": {"Testi Käyttäjä"},
		"email": {"VISITOR@Example.Test "}, "subject": {"Tuki"}, "message": {"This is a valid support request."}}
}

func (rig *contactRig) post(form url.Values, headers ...string) *httptest.ResponseRecorder {
	request := httptest.NewRequest("POST", "/contact", strings.NewReader(form.Encode()))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	request.Header.Set("Cookie", "session=x")
	request.Header.Set("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) Safari/605.1.15")
	request.Header.Set("Referer", "https://example.test/contact")
	request.Host = "example.test"
	request.RemoteAddr = "203.0.113.9:4444"
	for i := 0; i+1 < len(headers); i += 2 {
		request.Header.Set(headers[i], headers[i+1])
	}
	recorder := httptest.NewRecorder()
	rig.handler.ServeHTTP(recorder, request)
	return recorder
}

func contactTokenHash() string {
	sum := sha256.Sum256([]byte(contactTestToken))
	return hex.EncodeToString(sum[:])
}

func TestContactMailMatchesFlaskJinjaByteForByte(t *testing.T) {
	var cases map[string]contactMail
	raw, err := os.ReadFile("testdata/contact_mail_cases.json")
	if err != nil {
		t.Fatal(err)
	}
	var generic map[string]map[string]string
	if err := json.Unmarshal(raw, &generic); err != nil {
		t.Fatal(err)
	}
	cases = map[string]contactMail{}
	for name, c := range generic {
		cases[name] = contactMail{Name: c["name"], Email: c["email"], Subject: c["subject"], Message: c["message"], RequestID: c["request_id"]}
	}
	if len(cases) < 2 {
		t.Fatal("fixtures missing")
	}
	for name, mail := range cases {
		want, err := os.ReadFile("testdata/contact_mail_" + name + ".html")
		if err != nil {
			t.Fatal(err)
		}
		got, err := renderContactMail(mail)
		if err != nil {
			t.Fatal(err)
		}
		if got != string(want) {
			t.Errorf("%s: rendered mail differs from the Jinja golden fixture", name)
		}
	}
}

func TestContactJobHasTheEmailQueueShape(t *testing.T) {
	job := contactJob("<p>x</p>", "rid", "inst")
	raw, err := bson.Marshal(job)
	if err != nil {
		t.Fatal(err)
	}
	var doc bson.M
	if err := bson.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	if doc["subject"] != "Uusi tukipyyntö!" || doc["status"] != "pending" || doc["attempts"] != int32(0) || doc["sender"] != nil ||
		doc["body"] != "<p>x</p>" || doc["html"] != "<p>x</p>" || doc["instance_id"] != "inst" {
		t.Fatalf("unexpected job %v", doc)
	}
	if recipients := doc["recipients"].(bson.A); len(recipients) != 1 || recipients[0] != "tuki@mielenosoitukset.fi" {
		t.Fatalf("recipients %v", recipients)
	}
	if len(doc["attachments"].(bson.A)) != 0 || doc["extra_headers"].(bson.D)[0].Value != "rid" {
		t.Fatalf("attachments/headers %v", doc)
	}
	if len(doc) != 10 {
		t.Fatalf("job has %d fields, EmailJob.to_dict has 10", len(doc))
	}
}

func TestContactAcceptsAValidFormAndQueuesTheSupportMail(t *testing.T) {
	rig := newContactRig(t)
	response := rig.post(validForm())
	if response.Code != 302 || response.Header().Get("Location") != "/contact?contact=sent" {
		t.Fatalf("got %d %q", response.Code, response.Header().Get("Location"))
	}
	if !regexp.MustCompile(`^[a-f0-9]{32}$`).MatchString(response.Header().Get("X-MF-Request-ID")) || response.Header().Get("Cache-Control") != "no-store" {
		t.Fatalf("headers %v", response.Header())
	}
	if len(rig.store.jobs) != 1 {
		t.Fatalf("expected exactly one queued mail, got %d", len(rig.store.jobs))
	}
	body := rig.store.jobs[0][2].Value.(string)
	if !strings.Contains(body, "visitor@example.test") || !strings.Contains(body, "Pyyntötunnus: 0123456789abcdef0123456789abcdef") {
		t.Fatalf("mail missing normalized email or request id")
	}
	expires, used := rig.store.tokens[contactTokenHash()]
	if !used || !expires.Equal(time.Date(2026, 11, 17, 14, 30, 0, 0, time.UTC)) {
		t.Fatalf("token hash not stored with consumption time + max age: %v %v", used, expires)
	}
	if got := *rig.asked; len(got) != 1 || got[0] != contactTestToken {
		t.Fatalf("Flask was asked %v", got)
	}
	if len(rig.store.events) != 1 || rig.store.events[0]["event"] != "contact_message" || rig.store.events[0]["device"] != "desktop" ||
		rig.store.events[0]["referrer"] != "internal" || rig.store.events[0]["language"] != "fi" ||
		rig.store.events[0]["date"] != "2026-11-17" || rig.store.events[0]["hour"] != 14 {
		t.Fatalf("event %v", rig.store.events)
	}
}

func TestContactReusedTokenIsRejectedAndSendsNothing(t *testing.T) {
	rig := newContactRig(t)
	if rig.post(validForm()).Code != 302 {
		t.Fatal("first use must be accepted")
	}
	replay := rig.post(validForm())
	if replay.Code != 400 || len(rig.store.jobs) != 1 {
		t.Fatalf("replay got %d with %d mails", replay.Code, len(rig.store.jobs))
	}
}

func TestContactTokenIsConsumedExactlyOnceUnderConcurrency(t *testing.T) {
	rig := newContactRig(t)
	var wg sync.WaitGroup
	var accepted int32
	for i := 0; i < 6; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			// A fresh IP per request keeps the quota out of the way.
			if rig.post(validForm(), "X-Forwarded-For", "198.51.100.1").Code == 302 {
				atomic.AddInt32(&accepted, 1)
			}
		}()
	}
	wg.Wait()
	if accepted != 1 || len(rig.store.jobs) != 1 {
		t.Fatalf("accepted %d, mails %d", accepted, len(rig.store.jobs))
	}
}

func TestContactTokenNotInFlasksSessionIsRejectedWithoutConsuming(t *testing.T) {
	rig := newContactRig(t)
	rig.answer.Valid = false
	if got := rig.post(validForm()).Code; got != 400 {
		t.Fatalf("got %d", got)
	}
	if len(rig.store.tokens) != 0 || len(rig.store.jobs) != 0 {
		t.Fatal("an invalid token must not be stored or accepted")
	}
	// A token that cannot be one of ours never reaches Flask's header.
	form := validForm()
	form.Set("csrf_token", "bad token\r\nX-Evil: 1")
	rig.answer.Valid = true
	if got := rig.post(form).Code; got != 400 {
		t.Fatalf("got %d", got)
	}
	if last := (*rig.asked)[len(*rig.asked)-1]; last != "" {
		t.Fatalf("unsafe token forwarded to Flask: %q", last)
	}
	form.Del("csrf_token")
	if got := rig.post(form).Code; got != 400 {
		t.Fatalf("missing token got %d", got)
	}
}

func TestContactHoneypotGetsFakeSuccessWithoutMailOrEvent(t *testing.T) {
	rig := newContactRig(t)
	form := validForm()
	form.Set("website", "  https://spam.example  ")
	response := rig.post(form)
	if response.Code != 302 || response.Header().Get("Location") != "/contact?contact=sent" {
		t.Fatalf("got %d %q", response.Code, response.Header().Get("Location"))
	}
	if len(rig.store.jobs) != 0 || len(rig.store.events) != 0 {
		t.Fatal("honeypot must queue nothing and count nothing")
	}
	if len(rig.store.tokens) != 1 {
		t.Fatal("the token is consumed before the honeypot is looked at, as in Flask")
	}
	// A whitespace-only honeypot is blank.
	form.Set("website", " \t ")
	form.Set("csrf_token", contactTestToken+"2")
	if rig.post(form).Header().Get("Location") != "/contact?contact=sent" || len(rig.store.jobs) != 1 {
		t.Fatal("blank honeypot must be treated as a real submission")
	}
}

func TestContactValidationMatchesFlask(t *testing.T) {
	long := func(n int) string { return strings.Repeat("ä", n) }
	cases := []struct {
		name   string
		change func(url.Values)
		ok     bool
	}{
		{"valid", func(url.Values) {}, true},
		{"blank name", func(v url.Values) { v.Set("name", "   ") }, false},
		{"name at limit counts characters not bytes", func(v url.Values) { v.Set("name", long(120)) }, true},
		{"name over limit", func(v url.Values) { v.Set("name", long(121)) }, false},
		{"email without domain dot", func(v url.Values) { v.Set("email", "visitor@example") }, false},
		{"email with unicode domain", func(v url.Values) { v.Set("email", "visitor@exämple.test") }, false},
		{"email too long", func(v url.Values) { v.Set("email", strings.Repeat("a", 250)+"@b.co") }, false},
		{"email plus sign", func(v url.Values) { v.Set("email", "a+b@example.test") }, true},
		{"unknown subject", func(v url.Values) { v.Set("subject", "tuki") }, false},
		{"subject Yleinen kysymys", func(v url.Values) { v.Set("subject", "Yleinen kysymys") }, true},
		{"subject Palautetta", func(v url.Values) { v.Set("subject", "Palautetta") }, true},
		{"subject Muu", func(v url.Values) { v.Set("subject", "Muu") }, true},
		{"message 9 characters", func(v url.Values) { v.Set("message", "123456789") }, false},
		{"message 10 characters", func(v url.Values) { v.Set("message", "1234567890") }, true},
		{"message 10 after trimming", func(v url.Values) { v.Set("message", "  123456789  ") }, false},
		{"message 5000 characters", func(v url.Values) { v.Set("message", strings.Repeat("o", 5000)) }, true},
		{"message 5001 characters", func(v url.Values) { v.Set("message", strings.Repeat("o", 5001)) }, false},
		{"missing field", func(v url.Values) { v.Del("message") }, false},
	}
	for _, c := range cases {
		rig := newContactRig(t)
		form := validForm()
		c.change(form)
		response := rig.post(form)
		want, queued := "/contact?contact=invalid", 0
		if c.ok {
			want, queued = "/contact?contact=sent", 1
		}
		if response.Code != 302 || response.Header().Get("Location") != want || len(rig.store.jobs) != queued {
			t.Errorf("%s: got %d %q with %d mails", c.name, response.Code, response.Header().Get("Location"), len(rig.store.jobs))
		}
		if response.Header().Get("X-MF-Request-ID") == "" {
			t.Errorf("%s: correlation id missing", c.name)
		}
		if len(rig.store.tokens) != 1 {
			t.Errorf("%s: the token is consumed before validation, as in Flask", c.name)
		}
	}
}

func TestContactUsesFlaskConfiguredLimits(t *testing.T) {
	rig := newContactRig(t)
	rig.answer.MessageMin, rig.answer.NameMax = 3, 4
	form := validForm()
	form.Set("message", "abc")
	form.Set("name", "Matti")
	if rig.post(form).Header().Get("Location") != "/contact?contact=invalid" {
		t.Fatal("name longer than the configured limit must be refused")
	}
	form.Set("name", "Matt")
	form.Set("csrf_token", contactTestToken+"b")
	if rig.post(form).Header().Get("Location") != "/contact?contact=sent" {
		t.Fatal("a short message allowed by the configured minimum must be accepted")
	}
}

func TestContactEmailIsCasefoldedLikePython(t *testing.T) {
	for in, want := range map[string]string{
		"VISITOR@Example.Test": "visitor@example.test",
		"a@K.test":             "a@k.test", // Kelvin sign folds to ASCII k
		"a@STRAßE.test":        "a@strasse.test",
		"a@ſ.test":             "a@s.test", // long s
	} {
		if got := contactCasefold(in); got != want {
			t.Errorf("casefold(%q) = %q, want %q", in, got, want)
		}
	}
	rig := newContactRig(t)
	form := validForm()
	form.Set("email", "visitor@K.test")
	rig.post(form)
	if len(rig.store.jobs) != 1 || !strings.Contains(rig.store.jobs[0][2].Value.(string), "visitor@k.test") {
		t.Fatal("a Kelvin-sign domain folds to ASCII and is accepted, as in Python")
	}
}

func TestContactDisabledFormRedirectsBeforeTokenWork(t *testing.T) {
	rig := newContactRig(t)
	rig.answer.Enabled = false
	response := rig.post(validForm())
	if response.Code != 302 || response.Header().Get("Location") != "/contact?contact=disabled" || len(rig.store.tokens) != 0 || len(rig.store.jobs) != 0 {
		t.Fatalf("got %d %q tokens=%d mails=%d", response.Code, response.Header().Get("Location"), len(rig.store.tokens), len(rig.store.jobs))
	}
}

func TestContactBodyBound(t *testing.T) {
	rig := newContactRig(t)
	form := validForm()
	form.Set("message", strings.Repeat("a", 16384))
	if got := rig.post(form).Code; got != 413 {
		t.Fatalf("declared oversize got %d", got)
	}
	// Unknown length: reaching the bound is rejected, as in Flask.
	request := httptest.NewRequest("POST", "/contact", strings.NewReader(strings.Repeat("a=b&", 4096)))
	request.ContentLength = -1
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	recorder := httptest.NewRecorder()
	rig.handler.ServeHTTP(recorder, request)
	if recorder.Code != 413 {
		t.Fatalf("streamed bound got %d", recorder.Code)
	}
	if len(rig.store.tokens) != 0 || len(rig.store.jobs) != 0 {
		t.Fatal("an oversized body must not consume a token")
	}
}

func TestContactMultipartIsReadLikeFlasksRequestForm(t *testing.T) {
	rig := newContactRig(t)
	boundary := "xyzBOUNDARY"
	var body strings.Builder
	for key, values := range validForm() {
		body.WriteString("--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + key + "\"\r\n\r\n" + values[0] + "\r\n")
	}
	body.WriteString("--" + boundary + "--\r\n")
	request := httptest.NewRequest("POST", "/contact?csrf_token=ignored&name=ignored", strings.NewReader(body.String()))
	request.Header.Set("Content-Type", "multipart/form-data; boundary="+boundary)
	request.Header.Set("Cookie", "session=x")
	request.RemoteAddr = "203.0.113.9:1"
	recorder := httptest.NewRecorder()
	rig.handler.ServeHTTP(recorder, request)
	if recorder.Header().Get("Location") != "/contact?contact=sent" || len(rig.store.jobs) != 1 {
		t.Fatalf("multipart got %d %q", recorder.Code, recorder.Header().Get("Location"))
	}
}

func TestContactQuotaKeysOrderAndLimits(t *testing.T) {
	rig := newContactRig(t)
	rig.post(validForm())
	want := []string{
		"LIMITER/global/public-contact-ingress/25/10/minute",
		"LIMITER/203.0.113.9/contact/3/10/minute",
		"LIMITER/global/public-contact-ingress/100/1/day",
		"LIMITER/203.0.113.9/contact/10/1/day",
	}
	if strings.Join(rig.store.hits, "\n") != strings.Join(want, "\n") {
		t.Fatalf("quota keys\n got %v\nwant %v", rig.store.hits, want)
	}
	// The fourth request in a window is refused before any token work.
	for i := 0; i < 2; i++ {
		form := validForm()
		form.Set("csrf_token", contactTestToken+string(rune('a'+i)))
		if got := rig.post(form).Code; got != 302 {
			t.Fatalf("request %d got %d", i+2, got)
		}
	}
	form := validForm()
	form.Set("csrf_token", contactTestToken+"z")
	asked := len(*rig.asked)
	if got := rig.post(form).Code; got != 429 || len(*rig.asked) != asked {
		t.Fatalf("fourth request got %d, Flask asked %d times", got, len(*rig.asked)-asked)
	}
	// The first breach stops the check, as in Flask-Limiter, and a different
	// address has its own quota.
	if rig.store.counters["LIMITER/global/public-contact-ingress/100/1/day"] != 3 {
		t.Fatal("counters after the breach differ from Flask-Limiter's behaviour")
	}
	if got := rig.post(validForm(), "X-Forwarded-For", "198.51.100.77").Code; got == 429 {
		t.Log("X-Forwarded-For is ignored from a non-loopback peer, so the same IP stays limited")
	}
}

func TestContactClientAddressFollowsTheTrustedProxyRules(t *testing.T) {
	rig := newContactRig(t)
	rig.post(validForm(), "X-Forwarded-For", "198.51.100.55")
	if rig.store.hits[1] != "LIMITER/203.0.113.9/contact/3/10/minute" {
		t.Fatalf("a client header from an untrusted peer must not pick the quota key: %v", rig.store.hits)
	}
	rig = newContactRig(t)
	request := httptest.NewRequest("POST", "/contact", strings.NewReader(validForm().Encode()))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	request.Header.Set("X-Forwarded-For", "10.9.9.9, 198.51.100.55")
	request.RemoteAddr = "127.0.0.1:5555"
	rig.handler.ServeHTTP(httptest.NewRecorder(), request)
	if rig.store.hits[1] != "LIMITER/198.51.100.55/contact/3/10/minute" {
		t.Fatalf("loopback proxy should supply the last forwarded address: %v", rig.store.hits)
	}
	// An IPv6 visitor is keyed by Python's canonical text form.
	rig = newContactRig(t)
	request = httptest.NewRequest("POST", "/contact", strings.NewReader(validForm().Encode()))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	request.Header.Set("X-Forwarded-For", "2001:DB8:0:0:0:0:0:1")
	request.RemoteAddr = "[::1]:5555"
	rig.handler.ServeHTTP(httptest.NewRecorder(), request)
	if rig.store.hits[1] != "LIMITER/2001:db8::1/contact/3/10/minute" {
		t.Fatalf("ipv6 key: %v", rig.store.hits)
	}
}

func TestContactFailuresNeverReportSuccess(t *testing.T) {
	rig := newContactRig(t)
	rig.store.queueErr = errors.New("insert failed")
	response := rig.post(validForm())
	if response.Code != 503 || response.Header().Get("Location") != "" || len(rig.store.events) != 0 {
		t.Fatalf("enqueue failure got %d %q events=%d", response.Code, response.Header().Get("Location"), len(rig.store.events))
	}
	if len(rig.store.tokens) != 1 {
		t.Fatal("the token stays consumed after a failed enqueue: exactly once")
	}
	rig = newContactRig(t)
	rig.store.hitErr = errors.New("limits down")
	if got := rig.post(validForm()).Code; got != 503 || len(rig.store.jobs) != 0 {
		t.Fatalf("limiter storage failure got %d", got)
	}
	rig = newContactRig(t)
	rig.store.tokenErr = errors.New("tokens down")
	if got := rig.post(validForm()).Code; got != 503 || len(rig.store.jobs) != 0 {
		t.Fatalf("token storage failure got %d", got)
	}
}

func TestContactFlaskBridgeFailureFailsClosed(t *testing.T) {
	config := testContactConfig(t)
	store := newFakeContactStore()
	handler := contactHandler(http.NotFoundHandler(), config, contactHandlerDeps{
		store: store,
		answer: func(context.Context, *http.Request, string) (contactAnswer, error) {
			return contactAnswer{}, errAuthUnavailable
		},
		now:      time.Now,
		requests: contactRequestID,
	})
	request := httptest.NewRequest("POST", "/contact", strings.NewReader(validForm().Encode()))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	if recorder.Code != 503 || len(store.tokens) != 0 || len(store.jobs) != 0 {
		t.Fatalf("got %d", recorder.Code)
	}
}

func TestContactRouteOnlyAnswersPostOnItsPath(t *testing.T) {
	rig := newContactRig(t)
	other := httptest.NewRecorder()
	rig.handler.ServeHTTP(other, httptest.NewRequest("POST", "/contact/", nil))
	if other.Code != 299 || atomic.LoadInt32(rig.nextHit) != 1 {
		t.Fatal("other paths must fall through")
	}
	get := httptest.NewRecorder()
	rig.handler.ServeHTTP(get, httptest.NewRequest("GET", "/contact", nil))
	if get.Code != 405 || get.Header().Get("Allow") != "POST" || len(rig.store.hits) != 0 {
		t.Fatalf("GET got %d", get.Code)
	}
}

func TestContactIsOffByDefaultAndNeedsTheBridge(t *testing.T) {
	bridge := authBridgeConfig{Enabled: true, Origin: "https://example.test", Socket: "/x"}
	t.Setenv("CONTACT_GO_ENABLED", "")
	if c, err := contactConfigFromEnv(bridge); err != nil || c.Enabled {
		t.Fatalf("unset flag must mean off: %v %v", c.Enabled, err)
	}
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(404) })
	handler := contactHandlerFromEnv(next, nil, bridge)
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest("POST", "/contact", nil))
	if recorder.Code != 404 {
		t.Fatalf("flag off must not claim the route, got %d", recorder.Code)
	}
	for _, value := range []string{"1", "TRUE", "yes", "false"} {
		t.Setenv("CONTACT_GO_ENABLED", value)
		if c, _ := contactConfigFromEnv(bridge); c.Enabled {
			t.Errorf("%q must not enable the route", value)
		}
	}
	t.Setenv("CONTACT_GO_ENABLED", "true")
	if _, err := contactConfigFromEnv(authBridgeConfig{}); err == nil {
		t.Fatal("enabling without the auth bridge must be an error")
	}
	handler = contactHandlerFromEnv(next, nil, authBridgeConfig{})
	recorder = httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest("POST", "/contact", nil))
	if recorder.Code != 404 {
		t.Fatalf("a bad configuration must leave the route off, got %d", recorder.Code)
	}
}

func TestContactLimitStringsParseLikeFlaskLimiter(t *testing.T) {
	limits, err := parseContactLimits("3 per 10 minutes; 10 per day")
	if err != nil || len(limits) != 2 || limits[0].suffix() != "3/10/minute" || limits[1].suffix() != "10/1/day" ||
		limits[0].window() != 10*time.Minute || limits[1].window() != 24*time.Hour {
		t.Fatalf("%v %+v", err, limits)
	}
	limits, err = parseContactLimits("5/minute|1 per 2 hours,7 per year")
	if err != nil || len(limits) != 3 || limits[0].suffix() != "5/1/minute" || limits[1].suffix() != "1/2/hour" || limits[2].window() != 360*24*time.Hour {
		t.Fatalf("%v %+v", err, limits)
	}
	for _, bad := range []string{"", "lots", "0 per day", "3 per fortnight", "3 per 10", "3 per 0 minutes", "-1 per day"} {
		if _, err := parseContactLimits(bad); err == nil {
			t.Errorf("%q must be rejected so a typo cannot drop a quota", bad)
		}
	}
	t.Setenv("CONTACT_GO_ENABLED", "true")
	t.Setenv("CONTACT_PER_IP_LIMIT", "oops")
	if _, err := contactConfigFromEnv(authBridgeConfig{Enabled: true}); err == nil {
		t.Fatal("a bad configured limit must keep the route off")
	}
}

func TestContactEventKeyFollowsFlaskAnalytics(t *testing.T) {
	now := time.Date(2026, 7, 1, 21, 30, 0, 0, time.UTC) // 00:30 Helsinki the next day (EEST)
	request := func(agent, referer, dnt string) *http.Request {
		r := httptest.NewRequest("POST", "/contact", nil)
		r.Host = "mielenosoitukset.fi"
		for key, value := range map[string]string{"User-Agent": agent, "Referer": referer, "DNT": dnt} {
			if value != "" {
				r.Header.Set(key, value)
			}
		}
		return r
	}
	key := contactEventKey(request("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Mobile", "https://www.google.com/search?q=x", ""), now)
	if key["device"] != "mobile" || key["referrer"] != "google" || key["date"] != "2026-07-02" || key["hour"] != 0 {
		t.Fatalf("%v", key)
	}
	if key = contactEventKey(request("", "", ""), now); key["device"] != "other" || key["referrer"] != "direct" {
		t.Fatalf("%v", key)
	}
	if contactEventKey(request("Googlebot/2.1", "", ""), now) != nil || contactEventKey(request("Mozilla/5.0 Linux", "", "1"), now) != nil {
		t.Fatal("bots and Do-Not-Track must not be counted")
	}
	if key = contactEventKey(request("Mozilla/5.0 Linux", "https://mielenosoitukset.fi/contact", ""), now); key["referrer"] != "internal" {
		t.Fatalf("%v", key)
	}
	if got := contactReferrer("https://elsewhere.example/page", "mielenosoitukset.fi"); got != "other" {
		t.Fatalf("got %s", got)
	}
}

func TestContactAnalyticsOffMeansNoCounter(t *testing.T) {
	t.Setenv("SITE_ANALYTICS_ENABLED", "")
	config := testContactConfig(t)
	t.Setenv("SITE_ANALYTICS_ENABLED", "")
	config.Analytics = false
	store := newFakeContactStore()
	handler := contactHandler(http.NotFoundHandler(), config, contactHandlerDeps{store: store, now: time.Now, requests: contactRequestID,
		answer: func(context.Context, *http.Request, string) (contactAnswer, error) { return defaultAnswer, nil }})
	request := httptest.NewRequest("POST", "/contact", strings.NewReader(validForm().Encode()))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	handler.ServeHTTP(httptest.NewRecorder(), request)
	if len(store.jobs) != 1 || len(store.events) != 0 {
		t.Fatalf("mails=%d events=%d", len(store.jobs), len(store.events))
	}
}

// The real session client against a fake Flask on a unix socket.
func TestContactSessionClientSendsTheTokenInAHeaderNotTheURL(t *testing.T) {
	var seen *http.Request
	config, calls := fakeFlask(t, func(w http.ResponseWriter, r *http.Request) {
		seen = r.Clone(context.Background())
		w.Header().Set("Content-Type", "application/json")
		if r.Header.Get("Cookie") == "session=down" {
			w.WriteHeader(500)
			return
		}
		w.Write([]byte(`{"authenticated":false,"contact_token":{"valid":true,"enabled":true,"max_age_seconds":7200,` +
			`"limits":{"name_max":120,"email_max":254,"message_min":10,"message_max":5000}}}`))
	})
	resolver, err := newSessionResolver(config)
	if err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest("POST", "/contact", nil)
	request.Header.Set("Cookie", "session=abc")
	request.Header.Set("User-Agent", "agent/1")
	request.RemoteAddr = "203.0.113.9:1"
	answer, err := resolver.contactAnswer(context.Background(), request, contactTestToken)
	if err != nil || !answer.Valid || !answer.Enabled || answer.MaxAge != 7200 || answer.MessageMax != 5000 || atomic.LoadInt32(calls) != 1 {
		t.Fatalf("%+v %v", answer, err)
	}
	if seen.URL.Path != "/api/session" || seen.URL.RawQuery != "" || strings.Contains(seen.RequestURI, contactTestToken) {
		t.Fatalf("the token must not be in the URL: %s", seen.RequestURI)
	}
	if seen.Header.Get("X-Contact-Form-Token") != contactTestToken || seen.Header.Get("Cookie") != "session=abc" || seen.Method != "GET" {
		t.Fatalf("headers %v", seen.Header)
	}
	request.Header.Set("Cookie", "session=down")
	if _, err := resolver.contactAnswer(context.Background(), request, contactTestToken); err == nil {
		t.Fatal("a Flask error must be an error, never 'invalid'")
	}
}

func TestContactSessionClientRejectsAnswersWithoutTheScope(t *testing.T) {
	for name, body := range map[string]string{
		"flag off in Flask":   `{"authenticated":false}`,
		"missing valid":       `{"contact_token":{"enabled":true,"max_age_seconds":7200,"limits":{"name_max":1,"email_max":1,"message_min":0,"message_max":1}}}`,
		"zero limits":         `{"contact_token":{"valid":true,"enabled":true,"max_age_seconds":7200,"limits":{}}}`,
		"min above max":       `{"contact_token":{"valid":true,"enabled":true,"max_age_seconds":7200,"limits":{"name_max":1,"email_max":1,"message_min":9,"message_max":2}}}`,
		"unbounded token age": `{"contact_token":{"valid":true,"enabled":true,"max_age_seconds":99999999,"limits":{"name_max":1,"email_max":1,"message_min":0,"message_max":1}}}`,
		"not json":            `<html>`,
	} {
		config, _ := fakeFlask(t, func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(body)) })
		resolver, _ := newSessionResolver(config)
		request := httptest.NewRequest("POST", "/contact", nil)
		if _, err := resolver.contactAnswer(context.Background(), request, ""); err == nil {
			t.Errorf("%s must fail closed", name)
		}
	}
}

// With a disposable MongoDB (CONTACT_TEST_MONGO) the Flask-Limiter counter
// update, the token insert and the queue row run for real.
func contactTestMongo(t *testing.T) (*mongo.Database, *mongo.Database) {
	t.Helper()
	uri := os.Getenv("CONTACT_TEST_MONGO")
	if uri == "" {
		t.Skip("CONTACT_TEST_MONGO not set")
	}
	client, err := mongo.Connect(options.Client().ApplyURI(uri))
	if err != nil {
		t.Fatal(err)
	}
	suffix := hex.EncodeToString([]byte(t.Name()))[:16]
	db, limits := client.Database("ct_"+suffix), client.Database("ctl_"+suffix)
	ctx := context.Background()
	_ = db.Drop(ctx)
	_ = limits.Drop(ctx)
	t.Cleanup(func() { _ = db.Drop(ctx); _ = limits.Drop(ctx); _ = client.Disconnect(ctx) })
	return db, limits
}

func TestContactMongoCounterTokenAndQueue(t *testing.T) {
	db, limits := contactTestMongo(t)
	store := mongoContactStore{db: db, limiter: limits}
	ctx := context.Background()

	for want := int64(1); want <= 3; want++ {
		if got, err := store.Hit(ctx, "LIMITER/203.0.113.9/contact/3/10/minute", 10*time.Minute); err != nil || got != want {
			t.Fatalf("hit %d returned %d, %v", want, got, err)
		}
	}
	var counter bson.M
	if err := limits.Collection("counters").FindOne(ctx, bson.M{"_id": "LIMITER/203.0.113.9/contact/3/10/minute"}).Decode(&counter); err != nil {
		t.Fatal(err)
	}
	if _, ok := counter["expireAt"].(bson.DateTime); !ok || len(counter) != 3 {
		t.Fatalf("counter document differs from the limits library's: %v", counter)
	}
	// An expired window starts again at one, like the library's pipeline.
	if _, err := limits.Collection("counters").UpdateOne(ctx, bson.M{"_id": "LIMITER/203.0.113.9/contact/3/10/minute"},
		bson.M{"$set": bson.M{"expireAt": time.Now().Add(-time.Minute)}}); err != nil {
		t.Fatal(err)
	}
	if got, err := store.Hit(ctx, "LIMITER/203.0.113.9/contact/3/10/minute", 10*time.Minute); err != nil || got != 1 {
		t.Fatalf("expired window returned %d, %v", got, err)
	}
	// Parallel hits never lose an increment.
	var wg sync.WaitGroup
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			store.Hit(ctx, "LIMITER/global/public-contact-ingress/25/10/minute", 10*time.Minute)
		}()
	}
	wg.Wait()
	if got, _ := store.Hit(ctx, "LIMITER/global/public-contact-ingress/25/10/minute", 10*time.Minute); got != 21 {
		t.Fatalf("parallel hits counted %d, want 21", got)
	}

	hash := contactTokenHash()
	expires := time.Now().UTC().Add(2 * time.Hour)
	if ok, err := store.ConsumeToken(ctx, hash, expires); !ok || err != nil {
		t.Fatalf("first consume: %v %v", ok, err)
	}
	if ok, err := store.ConsumeToken(ctx, hash, expires); ok || err != nil {
		t.Fatalf("second consume must be a clean 'already used': %v %v", ok, err)
	}
	var token bson.M
	if err := db.Collection("contact_form_tokens").FindOne(ctx, bson.M{"_id": hash}).Decode(&token); err != nil || token["expires_at"] == nil || len(token) != 2 {
		t.Fatalf("token document %v %v", token, err)
	}

	if err := store.Enqueue(ctx, contactJob("<p>x</p>", "rid", "inst")); err != nil {
		t.Fatal(err)
	}
	var queued bson.M
	if err := db.Collection("email_queue").FindOne(ctx, bson.M{"status": "pending", "attempts": 0}).Decode(&queued); err != nil || queued["subject"] != "Uusi tukipyyntö!" {
		t.Fatalf("queue row %v %v", queued, err)
	}
}

func TestContactEndToEndAgainstMongo(t *testing.T) {
	db, limits := contactTestMongo(t)
	rig := newContactRig(t)
	store := mongoContactStore{db: db, limiter: limits}
	handler := contactHandler(http.NotFoundHandler(), testContactConfig(t), contactHandlerDeps{
		store: store, now: time.Now, requests: contactRequestID, instance: contactUUID(),
		answer: func(context.Context, *http.Request, string) (contactAnswer, error) { return defaultAnswer, nil },
	})
	_ = rig
	request := httptest.NewRequest("POST", "/contact", strings.NewReader(validForm().Encode()))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	request.RemoteAddr = "203.0.113.9:1"
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	if recorder.Code != 302 {
		t.Fatalf("got %d", recorder.Code)
	}
	ctx := context.Background()
	if n, _ := db.Collection("email_queue").CountDocuments(ctx, bson.M{}); n != 1 {
		t.Fatalf("queued %d mails", n)
	}
	if n, _ := limits.Collection("counters").CountDocuments(ctx, bson.M{}); n != 4 {
		t.Fatalf("limiter wrote %d counters", n)
	}
	if n, _ := db.Collection("contact_form_tokens").CountDocuments(ctx, bson.M{}); n != 1 {
		t.Fatalf("stored %d token hashes", n)
	}
	if n, _ := db.Collection("site_analytics").CountDocuments(ctx, bson.M{"event": "contact_message"}); n != 1 {
		t.Fatalf("counted %d events", n)
	}
}
