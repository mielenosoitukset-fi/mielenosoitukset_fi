package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

// Tests for the dark Go POST /kampanja/api/volunteers (docs/go-campaign-volunteers.md).
// No test sends mail: the store is a fake that only records queue rows.

type fakeVolunteerStore struct {
	mu          sync.Mutex
	published   bool
	publishedEr error
	attempts    map[string][]time.Time
	volunteers  []bson.D
	jobs        []bson.D
	events      []bson.M
	purged      []time.Time
	insertErr   error
	queueErr    error
	attemptErr  error
	deleted     int
}

func newFakeVolunteerStore() *fakeVolunteerStore {
	return &fakeVolunteerStore{published: true, attempts: map[string][]time.Time{}}
}

func (s *fakeVolunteerStore) Published(context.Context) (bool, error) {
	return s.published, s.publishedEr
}
func (s *fakeVolunteerStore) PurgeAttempts(context.Context, time.Time) error { return nil }
func (s *fakeVolunteerStore) Attempt(_ context.Context, key string, now time.Time, window time.Duration) (int64, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.attemptErr != nil {
		return 0, s.attemptErr
	}
	s.attempts[key] = append(s.attempts[key], now)
	var n int64
	for _, at := range s.attempts[key] {
		if !at.Before(now.Add(-window)) {
			n++
		}
	}
	return n - 1, nil
}
func (s *fakeVolunteerStore) PurgeUnconfirmed(_ context.Context, before time.Time) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.purged = append(s.purged, before)
	return nil
}
func (s *fakeVolunteerStore) Insert(_ context.Context, doc bson.D) (any, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.insertErr != nil {
		return nil, s.insertErr
	}
	s.volunteers = append(s.volunteers, doc)
	return len(s.volunteers) - 1, nil
}
func (s *fakeVolunteerStore) Delete(_ context.Context, id any) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.deleted++
	s.volunteers[id.(int)] = nil
	return nil
}
func (s *fakeVolunteerStore) Enqueue(_ context.Context, job bson.D) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.queueErr != nil {
		return s.queueErr
	}
	s.jobs = append(s.jobs, job)
	return nil
}
func (s *fakeVolunteerStore) Count(_ context.Context, key bson.M) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.events = append(s.events, key)
	return nil
}

var volunteerNow = time.Date(2026, 11, 20, 10, 0, 0, 0, time.UTC)

type volunteerHarness struct {
	store   *fakeVolunteerStore
	handler http.Handler
	signed  bool
	authErr error
	clock   *time.Time
}

func newVolunteerHarness(analytics bool) *volunteerHarness {
	h := &volunteerHarness{store: newFakeVolunteerStore()}
	now := volunteerNow
	h.clock = &now
	var counter atomic.Int64
	h.handler = campaignVolunteerHandler(http.NotFoundHandler(), campaignVolunteerConfig{Enabled: true, Analytics: analytics}, campaignVolunteerDeps{
		store: h.store,
		principal: func(context.Context, *http.Request) (Principal, error) {
			return Principal{Authenticated: h.signed}, h.authErr
		},
		now: func() time.Time { return *h.clock },
		token: func() (string, error) {
			n := counter.Add(1)
			return "tok" + strings.Repeat("A", 17) + string(rune('a'+n/26%26)) + string(rune('a'+n%26)), nil
		},
		instance: "00000000-0000-4000-8000-000000000000",
	})
	return h
}

func (h *volunteerHarness) post(body string, mutate ...func(*http.Request)) *httptest.ResponseRecorder {
	r := httptest.NewRequest(http.MethodPost, campaignVolunteerPath, strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	r.RemoteAddr = "203.0.113.7:4000"
	for _, m := range mutate {
		m(r)
	}
	w := httptest.NewRecorder()
	h.handler.ServeHTTP(w, r)
	return w
}

func volunteerBody(w *httptest.ResponseRecorder) map[string]any {
	var out map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &out)
	return out
}

func volunteerDoc(d bson.D) map[string]any {
	out := map[string]any{}
	for _, e := range d {
		out[e.Key] = e.Value
	}
	return out
}

func TestVolunteerSignupStoresDocumentLikeFlask(t *testing.T) {
	h := newVolunteerHarness(false)
	w := h.post(`{"name":" Aino ","email":"aino-roles@example.test","city":" Tampere ","roles":["communications","stickers","stickers"],"area":" Tampereen keskusta ","notes":" Hei ","phone":"040 123 4567","unexpected":"x"}`)
	if w.Code != 201 || volunteerBody(w)["success"] != true {
		t.Fatalf("got %d %s", w.Code, w.Body)
	}
	if len(h.store.volunteers) != 1 || len(h.store.jobs) != 1 {
		t.Fatalf("volunteers=%d jobs=%d", len(h.store.volunteers), len(h.store.jobs))
	}
	doc := volunteerDoc(h.store.volunteers[0])
	want := map[string]any{"name": "Aino", "email": "aino-roles@example.test", "city": "Tampere", "area": "Tampereen keskusta", "notes": "Hei", "confirmed": false}
	for k, v := range want {
		if doc[k] != v {
			t.Errorf("%s = %#v, want %#v", k, doc[k], v)
		}
	}
	if roles := doc["roles"].([]string); strings.Join(roles, ",") != "stickers,communications" {
		t.Errorf("roles %v", roles)
	}
	for _, forbidden := range []string{"phone", "unexpected", "confirmation_token"} {
		if _, ok := doc[forbidden]; ok {
			t.Errorf("%s must not be stored", forbidden)
		}
	}
	// Key order and field set match the Flask document.
	var keys []string
	for _, e := range h.store.volunteers[0] {
		keys = append(keys, e.Key)
	}
	if strings.Join(keys, ",") != "name,email,city,roles,area,notes,confirmed,confirmation_token_hash,confirmation_expires" {
		t.Errorf("keys %v", keys)
	}
	if doc["confirmation_expires"] != volunteerNow.Add(24*time.Hour) {
		t.Errorf("expiry %v", doc["confirmation_expires"])
	}
}

func TestVolunteerTokenIsHashedAndLinkedInTheMail(t *testing.T) {
	h := newVolunteerHarness(false)
	h.post(`{"name":"Aino","email":"hash@example.test"}`)
	doc := volunteerDoc(h.store.volunteers[0])
	job := volunteerDoc(h.store.jobs[0])
	body := job["html"].(string)
	const prefix = "https://mielenosoitukset.fi/kampanja/confirm/"
	i := strings.Index(body, prefix)
	if i < 0 {
		t.Fatalf("no link in %q", body)
	}
	token := body[i+len(prefix) : strings.Index(body[i:], `"`)+i]
	if doc["confirmation_token_hash"] != campaignDigest(token) || len(doc["confirmation_token_hash"].(string)) != 64 {
		t.Fatalf("hash %v does not match mailed token %q", doc["confirmation_token_hash"], token)
	}
	for _, e := range h.store.volunteers[0] {
		if s, ok := e.Value.(string); ok && s == token {
			t.Fatalf("plain token stored under %s", e.Key)
		}
	}
	if job["subject"] != "Kiitos ilmoittautumisesta! Vahvista sähköpostisi" || job["status"] != "pending" || job["attempts"] != int32(0) ||
		job["body"] != body || job["sender"] != nil || job["attachments"] != nil {
		t.Errorf("job %#v", job)
	}
	if got := job["recipients"].(bson.A); len(got) != 1 || got[0] != "hash@example.test" {
		t.Errorf("recipients %v", got)
	}
}

func TestCampaignTokenShape(t *testing.T) {
	seen := map[string]bool{}
	for i := 0; i < 200; i++ {
		token, err := campaignToken()
		if err != nil || len(token) != 22 || !campaignConfirmToken.MatchString(token) || seen[token] {
			t.Fatalf("bad token %q %v", token, err)
		}
		seen[token] = true
	}
}

func TestVolunteerWelcomeMailMatchesJinjaGolden(t *testing.T) {
	raw, err := os.ReadFile("testdata/campaign_volunteer_welcome.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases map[string]struct{ Name, Link, HTML string }
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	for name, c := range cases {
		got, err := campaignWelcomeBody(c.Name, c.Link)
		if err != nil || got != c.HTML {
			t.Errorf("%s:\n got %q\nwant %q (%v)", name, got, c.HTML, err)
		}
	}
}

func TestVolunteerValidationMatrix(t *testing.T) {
	long := func(n int) string { return strings.Repeat("ä", n) }
	required, format := campaignErrRequired, campaignErrFormat
	cases := []struct {
		name, body, ctype string
		status            int
		message           string
	}{
		{"missing name", `{"email":"a@b.fi"}`, "", 400, required},
		{"missing email", `{"name":"A"}`, "", 400, required},
		{"blank name", `{"name":"  ","email":"a@b.fi"}`, "", 400, required},
		{"name not a string", `{"name":5,"email":"a@b.fi"}`, "", 400, required},
		{"format char only name", `{"name":"\u200b\u202e","email":"a@b.fi"}`, "", 400, required},
		{"not json", `name=A`, "", 400, required},
		{"form content type", `{"name":"A","email":"a@b.fi"}`, "application/x-www-form-urlencoded", 400, required},
		{"array body", `[{"name":"A","email":"a@b.fi"}]`, "", 400, required},
		{"null body", `null`, "", 400, required},
		{"invalid utf8", "{\"name\":\"A\xff\",\"email\":\"a@b.fi\"}", "", 400, required},
		{"vendor json type", `{"name":"A","email":"a@b.fi"}`, "application/vnd.api+json", 201, ""},
		{"bad email no at", `{"name":"A","email":"ab.fi"}`, "", 400, format},
		{"bad email no dot", `{"name":"A","email":"a@bfi"}`, "", 400, format},
		{"bad email dot first", `{"name":"A","email":"a@.fi"}`, "", 400, format},
		{"bad email dot last", `{"name":"A","email":"a@b."}`, "", 400, format},
		{"bad email two at", `{"name":"A","email":"a@@b.fi"}`, "", 400, format},
		{"bad email inner space", `{"name":"A","email":"a b@c.fi"}`, "", 400, format},
		{"bad email nbsp", `{"name":"A","email":"a@b\u00a0c.fi"}`, "", 400, format},
		{"ok email dots", `{"name":"A","email":"a@b.c.d"}`, "", 201, ""},
		{"ok email subdomain dot at index one", `{"name":"A","email":"a@b.cc"}`, "", 201, ""},
		{"email 254 ok", `{"name":"A","email":"` + strings.Repeat("a", 243) + `@example.fi"}`, "", 201, ""},
		{"email 255", `{"name":"A","email":"` + strings.Repeat("a", 244) + `@example.fi"}`, "", 400, format},
		{"name 120 ok", `{"name":"` + long(120) + `","email":"n120@b.fi"}`, "", 201, ""},
		{"name 121", `{"name":"` + long(121) + `","email":"n121@b.fi"}`, "", 400, format},
		{"city 100 ok", `{"name":"A","email":"c100@b.fi","city":"` + long(100) + `"}`, "", 201, ""},
		{"city 101", `{"name":"A","email":"c101@b.fi","city":"` + long(101) + `"}`, "", 400, format},
		{"area 201 even without stickers", `{"name":"A","email":"a201@b.fi","area":"` + long(201) + `"}`, "", 400, format},
		{"notes 2000 ok", `{"name":"A","email":"n2000@b.fi","notes":"` + long(2000) + `"}`, "", 201, ""},
		{"notes 2001", `{"name":"A","email":"n2001@b.fi","notes":"` + long(2001) + `"}`, "", 400, format},
		{"roles string", `{"name":"A","email":"r1@b.fi","roles":"stickers"}`, "", 400, format},
		{"roles null", `{"name":"A","email":"r2@b.fi","roles":null}`, "", 400, format},
		{"roles unknown", `{"name":"A","email":"r3@b.fi","roles":["stickers","hacker"]}`, "", 400, format},
		{"roles nested", `{"name":"A","email":"r4@b.fi","roles":[["stickers"]]}`, "", 400, format},
		{"roles number", `{"name":"A","email":"r5@b.fi","roles":[1]}`, "", 400, format},
		{"roles empty ok", `{"name":"A","email":"r6@b.fi","roles":[]}`, "", 201, ""},
		{"required beats format", `{"name":"","email":"x","roles":"bad"}`, "", 400, required},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			h := newVolunteerHarness(false)
			w := h.post(c.body, func(r *http.Request) {
				if c.ctype != "" {
					r.Header.Set("Content-Type", c.ctype)
				}
			})
			if w.Code != c.status {
				t.Fatalf("status %d, want %d (%s)", w.Code, c.status, w.Body)
			}
			body := volunteerBody(w)
			if c.status == 201 {
				if body["success"] != true || len(body) != 1 || len(h.store.volunteers) != 1 {
					t.Fatalf("body %v stored %d", body, len(h.store.volunteers))
				}
				return
			}
			if body["success"] != false || body["error"] != c.message {
				t.Fatalf("body %v", body)
			}
			if len(h.store.volunteers)+len(h.store.jobs) != 0 || len(h.store.attempts) != 0 {
				t.Fatal("invalid input must store nothing and cost no rate-limit budget")
			}
		})
	}
}

func TestVolunteerFieldCleaning(t *testing.T) {
	h := newVolunteerHarness(false)
	h.post(`{"name":"\u202eA\u200bino\u202c ","email":" cl\u200ben@example.test\t","city":"\u00a0Oulu\u001f","area":"x","roles":["events"],"notes":"\u2003hello\n"}`)
	doc := volunteerDoc(h.store.volunteers[0])
	if doc["name"] != "Aino" || doc["email"] != "clen@example.test" || doc["city"] != "Oulu" || doc["notes"] != "hello" || doc["area"] != "" {
		t.Fatalf("%#v", doc)
	}
}

func TestVolunteerAreaOnlyWithStickers(t *testing.T) {
	h := newVolunteerHarness(false)
	h.post(`{"name":"A","email":"x1@example.test","roles":["events"],"area":"Turku"}`)
	h.post(`{"name":"A","email":"x2@example.test","roles":["stickers","events"],"area":"Turku"}`)
	if volunteerDoc(h.store.volunteers[0])["area"] != "" || volunteerDoc(h.store.volunteers[1])["area"] != "Turku" {
		t.Fatalf("%v", h.store.volunteers)
	}
}

func TestVolunteerPublicationGate(t *testing.T) {
	body := `{"name":"A","email":"gate@example.test"}`
	t.Run("private anonymous", func(t *testing.T) {
		h := newVolunteerHarness(false)
		h.store.published = false
		w := h.post(body)
		if w.Code != 401 || volunteerBody(w)["error"] != campaignErrPrivate || len(h.store.volunteers) != 0 || len(h.store.attempts) != 0 {
			t.Fatalf("%d %s", w.Code, w.Body)
		}
		if w.Header().Get("X-Robots-Tag") != "noindex, nofollow" || w.Header().Get("Cache-Control") != "private, no-store" || w.Header().Get("Vary") != "Cookie" {
			t.Fatalf("headers %v", w.Header())
		}
	})
	t.Run("private signed in may preview", func(t *testing.T) {
		h := newVolunteerHarness(false)
		h.store.published, h.signed = false, true
		w := h.post(body)
		if w.Code != 201 || w.Header().Get("X-Robots-Tag") != "noindex, nofollow" {
			t.Fatalf("%d %v", w.Code, w.Header())
		}
	})
	t.Run("read failure is private", func(t *testing.T) {
		h := newVolunteerHarness(false)
		h.store.publishedEr = errors.New("db down")
		if w := h.post(body); w.Code != 401 {
			t.Fatalf("%d", w.Code)
		}
	})
	t.Run("bridge failure is unavailable, never allowed", func(t *testing.T) {
		h := newVolunteerHarness(false)
		h.store.published, h.authErr = false, errors.New("bridge down")
		if w := h.post(body); w.Code != 503 || len(h.store.volunteers) != 0 {
			t.Fatalf("%d", w.Code)
		}
	})
	t.Run("public needs no session and is not robots-blocked", func(t *testing.T) {
		h := newVolunteerHarness(false)
		h.authErr = errors.New("must not be asked")
		w := h.post(body)
		if w.Code != 201 || w.Header().Get("X-Robots-Tag") != "" || w.Header().Get("Cache-Control") != "private, no-store" {
			t.Fatalf("%d %v", w.Code, w.Header())
		}
	})
}

func TestVolunteerRateLimitPerIP(t *testing.T) {
	h := newVolunteerHarness(false)
	for i := 0; i < 5; i++ {
		if w := h.post(`{"name":"A","email":"ip` + string(rune('a'+i)) + `@example.test"}`); w.Code != 201 {
			t.Fatalf("request %d: %d", i, w.Code)
		}
	}
	w := h.post(`{"name":"A","email":"ipz@example.test"}`)
	if w.Code != 429 || volunteerBody(w)["error"] != campaignErrRate || len(h.store.volunteers) != 5 {
		t.Fatalf("%d %s stored %d", w.Code, w.Body, len(h.store.volunteers))
	}
	// Another client, and the same client an hour later, are fine.
	if w := h.post(`{"name":"A","email":"other@example.test"}`, func(r *http.Request) { r.RemoteAddr = "203.0.113.99:1" }); w.Code != 201 {
		t.Fatalf("other client %d", w.Code)
	}
	*h.clock = h.clock.Add(61 * time.Minute)
	if w := h.post(`{"name":"A","email":"later@example.test"}`); w.Code != 201 {
		t.Fatalf("after window %d", w.Code)
	}
}

func TestVolunteerRateLimitKeysAreHashed(t *testing.T) {
	h := newVolunteerHarness(false)
	h.post(`{"name":"A","email":"Key@Example.test"}`)
	want := []string{
		"ip:" + campaignDigest("203.0.113.7"),
		"email:" + campaignDigest("key@example.test"),
		"email-day:" + campaignDigest("key@example.test"),
	}
	if len(h.store.attempts) != 3 {
		t.Fatalf("keys %v", h.store.attempts)
	}
	for _, key := range want {
		if _, ok := h.store.attempts[key]; !ok {
			t.Errorf("missing key %s in %v", key, h.store.attempts)
		}
	}
	for key := range h.store.attempts {
		if strings.Contains(key, "203.0.113") || strings.Contains(strings.ToLower(key), "example") {
			t.Errorf("key leaks its value: %s", key)
		}
	}
}

func TestVolunteerRepeatAddressGetsOneMailSilently(t *testing.T) {
	h := newVolunteerHarness(false)
	for i := 0; i < 4; i++ {
		// Different clients so only the address limit applies.
		w := h.post(`{"name":"A","email":"Repeat@example.test"}`, func(r *http.Request) { r.RemoteAddr = "198.51.100." + string(rune('1'+i)) + ":1" })
		if w.Code != 201 || volunteerBody(w)["success"] != true {
			t.Fatalf("request %d: %d %s", i, w.Code, w.Body)
		}
	}
	// 2 per hour: the third and fourth are silent no-ops.
	if len(h.store.volunteers) != 2 || len(h.store.jobs) != 2 {
		t.Fatalf("stored %d mailed %d", len(h.store.volunteers), len(h.store.jobs))
	}
}

func TestVolunteerDailyAddressLimit(t *testing.T) {
	h := newVolunteerHarness(false)
	for i := 0; i < 6; i++ {
		h.post(`{"name":"A","email":"day@example.test"}`, func(r *http.Request) { r.RemoteAddr = "198.51.100." + string(rune('1'+i)) + ":1" })
		*h.clock = h.clock.Add(61 * time.Minute) // fresh hourly window each time
	}
	if len(h.store.volunteers) != 3 {
		t.Fatalf("a day allows 3 signups, stored %d", len(h.store.volunteers))
	}
}

func TestVolunteerHourlyEmailShortCircuitsDailyCount(t *testing.T) {
	// Flask evaluates `hour or day`: when the hourly limit already trips, the
	// daily key is not touched.
	h := newVolunteerHarness(false)
	for i := 0; i < 3; i++ {
		h.post(`{"name":"A","email":"sc@example.test"}`, func(r *http.Request) { r.RemoteAddr = "198.51.100." + string(rune('1'+i)) + ":1" })
	}
	day := h.store.attempts["email-day:"+campaignDigest("sc@example.test")]
	hour := h.store.attempts["email:"+campaignDigest("sc@example.test")]
	if len(hour) != 3 || len(day) != 2 {
		t.Fatalf("hour %d day %d", len(hour), len(day))
	}
}

func TestVolunteerPurgesOldUnconfirmedOnlyForNewSignups(t *testing.T) {
	h := newVolunteerHarness(false)
	h.post(`{"name":"A","email":"purge@example.test"}`)
	if len(h.store.purged) != 1 || !h.store.purged[0].Equal(volunteerNow.Add(-7*24*time.Hour)) {
		t.Fatalf("%v", h.store.purged)
	}
	h.post(`{"name":"A","email":"purge@example.test"}`)
	h.post(`{"name":"A","email":"purge@example.test"}`) // silent repeat
	if len(h.store.purged) != 2 {
		t.Fatalf("silent repeats must not purge: %v", h.store.purged)
	}
}

func TestVolunteerConcurrentSignupsNeverExceedTheLimit(t *testing.T) {
	h := newVolunteerHarness(false)
	var wg sync.WaitGroup
	var mu sync.Mutex
	created, limited := 0, 0
	for i := 0; i < 40; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			w := h.post(`{"name":"A","email":"conc` + strings.Repeat("x", i) + `@example.test"}`)
			mu.Lock()
			defer mu.Unlock()
			switch w.Code {
			case 201:
				created++
			case 429:
				limited++
			case 503: // busy: the in-flight cap is part of the contract
			default:
				t.Errorf("status %d", w.Code)
			}
		}(i)
	}
	wg.Wait()
	if created > campaignLimitPerIP || len(h.store.volunteers) != created || len(h.store.jobs) != created {
		t.Fatalf("created %d stored %d mailed %d", created, len(h.store.volunteers), len(h.store.jobs))
	}
}

func TestVolunteerConcurrentSameAddressStoresAtMostTwo(t *testing.T) {
	h := newVolunteerHarness(false)
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			h.post(`{"name":"A","email":"same@example.test"}`, func(r *http.Request) { r.RemoteAddr = "198.51.100." + string(rune('1'+i)) + ":1" })
		}(i)
	}
	wg.Wait()
	if len(h.store.volunteers) > campaignLimitPerEmail {
		t.Fatalf("stored %d for one address", len(h.store.volunteers))
	}
}

func TestVolunteerStorageFailures(t *testing.T) {
	t.Run("rate storage down", func(t *testing.T) {
		h := newVolunteerHarness(false)
		h.store.attemptErr = errors.New("down")
		if w := h.post(`{"name":"A","email":"f1@example.test"}`); w.Code != 503 || len(h.store.volunteers) != 0 {
			t.Fatalf("%d", w.Code)
		}
	})
	t.Run("insert fails", func(t *testing.T) {
		h := newVolunteerHarness(false)
		h.store.insertErr = errors.New("down")
		if w := h.post(`{"name":"A","email":"f2@example.test"}`); w.Code != 503 || len(h.store.jobs) != 0 {
			t.Fatalf("%d", w.Code)
		}
	})
	t.Run("queue fails removes the signup", func(t *testing.T) {
		h := newVolunteerHarness(false)
		h.store.queueErr = errors.New("down")
		w := h.post(`{"name":"A","email":"f3@example.test"}`)
		if w.Code != 503 || volunteerBody(w)["success"] != false || h.store.deleted != 1 || h.store.volunteers[0] != nil {
			t.Fatalf("%d deleted=%d", w.Code, h.store.deleted)
		}
	})
}

func TestVolunteerBodyBoundAndMethod(t *testing.T) {
	h := newVolunteerHarness(false)
	big := `{"name":"A","email":"big@example.test","notes":"` + strings.Repeat("x", campaignVolunteerMaxBody) + `"}`
	if w := h.post(big); w.Code != 413 || len(h.store.volunteers) != 0 {
		t.Fatalf("%d", w.Code)
	}
	r := httptest.NewRequest(http.MethodGet, campaignVolunteerPath, nil)
	w := httptest.NewRecorder()
	h.handler.ServeHTTP(w, r)
	if w.Code != 405 || w.Header().Get("Allow") != "POST" {
		t.Fatalf("%d", w.Code)
	}
	other := httptest.NewRecorder()
	h.handler.ServeHTTP(other, httptest.NewRequest(http.MethodPost, "/kampanja/api/other", nil))
	if other.Code != 404 {
		t.Fatalf("other paths fall through, got %d", other.Code)
	}
}

func TestVolunteerAnalyticsOnlyWhenEnabledAndNotBotOrDNT(t *testing.T) {
	post := func(h *volunteerHarness, agent, dnt string) {
		h.post(`{"name":"A","email":"an@example.test","city":"Tampere"}`, func(r *http.Request) {
			r.Header.Set("User-Agent", agent)
			if dnt != "" {
				r.Header.Set("DNT", dnt)
			}
		})
	}
	desktop := "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
	h := newVolunteerHarness(true)
	post(h, desktop, "")
	if len(h.store.events) != 1 || h.store.events[0]["event"] != "volunteer_signup" || h.store.events[0]["resource_id"] != "Tampere" || h.store.events[0]["date"] != "2026-11-20" || fmt.Sprint(h.store.events[0]["hour"]) != "12" {
		t.Fatalf("%v", h.store.events)
	}
	for name, run := range map[string]func() *volunteerHarness{
		"disabled": func() *volunteerHarness { h := newVolunteerHarness(false); post(h, desktop, ""); return h },
		"dnt":      func() *volunteerHarness { h := newVolunteerHarness(true); post(h, desktop, "1"); return h },
		"bot":      func() *volunteerHarness { h := newVolunteerHarness(true); post(h, "Googlebot/2.1", ""); return h },
	} {
		if h := run(); len(h.store.events) != 0 || len(h.store.volunteers) != 1 {
			t.Errorf("%s: events %v stored %d", name, h.store.events, len(h.store.volunteers))
		}
	}
	// A missing city leaves the resource out, like record_event(resource_id=None).
	h = newVolunteerHarness(true)
	h.post(`{"name":"A","email":"nc@example.test"}`, func(r *http.Request) { r.Header.Set("User-Agent", desktop) })
	if _, ok := h.store.events[0]["resource_id"]; ok {
		t.Fatalf("%v", h.store.events[0])
	}
}

func TestCampaignVolunteerConfigIsOffByDefault(t *testing.T) {
	t.Setenv("CAMPAIGN_VOLUNTEERS_GO_ENABLED", "")
	c, err := campaignVolunteerConfigFromEnv(authBridgeConfig{})
	if err != nil || c.Enabled {
		t.Fatalf("%v %v", c, err)
	}
	next := http.NotFoundHandler()
	if got := campaignVolunteerHandlerFromEnv(next, nil, authBridgeConfig{}); got == nil {
		t.Fatal("nil handler")
	}
	t.Setenv("CAMPAIGN_VOLUNTEERS_GO_ENABLED", "true")
	if _, err := campaignVolunteerConfigFromEnv(authBridgeConfig{}); err == nil {
		t.Fatal("enabling without the auth bridge must fail closed")
	}
	if _, err := campaignVolunteerConfigFromEnv(authBridgeConfig{Enabled: true}); err != nil {
		t.Fatal(err)
	}
}

func TestVolunteerEmailValidParity(t *testing.T) {
	for email, want := range map[string]bool{
		"a@b.c": true, "a@b.cc": true, "a.b+c@d-e.fi": true, "ä@ö.fi": true, "a@b.c.": true, "a@.b.c": true,
		"@b.fi": false, "a@": false, "a@b": false, "a@b@c.fi": false, "a\u2003b@c.fi": false, "a@b.c\u001fd": false,
	} {
		if got := campaignEmailValid(email); got != want {
			t.Errorf("%q: %v, want %v", email, got, want)
		}
	}
}

// ---- confirm link ----

type fakeConfirmStore struct {
	mu      sync.Mutex
	hash    string
	plain   string
	expires time.Time
	present bool
	state   string
}

func (s *fakeConfirmStore) ConfirmUnexpired(_ context.Context, hash, plain string, now time.Time) (bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.present || (hash != s.hash && plain != s.plain) || s.expires.Before(now) {
		return false, nil
	}
	s.present, s.state = false, "confirmed"
	return true, nil
}
func (s *fakeConfirmStore) TokenKnown(_ context.Context, hash, plain string) (bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.present && (hash == s.hash || plain == s.plain), nil
}

func TestConfirmCampaignVolunteer(t *testing.T) {
	token := "AbCdEfGhIjKlMnOpQrStUv"
	now := volunteerNow
	cases := []struct {
		name  string
		store *fakeConfirmStore
		token string
		want  campaignConfirmStatus
	}{
		{"valid hashed", &fakeConfirmStore{present: true, hash: campaignDigest(token), expires: now.Add(time.Hour)}, token, campaignConfirmed},
		{"valid legacy plain", &fakeConfirmStore{present: true, plain: token, expires: now.Add(time.Hour)}, token, campaignConfirmed},
		{"expires exactly now is still valid", &fakeConfirmStore{present: true, hash: campaignDigest(token), expires: now}, token, campaignConfirmed},
		{"expired", &fakeConfirmStore{present: true, hash: campaignDigest(token), expires: now.Add(-time.Second)}, token, campaignExpired},
		{"unknown", &fakeConfirmStore{present: true, hash: campaignDigest("other"), expires: now.Add(time.Hour)}, token, campaignInvalid},
		{"already confirmed", &fakeConfirmStore{present: false, hash: campaignDigest(token)}, token, campaignInvalid},
		{"empty token", &fakeConfirmStore{}, "", campaignInvalid},
		{"token with bad characters", &fakeConfirmStore{present: true, hash: campaignDigest("a b"), expires: now.Add(time.Hour)}, "a b", campaignInvalid},
		{"token too long", &fakeConfirmStore{}, strings.Repeat("a", 257), campaignInvalid},
	}
	for _, c := range cases {
		got, err := confirmCampaignVolunteer(context.Background(), c.store, c.token, now)
		if err != nil || got != c.want {
			t.Errorf("%s: %q %v, want %q", c.name, got, err, c.want)
		}
	}
	// A plaintext stored token must never match through the hash path, and the
	// hash of the token must not be accepted as a token.
	hashed := &fakeConfirmStore{present: true, hash: campaignDigest(token), expires: now.Add(time.Hour)}
	if got, _ := confirmCampaignVolunteer(context.Background(), hashed, campaignDigest(token), now); got != campaignInvalid {
		t.Errorf("the stored hash works as a token: %q", got)
	}
}

func TestConfirmCampaignVolunteerConcurrentClicksConfirmOnce(t *testing.T) {
	token := "AbCdEfGhIjKlMnOpQrStUv"
	store := &fakeConfirmStore{present: true, hash: campaignDigest(token), expires: volunteerNow.Add(time.Hour)}
	var wg sync.WaitGroup
	var mu sync.Mutex
	won := 0
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if got, err := confirmCampaignVolunteer(context.Background(), store, token, volunteerNow); err == nil && got == campaignConfirmed {
				mu.Lock()
				won++
				mu.Unlock()
			}
		}()
	}
	wg.Wait()
	if won != 1 {
		t.Fatalf("%d confirmations", won)
	}
}

func TestConfirmCampaignVolunteerStoreErrorIsNotInvalid(t *testing.T) {
	_, err := confirmCampaignVolunteer(context.Background(), failingConfirmStore{}, "AbCdEfGhIjKlMnOpQrStUv", volunteerNow)
	if err == nil {
		t.Fatal("a database error must not read as an invalid link")
	}
}

type failingConfirmStore struct{}

func (failingConfirmStore) ConfirmUnexpired(context.Context, string, string, time.Time) (bool, error) {
	return false, errors.New("down")
}
func (failingConfirmStore) TokenKnown(context.Context, string, string) (bool, error) {
	return false, errors.New("down")
}

// With a disposable MongoDB (CAMPAIGN_VOLUNTEER_TEST_MONGO=mongodb://...) the
// real store runs: signup, hashed token, mail row, attempt counting, the
// atomic confirm, legacy plain tokens and concurrent confirmations.
func TestCampaignVolunteerAgainstMongo(t *testing.T) {
	uri := os.Getenv("CAMPAIGN_VOLUNTEER_TEST_MONGO")
	if uri == "" {
		t.Skip("CAMPAIGN_VOLUNTEER_TEST_MONGO not set")
	}
	ctx := context.Background()
	client, err := mongo.Connect(options.Client().ApplyURI(uri))
	if err != nil {
		t.Fatal(err)
	}
	defer client.Disconnect(ctx)
	db := client.Database("campaign_volunteer_test_" + strings.ReplaceAll(t.Name(), "/", "_"))
	defer db.Drop(ctx)
	store := mongoCampaignVolunteerStore{db: db}
	_, _ = db.Collection("campaign_settings").InsertOne(ctx, bson.M{"_id": "public_pages", "published": true})

	now := time.Now().UTC().Truncate(time.Millisecond)
	handler := campaignVolunteerHandler(http.NotFoundHandler(), campaignVolunteerConfig{Enabled: true}, campaignVolunteerDeps{
		store:     store,
		principal: func(context.Context, *http.Request) (Principal, error) { return Principal{}, nil },
		now:       func() time.Time { return now },
		token:     campaignToken,
		instance:  "00000000-0000-4000-8000-000000000000",
	})
	post := func(email, addr string) int {
		r := httptest.NewRequest(http.MethodPost, campaignVolunteerPath, strings.NewReader(`{"name":"Aino","email":"`+email+`","roles":["stickers"],"area":"Tampere"}`))
		r.Header.Set("Content-Type", "application/json")
		r.RemoteAddr = addr
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		return w.Code
	}
	if code := post("real@example.test", "203.0.113.7:1"); code != 201 {
		t.Fatalf("signup %d", code)
	}
	var stored struct {
		Hash    string    `bson:"confirmation_token_hash"`
		Expires time.Time `bson:"confirmation_expires"`
		Roles   []string  `bson:"roles"`
	}
	if err := db.Collection("volunteers").FindOne(ctx, bson.M{"email": "real@example.test"}).Decode(&stored); err != nil {
		t.Fatal(err)
	}
	if len(stored.Hash) != 64 || !stored.Expires.Equal(now.Add(24*time.Hour)) || len(stored.Roles) != 1 {
		t.Fatalf("%+v", stored)
	}
	var job struct {
		HTML       string   `bson:"html"`
		Status     string   `bson:"status"`
		Recipients []string `bson:"recipients"`
	}
	if err := db.Collection("email_queue").FindOne(ctx, bson.M{}).Decode(&job); err != nil || job.Status != "pending" || job.Recipients[0] != "real@example.test" {
		t.Fatalf("%v %+v", err, job)
	}
	const prefix = "https://mielenosoitukset.fi/kampanja/confirm/"
	i := strings.Index(job.HTML, prefix)
	token := job.HTML[i+len(prefix) : i+len(prefix)+22]
	if campaignDigest(token) != stored.Hash {
		t.Fatal("mailed token does not match the stored hash")
	}

	// Attempt counting: the same address twice is the limit; the third is silent.
	post("real@example.test", "203.0.113.8:1")
	post("real@example.test", "203.0.113.9:1")
	if n, _ := db.Collection("volunteers").CountDocuments(ctx, bson.M{"email": "real@example.test"}); n != 2 {
		t.Fatalf("stored %d", n)
	}

	// Concurrent confirmations: exactly one wins, then the link is spent.
	var wg sync.WaitGroup
	var mu sync.Mutex
	statuses := map[campaignConfirmStatus]int{}
	for i := 0; i < 10; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			status, err := confirmCampaignVolunteer(ctx, store, token, now.Add(time.Minute))
			mu.Lock()
			defer mu.Unlock()
			if err != nil {
				t.Error(err)
			}
			statuses[status]++
		}()
	}
	wg.Wait()
	if statuses[campaignConfirmed] != 1 || statuses[campaignInvalid] != 9 {
		t.Fatalf("%v", statuses)
	}
	var after bson.M
	_ = db.Collection("volunteers").FindOne(ctx, bson.M{"email": "real@example.test", "confirmed": true}).Decode(&after)
	for _, gone := range []string{"confirmation_token", "confirmation_token_hash", "confirmation_expires"} {
		if _, ok := after[gone]; ok {
			t.Errorf("%s not removed", gone)
		}
	}

	// Legacy plaintext token and expiry.
	_, _ = db.Collection("volunteers").InsertOne(ctx, bson.M{"email": "old@example.test", "confirmed": false, "confirmation_token": "LegacyTokenLegacyTok1", "confirmation_expires": now.Add(time.Hour)})
	_, _ = db.Collection("volunteers").InsertOne(ctx, bson.M{"email": "late@example.test", "confirmed": false, "confirmation_token_hash": campaignDigest("LateTokenLateTokenLat"), "confirmation_expires": now.Add(-time.Hour)})
	if got, _ := confirmCampaignVolunteer(ctx, store, "LateTokenLateTokenLat", now); got != campaignExpired {
		t.Errorf("late: %q", got)
	}
	if got, _ := confirmCampaignVolunteer(ctx, store, "LegacyTokenLegacyTok1", now); got != campaignConfirmed {
		t.Errorf("legacy: %q", got)
	}
	// Unconfirmed signups a week past expiry are removed by the next signup.
	if code := post("purger@example.test", "203.0.113.20:1"); code != 201 {
		t.Fatal(code)
	}
	_, _ = db.Collection("volunteers").InsertOne(ctx, bson.M{"email": "stale@example.test", "confirmed": false, "confirmation_expires": now.Add(-8 * 24 * time.Hour)})
	post("purger2@example.test", "203.0.113.21:1")
	if n, _ := db.Collection("volunteers").CountDocuments(ctx, bson.M{"email": "stale@example.test"}); n != 0 {
		t.Errorf("stale signup kept")
	}
}
