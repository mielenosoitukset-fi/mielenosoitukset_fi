package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
)

// Tokens in testdata are signed by PyJWT, the library Flask uses, and carry what
// Flask's verify_confirmation_token answers for each (regenerate with
// testdata/make_email_confirm_tokens.py). That keeps the Go verifier honest.
type emailTokenFixture struct {
	Key        string            `json:"key"`
	Now        int64             `json:"now"`
	Tokens     map[string]string `json:"tokens"`
	FlaskEmail map[string]any    `json:"flask_email"`
}

func loadEmailTokens(t *testing.T) emailTokenFixture {
	t.Helper()
	raw, err := os.ReadFile("testdata/email_confirm_tokens.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture emailTokenFixture
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture
}

func TestEmailConfirmTokenVerifierMatchesPyJWT(t *testing.T) {
	fixture := loadEmailTokens(t)
	now := time.Unix(fixture.Now, 0).UTC()
	// Deliberately stricter than Flask: a token without exp is never minted by Flask,
	// and a non-string address cannot come from it either.
	stricter := map[string]bool{"no_exp": true, "email_not_string": true}
	for name, token := range fixture.Tokens {
		t.Run(name, func(t *testing.T) {
			email, verdict := verifyFlaskConfirmationToken(token, []byte(fixture.Key), now)
			want, flaskAccepts := fixture.FlaskEmail[name].(string)
			if stricter[name] {
				if verdict == confirmTokenValid {
					t.Fatalf("stricter case accepted: %q", email)
				}
				return
			}
			if flaskAccepts != (verdict == confirmTokenValid) || (flaskAccepts && email != want) {
				t.Fatalf("Flask gives %v, Go gives %q valid=%v", fixture.FlaskEmail[name], email, verdict == confirmTokenValid)
			}
		})
	}
	if _, v := verifyFlaskConfirmationToken(fixture.Tokens["valid"], nil, now); v == confirmTokenValid {
		t.Fatal("no key must never verify")
	}
	if _, v := verifyFlaskConfirmationToken(fixture.Tokens["valid"], []byte("short"), now); v == confirmTokenValid {
		t.Fatal("wrong key must not verify")
	}
	for _, junk := range []string{"", "a", "a.b", "a.b.c.d", "!!.!!.!!", strings.Repeat("a", 1001) + ".b.c"} {
		if _, v := verifyFlaskConfirmationToken(junk, []byte(fixture.Key), now); v == confirmTokenValid {
			t.Fatalf("junk accepted: %q", junk)
		}
	}
}

type fakeAccounts struct {
	mu        sync.Mutex
	confirmed map[string]bool // canonical address -> confirmed
	writes    int
}

func (f *fakeAccounts) Confirm(_ context.Context, email string) (bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if _, ok := f.confirmed[email]; !ok {
		return false, nil
	}
	f.confirmed[email] = true
	f.writes++
	return true, nil
}

func emailTestHandler(store emailConfirmStore, fixture emailTokenFixture, over *atomic.Bool, panicOn bool) http.Handler {
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(418) })
	return emailConfirmHandler(next, store, []byte(fixture.Key), tokenConfirmGuards{
		origin: "https://mielenosoitukset.fi",
		panic:  func(context.Context) (bool, error) { return panicOn, nil },
		allow:  func(context.Context, *http.Request) (bool, error) { return !over.Load(), nil },
		now:    func() time.Time { return time.Unix(fixture.Now, 0) },
	})
}

func emailPost(handler http.Handler, body string, headers map[string]string) *httptest.ResponseRecorder {
	request := httptest.NewRequest("POST", emailConfirmPath, strings.NewReader(body))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Origin", "https://mielenosoitukset.fi")
	for key, value := range headers {
		request.Header.Set(key, value)
	}
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	return recorder
}

func TestEmailConfirmOutcomes(t *testing.T) {
	fixture := loadEmailTokens(t)
	token := func(name string) string { return `{"token":"` + fixture.Tokens[name] + `"}` }
	cases := []struct {
		name, body, status string
		code               int
		confirms           bool
	}{
		{"valid, mixed-case address", token("valid"), "confirmed", 200, true},
		{"legacy token without purpose", token("legacy_no_purpose"), "confirmed", 200, true},
		{"inside the leeway", token("inside_leeway"), "confirmed", 200, true},
		{"expired", token("expired"), "invalid", 400, false},
		{"outside the leeway", token("outside_leeway"), "invalid", 400, false},
		{"a password reset token is no confirmation", token("password_reset_purpose"), "invalid", 400, false},
		{"forged with another key", token("wrong_key"), "invalid", 400, false},
		{"alg none", token("alg_none"), "invalid", 400, false},
		{"tampered payload", token("tampered_payload"), "invalid", 400, false},
		{"signed address has no account", token("unknown_user"), "user-not-found", 404, false},
		{"signed address is only whitespace", token("whitespace_email"), "invalid", 400, false},
		{"garbage", `{"token":"not.a.jwt!"}`, "invalid", 400, false},
		{"empty token", `{"token":""}`, "invalid", 400, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			store := &fakeAccounts{confirmed: map[string]bool{"maija@example.com": false}}
			response := emailPost(emailTestHandler(store, fixture, &atomic.Bool{}, false), c.body, nil)
			if response.Code != c.code || !strings.Contains(response.Body.String(), `"status":"`+c.status+`"`) {
				t.Fatalf("got %d %s", response.Code, response.Body.String())
			}
			if store.confirmed["maija@example.com"] != c.confirms {
				t.Fatalf("confirmed=%v, want %v", store.confirmed["maija@example.com"], c.confirms)
			}
			if response.Header().Get("Cache-Control") != "no-store" {
				t.Fatal("responses must be no-store")
			}
		})
	}
}

func TestEmailConfirmReplayIsHarmlessAndConcurrent(t *testing.T) {
	fixture := loadEmailTokens(t)
	store := &fakeAccounts{confirmed: map[string]bool{"maija@example.com": false}}
	handler := emailTestHandler(store, fixture, &atomic.Bool{}, false)
	body := `{"token":"` + fixture.Tokens["valid"] + `"}`
	var wg sync.WaitGroup
	var failures int32
	for i := 0; i < 30; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if emailPost(handler, body, nil).Code != 200 {
				atomic.AddInt32(&failures, 1)
			}
		}()
	}
	wg.Wait()
	// Flask answers success on every replay inside the hour; the flag just stays true.
	if failures != 0 || !store.confirmed["maija@example.com"] {
		t.Fatalf("%d failures, confirmed=%v", failures, store.confirmed["maija@example.com"])
	}
}

func TestEmailConfirmGuards(t *testing.T) {
	fixture := loadEmailTokens(t)
	body := `{"token":"` + fixture.Tokens["valid"] + `"}`
	fresh := func() *fakeAccounts { return &fakeAccounts{confirmed: map[string]bool{"maija@example.com": false}} }
	t.Run("GET and other methods never write", func(t *testing.T) {
		store := fresh()
		handler := emailTestHandler(store, fixture, &atomic.Bool{}, false)
		for _, method := range []string{"GET", "HEAD", "PUT", "DELETE"} {
			recorder := httptest.NewRecorder()
			handler.ServeHTTP(recorder, httptest.NewRequest(method, emailConfirmPath, nil))
			if recorder.Code != 405 || recorder.Header().Get("Allow") != "POST" {
				t.Fatalf("%s: %d", method, recorder.Code)
			}
		}
		if store.confirmed["maija@example.com"] {
			t.Fatal("a non-POST request wrote")
		}
	})
	t.Run("a token in the query string is ignored", func(t *testing.T) {
		store := fresh()
		request := httptest.NewRequest("POST", emailConfirmPath+"?token="+fixture.Tokens["valid"], strings.NewReader(`{"token":""}`))
		request.Header.Set("Content-Type", "application/json")
		request.Header.Set("Origin", "https://mielenosoitukset.fi")
		recorder := httptest.NewRecorder()
		emailTestHandler(store, fixture, &atomic.Bool{}, false).ServeHTTP(recorder, request)
		if recorder.Code != 400 || store.confirmed["maija@example.com"] {
			t.Fatalf("%d", recorder.Code)
		}
	})
	for name, headers := range map[string]map[string]string{
		"foreign origin": {"Origin": "https://evil.example"}, "no origin": {"Origin": ""}, "cross-site": {"Sec-Fetch-Site": "cross-site"},
	} {
		t.Run(name, func(t *testing.T) {
			store := fresh()
			if r := emailPost(emailTestHandler(store, fixture, &atomic.Bool{}, false), body, headers); r.Code != 403 || store.confirmed["maija@example.com"] {
				t.Fatalf("%d", r.Code)
			}
		})
	}
	t.Run("rate limited", func(t *testing.T) {
		over := &atomic.Bool{}
		over.Store(true)
		store := fresh()
		if r := emailPost(emailTestHandler(store, fixture, over, false), body, nil); r.Code != 429 || store.confirmed["maija@example.com"] {
			t.Fatalf("%d", r.Code)
		}
	})
	t.Run("panic switch", func(t *testing.T) {
		store := fresh()
		if r := emailPost(emailTestHandler(store, fixture, &atomic.Bool{}, true), body, nil); r.Code != 503 || store.confirmed["maija@example.com"] {
			t.Fatalf("%d", r.Code)
		}
	})
	for name, request := range map[string]struct{ contentType, body string }{
		"not JSON": {"text/plain", body}, "unknown field": {"application/json", `{"token":"x","admin":true}`},
		"trailing data": {"application/json", body + body}, "oversized": {"application/json", `{"token":"` + strings.Repeat("a", 5000) + `"}`},
	} {
		t.Run("rejects "+name, func(t *testing.T) {
			store := fresh()
			r := emailPost(emailTestHandler(store, fixture, &atomic.Bool{}, false), request.body, map[string]string{"Content-Type": request.contentType})
			if r.Code != 400 || store.confirmed["maija@example.com"] {
				t.Fatalf("%d", r.Code)
			}
		})
	}
	t.Run("store failure is 503, not a verdict", func(t *testing.T) {
		r := emailPost(emailTestHandler(failingAccounts{}, fixture, &atomic.Bool{}, false), body, nil)
		if r.Code != 503 || strings.Contains(r.Body.String(), `"status"`) {
			t.Fatalf("%d %s", r.Code, r.Body.String())
		}
	})
	t.Run("other paths pass through", func(t *testing.T) {
		recorder := httptest.NewRecorder()
		emailTestHandler(fresh(), fixture, &atomic.Bool{}, false).ServeHTTP(recorder, httptest.NewRequest("GET", "/api/home", nil))
		if recorder.Code != 418 {
			t.Fatal("next not called")
		}
	})
}

type failingAccounts struct{}

func (failingAccounts) Confirm(context.Context, string) (bool, error) {
	return false, context.DeadlineExceeded
}

func TestEmailConfirmOffByDefault(t *testing.T) {
	next := http.NewServeMux()
	config := submissionConfig{Origin: "https://mielenosoitukset.fi", Secret: "s"}
	long := strings.Repeat("k", 40)
	for name, env := range map[string][2]string{
		"flag unset": {"", long}, "flag true but no key": {"true", ""}, "flag true but short key": {"true", "short"},
	} {
		t.Run(name, func(t *testing.T) {
			t.Setenv("EMAIL_CONFIRM_GO_ENABLED", env[0])
			t.Setenv("EMAIL_CONFIRM_JWT_KEY", env[1])
			if emailConfirmFromEnv(next, nil, config) != http.Handler(next) {
				t.Fatal("must return the next handler unchanged")
			}
		})
	}
	t.Setenv("EMAIL_CONFIRM_GO_ENABLED", "true")
	t.Setenv("EMAIL_CONFIRM_JWT_KEY", long)
	if emailConfirmFromEnv(next, nil, submissionConfig{}) != http.Handler(next) {
		t.Fatal("without public submissions the route stays off")
	}
}

func TestNormalizeAccountEmailMatchesFlask(t *testing.T) {
	for in, want := range map[string]string{" Maija@Example.COM ": "maija@example.com", "A+b@x.fi": "a+b@x.fi", "": ""} {
		if got := normalizeAccountEmail(in); got != want {
			t.Fatalf("%q: %q", in, got)
		}
	}
}

// Needs a disposable MongoDB (REMINDER_TEST_MONGO): the real lookup and update.
func TestEmailConfirmMongoStore(t *testing.T) {
	db := reminderTestDB(t)
	ctx := context.Background()
	_, err := db.Collection("users").InsertMany(ctx, []any{
		bson.M{"username": "canonical", "email": "Maija@Example.com", "email_canonical": "maija@example.com", "confirmed": false},
		bson.M{"username": "legacy", "email": "Legacy.User+tag@Example.com", "confirmed": false},
		bson.M{"username": "lookalike", "email": "legacyXuser+tag@example.com", "confirmed": false},
		bson.M{"username": "other", "email": "other@example.com", "email_canonical": "other@example.com", "confirmed": false},
	})
	if err != nil {
		t.Fatal(err)
	}
	store := mongoEmailConfirm{db}
	for _, c := range []struct {
		email string
		found bool
	}{{"maija@example.com", true}, {"maija@example.com", true}, {"legacy.user+tag@example.com", true}, {"nobody@example.com", false}, {".*", false}} {
		found, err := store.Confirm(ctx, c.email)
		if err != nil || found != c.found {
			t.Fatalf("%s: %v %v", c.email, found, err)
		}
	}
	confirmed := map[string]bool{}
	cursor, _ := db.Collection("users").Find(ctx, bson.M{})
	for cursor.Next(ctx) {
		var row struct {
			Username  string `bson:"username"`
			Confirmed bool   `bson:"confirmed"`
		}
		_ = cursor.Decode(&row)
		confirmed[row.Username] = row.Confirmed
	}
	// Only the matching accounts changed: a regex-looking address matches nothing,
	// and "+" and "." are literal in the legacy lookup.
	if !confirmed["canonical"] || !confirmed["legacy"] || confirmed["lookalike"] || confirmed["other"] {
		t.Fatalf("unexpected state %v", confirmed)
	}
}

// The store must never be asked for an empty address, whatever the token says.
func TestEmailConfirmNeverQueriesEmptyAddress(t *testing.T) {
	fixture := loadEmailTokens(t)
	store := &fakeAccounts{confirmed: map[string]bool{"": false}}
	r := emailPost(emailTestHandler(store, fixture, &atomic.Bool{}, false), `{"token":"`+fixture.Tokens["whitespace_email"]+`"}`, nil)
	if r.Code != 400 || store.writes != 0 || store.confirmed[""] {
		t.Fatalf("%d writes=%d", r.Code, store.writes)
	}
}
