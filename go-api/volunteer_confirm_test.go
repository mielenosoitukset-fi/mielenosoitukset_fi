package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
)

// fakeVolunteers mirrors the stored rows: hash or legacy plain token, an expiry,
// and the same atomic consume as the Mongo store.
type fakeVolunteers struct {
	mu   sync.Mutex
	rows map[string]*fakeVolunteer // keyed by hash
}

type fakeVolunteer struct {
	hash, plain string
	expires     time.Time
	confirmed   bool
	live        bool // token fields still present
}

func (f *fakeVolunteers) Consume(_ context.Context, hash, plain string, now time.Time) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	for _, row := range f.rows {
		if !row.live || !(row.hash == hash || (row.plain != "" && row.plain == plain)) {
			continue
		}
		if row.expires.Before(now) {
			return volunteerExpired, nil
		}
		row.confirmed, row.live = true, false
		return volunteerConfirmed, nil
	}
	return volunteerInvalid, nil
}

func volunteerTestHandler(store volunteerConfirmStore, now time.Time, over *atomic.Bool, panicOn bool) (http.Handler, *int32) {
	var nextCalls int32
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { atomic.AddInt32(&nextCalls, 1); w.WriteHeader(418) })
	return volunteerConfirmHandler(next, store, tokenConfirmGuards{
		origin: "https://mielenosoitukset.fi",
		panic:  func(context.Context) (bool, error) { return panicOn, nil },
		allow:  func(context.Context, *http.Request) (bool, error) { return !over.Load(), nil },
		now:    func() time.Time { return now },
	}), &nextCalls
}

func volunteerPost(handler http.Handler, body string, headers map[string]string) *httptest.ResponseRecorder {
	request := httptest.NewRequest("POST", volunteerConfirmPath, strings.NewReader(body))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Origin", "https://mielenosoitukset.fi")
	for key, value := range headers {
		request.Header.Set(key, value)
	}
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	return recorder
}

func TestVolunteerConfirmOutcomesMatchFlask(t *testing.T) {
	now := time.Date(2026, 11, 20, 10, 0, 0, 0, time.UTC)
	good, expired, legacy, boundary := "goodtoken_AAAAAAAAAAAA", "expiredtoken_BBBBBBBBB", "legacytoken_CCCCCCCCC", "boundarytoken_DDDDDDDD"
	build := func() *fakeVolunteers {
		return &fakeVolunteers{rows: map[string]*fakeVolunteer{
			"g": {hash: tokenHash(good), expires: now.Add(time.Hour), live: true},
			"e": {hash: tokenHash(expired), expires: now.Add(-time.Second), live: true},
			"l": {plain: legacy, expires: now.Add(time.Hour), live: true},
			"b": {hash: tokenHash(boundary), expires: now, live: true}, // Flask: expires < now is expired, so == now is valid
		}}
	}
	cases := []struct {
		name, body, status string
		code               int
	}{
		{"valid hashed token", `{"token":"` + good + `"}`, "success", 200},
		{"valid legacy plain token", `{"token":"` + legacy + `"}`, "success", 200},
		{"expiry equal to now still valid", `{"token":"` + boundary + `"}`, "success", 200},
		{"expired link", `{"token":"` + expired + `"}`, "expired", 400},
		{"unknown token", `{"token":"nosuchtoken_EEEEEEEEEE"}`, "invalid", 400},
		{"empty token", `{"token":""}`, "invalid", 400},
		{"token with operator characters", `{"token":"x$ne"}`, "invalid", 400},
		{"token too long", `{"token":"` + strings.Repeat("a", 129) + `"}`, "invalid", 400},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			handler, _ := volunteerTestHandler(build(), now, &atomic.Bool{}, false)
			response := volunteerPost(handler, c.body, nil)
			if response.Code != c.code || !strings.Contains(response.Body.String(), `"status":"`+c.status+`"`) {
				t.Fatalf("got %d %s", response.Code, response.Body.String())
			}
			if response.Header().Get("Cache-Control") != "no-store" {
				t.Fatal("responses must be no-store")
			}
		})
	}
}

func TestVolunteerConfirmReplayAndExpiredRowKept(t *testing.T) {
	now := time.Now().UTC()
	store := &fakeVolunteers{rows: map[string]*fakeVolunteer{
		"g": {hash: tokenHash("replaytoken_FFFFFFFFFF"), expires: now.Add(time.Hour), live: true},
		"e": {hash: tokenHash("oldtoken_GGGGGGGGGGGG"), expires: now.Add(-time.Hour), live: true},
	}}
	handler, _ := volunteerTestHandler(store, now, &atomic.Bool{}, false)
	body := `{"token":"replaytoken_FFFFFFFFFF"}`
	if r := volunteerPost(handler, body, nil); r.Code != 200 {
		t.Fatalf("first use: %d", r.Code)
	}
	// Flask: a consumed token has its fields removed, so a second use is invalid.
	if r := volunteerPost(handler, body, nil); r.Code != 400 || !strings.Contains(r.Body.String(), `"invalid"`) {
		t.Fatalf("replay: %d %s", r.Code, r.Body.String())
	}
	// An expired link is left untouched, as in Flask.
	volunteerPost(handler, `{"token":"oldtoken_GGGGGGGGGGGG"}`, nil)
	if !store.rows["e"].live || store.rows["e"].confirmed {
		t.Fatal("expired row must not change")
	}
}

func TestVolunteerConfirmConcurrentClicksConfirmOnce(t *testing.T) {
	now := time.Now().UTC()
	store := &fakeVolunteers{rows: map[string]*fakeVolunteer{"g": {hash: tokenHash("racetoken_HHHHHHHHHHHH"), expires: now.Add(time.Hour), live: true}}}
	handler, _ := volunteerTestHandler(store, now, &atomic.Bool{}, false)
	var ok, bad int32
	var wg sync.WaitGroup
	for i := 0; i < 40; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if volunteerPost(handler, `{"token":"racetoken_HHHHHHHHHHHH"}`, nil).Code == 200 {
				atomic.AddInt32(&ok, 1)
			} else {
				atomic.AddInt32(&bad, 1)
			}
		}()
	}
	wg.Wait()
	if ok != 1 || bad != 39 {
		t.Fatalf("want exactly one success, got %d ok %d refused", ok, bad)
	}
}

func TestVolunteerConfirmGuards(t *testing.T) {
	now := time.Now().UTC()
	fresh := func() *fakeVolunteers {
		return &fakeVolunteers{rows: map[string]*fakeVolunteer{"g": {hash: tokenHash("guardtoken_IIIIIIIIIII"), expires: now.Add(time.Hour), live: true}}}
	}
	body := `{"token":"guardtoken_IIIIIIIIIII"}`
	t.Run("GET never writes", func(t *testing.T) {
		store := fresh()
		handler, _ := volunteerTestHandler(store, now, &atomic.Bool{}, false)
		for _, method := range []string{"GET", "HEAD", "PUT", "DELETE"} {
			request := httptest.NewRequest(method, volunteerConfirmPath, nil)
			recorder := httptest.NewRecorder()
			handler.ServeHTTP(recorder, request)
			if recorder.Code != 405 || recorder.Header().Get("Allow") != "POST" {
				t.Fatalf("%s: %d", method, recorder.Code)
			}
		}
		if store.rows["g"].confirmed {
			t.Fatal("a non-POST request wrote")
		}
	})
	t.Run("other paths pass through", func(t *testing.T) {
		handler, calls := volunteerTestHandler(fresh(), now, &atomic.Bool{}, false)
		handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("GET", "/api/home", nil))
		if *calls != 1 {
			t.Fatal("next not called")
		}
	})
	for name, headers := range map[string]map[string]string{
		"foreign origin": {"Origin": "https://evil.example"},
		"no origin":      {"Origin": ""},
		"cross-site":     {"Sec-Fetch-Site": "cross-site"},
	} {
		t.Run(name, func(t *testing.T) {
			store := fresh()
			handler, _ := volunteerTestHandler(store, now, &atomic.Bool{}, false)
			if r := volunteerPost(handler, body, headers); r.Code != 403 || store.rows["g"].confirmed {
				t.Fatalf("%d, confirmed=%v", r.Code, store.rows["g"].confirmed)
			}
		})
	}
	t.Run("www origin is the same site", func(t *testing.T) {
		handler, _ := volunteerTestHandler(fresh(), now, &atomic.Bool{}, false)
		if r := volunteerPost(handler, body, map[string]string{"Origin": "https://www.mielenosoitukset.fi"}); r.Code != 200 {
			t.Fatalf("%d", r.Code)
		}
	})
	t.Run("rate limited", func(t *testing.T) {
		over := &atomic.Bool{}
		over.Store(true)
		store := fresh()
		handler, _ := volunteerTestHandler(store, now, over, false)
		if r := volunteerPost(handler, body, nil); r.Code != 429 || r.Header().Get("Retry-After") == "" || store.rows["g"].confirmed {
			t.Fatalf("%d", r.Code)
		}
	})
	t.Run("panic switch", func(t *testing.T) {
		store := fresh()
		handler, _ := volunteerTestHandler(store, now, &atomic.Bool{}, true)
		if r := volunteerPost(handler, body, nil); r.Code != 503 || store.rows["g"].confirmed {
			t.Fatalf("%d", r.Code)
		}
	})
	for name, request := range map[string]struct{ contentType, body string }{
		"not JSON":      {"text/plain", body},
		"unknown field": {"application/json", `{"token":"guardtoken_IIIIIIIIIII","admin":true}`},
		"trailing data": {"application/json", body + body},
		"malformed":     {"application/json", `{"token":`},
	} {
		t.Run("rejects "+name, func(t *testing.T) {
			store := fresh()
			handler, _ := volunteerTestHandler(store, now, &atomic.Bool{}, false)
			r := volunteerPost(handler, request.body, map[string]string{"Content-Type": request.contentType})
			if r.Code != 400 || store.rows["g"].confirmed {
				t.Fatalf("%d", r.Code)
			}
		})
	}
	t.Run("store failure is 503 not a state", func(t *testing.T) {
		handler, _ := volunteerTestHandler(failingVolunteers{}, now, &atomic.Bool{}, false)
		if r := volunteerPost(handler, body, nil); r.Code != 503 || strings.Contains(r.Body.String(), `"status"`) {
			t.Fatalf("%d %s", r.Code, r.Body.String())
		}
	})
}

type failingVolunteers struct{}

func (failingVolunteers) Consume(context.Context, string, string, time.Time) (string, error) {
	return "", context.DeadlineExceeded
}

func TestVolunteerConfirmOffByDefault(t *testing.T) {
	t.Setenv("VOLUNTEER_CONFIRM_GO_ENABLED", "")
	next := http.NewServeMux()
	if volunteerConfirmFromEnv(next, nil, submissionConfig{Origin: "https://mielenosoitukset.fi", Secret: "s"}) != http.Handler(next) {
		t.Fatal("flag off must return the next handler unchanged")
	}
	t.Setenv("VOLUNTEER_CONFIRM_GO_ENABLED", "true")
	if volunteerConfirmFromEnv(next, nil, submissionConfig{}) != http.Handler(next) {
		t.Fatal("without public submissions the route stays off")
	}
}

// Needs a disposable MongoDB (REMINDER_TEST_MONGO): the real atomic update.
func TestVolunteerConfirmMongoStore(t *testing.T) {
	db := reminderTestDB(t)
	ctx := context.Background()
	now := time.Now().UTC().Truncate(time.Millisecond)
	_, err := db.Collection("volunteers").InsertMany(ctx, []any{
		bson.M{"email": "a@example.com", "confirmed": false, "confirmation_token_hash": tokenHash("mongotoken_JJJJJJJJJJJ"), "confirmation_expires": now.Add(time.Hour)},
		bson.M{"email": "b@example.com", "confirmed": false, "confirmation_token": "legacy_KKKKKKKKKKKKKK", "confirmation_expires": now.Add(time.Hour)},
		bson.M{"email": "c@example.com", "confirmed": false, "confirmation_token_hash": tokenHash("oldmongo_LLLLLLLLLLLLL"), "confirmation_expires": now.Add(-time.Hour)},
		bson.M{"email": "d@example.com", "confirmed": false, "confirmation_token_hash": tokenHash("noexpiry_NNNNNNNNNNNNN")}, // Flask: KeyError, a 500
	})
	if err != nil {
		t.Fatal(err)
	}
	store := mongoVolunteerConfirm{db}
	for _, c := range []struct{ token, want string }{
		{"mongotoken_JJJJJJJJJJJ", volunteerConfirmed}, {"mongotoken_JJJJJJJJJJJ", volunteerInvalid},
		{"legacy_KKKKKKKKKKKKKK", volunteerConfirmed}, {"oldmongo_LLLLLLLLLLLLL", volunteerExpired}, {"unknown_MMMMMMMMMMMMMM", volunteerInvalid}, {"noexpiry_NNNNNNNNNNNNN", volunteerExpired},
	} {
		got, err := store.Consume(ctx, tokenHash(c.token), c.token, now)
		if err != nil || got != c.want {
			t.Fatalf("%s: got %q %v want %q", c.token, got, err, c.want)
		}
	}
	var row bson.M
	if err := db.Collection("volunteers").FindOne(ctx, bson.M{"email": "a@example.com"}).Decode(&row); err != nil {
		t.Fatal(err)
	}
	if row["confirmed"] != true || row["confirmation_token_hash"] != nil || row["confirmation_expires"] != nil {
		t.Fatalf("row not consumed like Flask: %v", row)
	}
	if err := db.Collection("volunteers").FindOne(ctx, bson.M{"email": "c@example.com"}).Decode(&row); err != nil || row["confirmed"] != false || row["confirmation_token_hash"] == nil {
		t.Fatalf("expired row changed: %v %v", row, err)
	}
}
