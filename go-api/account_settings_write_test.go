package main

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
)

type fakeAccountWriteStore struct {
	bios    []*string
	owners  []bson.ObjectID
	audits  []bson.M
	missing bool
	err     error
}

func (f *fakeAccountWriteStore) SetBio(_ context.Context, owner bson.ObjectID, bio *string) (bool, error) {
	if f.err != nil {
		return false, f.err
	}
	f.owners = append(f.owners, owner)
	f.bios = append(f.bios, bio)
	return !f.missing, nil
}

func (f *fakeAccountWriteStore) Audit(_ context.Context, doc bson.M) error {
	f.audits = append(f.audits, doc)
	return nil
}

type accountWriteRig struct {
	h      http.Handler
	store  *fakeAccountWriteStore
	writes *accountWrites
}

func newAccountWriteRig(t *testing.T, enabled bool) accountWriteRig {
	t.Helper()
	config, _ := fakeFlask(t, flaskAnswer)
	resolver, _ := newSessionResolver(config)
	store := &fakeAccountWriteStore{}
	var writes *accountWrites
	if enabled {
		writes = newAccountWrites(store, "https://example.test")
	}
	h := accountSettingsHandler(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(299) }),
		&fakeAccountSettingsStore{data: fullAccount()}, resolver, writes)
	return accountWriteRig{h, store, writes}
}

func (g accountWriteRig) post(body string, headers ...string) *httptest.ResponseRecorder {
	r := httptest.NewRequest("POST", accountProfilePath, strings.NewReader(body))
	r.Header.Set("Cookie", "session=member")
	r.Header.Set("Sec-Fetch-Site", "same-origin")
	r.Header.Set("Origin", "https://example.test")
	r.Header.Set("Content-Type", "application/json")
	if g.writes != nil {
		r.Header.Set(developerCSRFHeader, g.writes.guard.csrfToken(testActor))
	}
	for i := 0; i+1 < len(headers); i += 2 {
		if headers[i+1] == "-" {
			r.Header.Del(headers[i])
		} else {
			r.Header.Set(headers[i], headers[i+1])
		}
	}
	w := httptest.NewRecorder()
	g.h.ServeHTTP(w, r)
	return w
}

func TestAccountBioWriteOffPassesThrough(t *testing.T) {
	g := newAccountWriteRig(t, false)
	if w := g.post(`{"bio":"x"}`); w.Code != 299 || len(g.store.bios) != 0 {
		t.Fatal(w.Code)
	}
	w := get(g.h, accountSettingsPath, "session=member")
	if strings.Contains(w.Body.String(), "csrf") || strings.Contains(w.Body.String(), `"writes"`) {
		t.Fatal("no csrf token while writes are off")
	}
}

func TestAccountReadAdvertisesWritesAndCSRF(t *testing.T) {
	g := newAccountWriteRig(t, true)
	w := get(g.h, accountSettingsPath, "session=member")
	if !strings.Contains(w.Body.String(), `"writes":true`) || !strings.Contains(w.Body.String(), `"csrf":"`) {
		t.Fatal(w.Body.String())
	}
}

func TestAccountBioWriteGuards(t *testing.T) {
	g := newAccountWriteRig(t, true)
	cases := []struct {
		name string
		want int
		h    []string
	}{
		{"cross site", 403, []string{"Sec-Fetch-Site", "cross-site"}},
		{"missing fetch metadata", 403, []string{"Sec-Fetch-Site", "-"}},
		{"foreign origin", 403, []string{"Origin", "https://evil.test"}},
		{"no csrf", 403, []string{developerCSRFHeader, "-"}},
		{"bad csrf", 403, []string{developerCSRFHeader, "1.2.3"}},
		{"form post", 415, []string{"Content-Type", "application/x-www-form-urlencoded"}},
	}
	for _, c := range cases {
		if w := g.post(`{"bio":"x"}`, c.h...); w.Code != c.want {
			t.Errorf("%s: got %d", c.name, w.Code)
		}
	}
	for _, cookie := range []string{"", "session=guest", "session=error"} {
		r := httptest.NewRequest("POST", accountProfilePath, strings.NewReader(`{"bio":"x"}`))
		if cookie != "" {
			r.Header.Set("Cookie", cookie)
		}
		w := httptest.NewRecorder()
		g.h.ServeHTTP(w, r)
		if w.Code != 401 && w.Code != 503 {
			t.Errorf("%q: got %d", cookie, w.Code)
		}
	}
	if len(g.store.bios) != 0 {
		t.Fatal("rejected requests must not write")
	}
	r := httptest.NewRequest("GET", accountProfilePath, nil)
	w := httptest.NewRecorder()
	g.h.ServeHTTP(w, r)
	if w.Code != 405 || w.Header().Get("Allow") != "POST" {
		t.Fatal(w.Code)
	}
}

func TestAccountCSRFIsBoundToAccount(t *testing.T) {
	g := newAccountWriteRig(t, true)
	other := g.writes.guard.csrfToken(testOrg)
	if w := g.post(`{"bio":"x"}`, developerCSRFHeader, other); w.Code != 403 {
		t.Fatal("token of another account accepted", w.Code)
	}
	expired := g.writes.guard.csrfToken(testActor)
	g.writes.guard.now = func() time.Time { return time.Now().Add(developerCSRFLife + time.Minute) }
	if w := g.post(`{"bio":"x"}`, developerCSRFHeader, expired); w.Code != 403 {
		t.Fatal("expired token accepted", w.Code)
	}
}

func TestAccountBioWriteStoresTrimmedTextForTheBridgeActor(t *testing.T) {
	g := newAccountWriteRig(t, true)
	w := g.post("{\"bio\":\"  Hei\\r\\nmaailma\\tä  \"}")
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"status":"success"`) || w.Header().Get("Cache-Control") != "no-store" {
		t.Fatal(w.Code, w.Body.String())
	}
	if len(g.store.bios) != 1 || *g.store.bios[0] != "Hei\nmaailma\tä" || g.store.owners[0].Hex() != testActor {
		t.Fatalf("stored %v for %v", g.store.bios, g.store.owners)
	}
	if len(g.store.audits) != 1 || g.store.audits[0]["action"] != "profile bio updated" || g.store.audits[0]["source"] != "go-api" {
		t.Fatal(g.store.audits)
	}
	for k, v := range g.store.audits[0] {
		if s, ok := v.(string); ok && strings.Contains(s, "Hei") {
			t.Errorf("audit field %s carries the bio text", k)
		}
	}
}

func TestAccountBioEmptyClears(t *testing.T) {
	g := newAccountWriteRig(t, true)
	w := g.post(`{"bio":"   "}`)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"bio":null`) || len(g.store.bios) != 1 || g.store.bios[0] != nil {
		t.Fatal(w.Code, w.Body.String(), g.store.bios)
	}
}

func TestAccountBioValidation(t *testing.T) {
	g := newAccountWriteRig(t, true)
	bad := []string{`{}`, `{"bio":null}`, `{"bio":5}`, `[]`, `nope`, ``,
		`{"bio":"` + strings.Repeat("a", accountBioMax+1) + `"}`,
		`{"bio":"a\u0000b"}`, `{"bio":"a\u001bb"}`, `{"bio":"a\u0085b"}`,
		`{"bio":"` + strings.Repeat("a", accountWriteBody) + `"}`}
	for _, body := range bad {
		if w := g.post(body); w.Code != 400 {
			t.Errorf("%.40q: got %d", body, w.Code)
		}
	}
	if len(g.store.bios) != 0 {
		t.Fatal("invalid input wrote")
	}
	if w := g.post(`{"bio":"` + strings.Repeat("ä", accountBioMax) + `"}`); w.Code != 200 {
		t.Fatal("exactly 1000 characters must pass", w.Code)
	}
}

func TestAccountBioStoreFailureAndMissingAccount(t *testing.T) {
	g := newAccountWriteRig(t, true)
	g.store.err = errors.New("db down")
	if w := g.post(`{"bio":"x"}`); w.Code != 503 {
		t.Fatal(w.Code)
	}
	g.store.err, g.store.missing = nil, true
	if w := g.post(`{"bio":"x"}`); w.Code != 404 || len(g.store.audits) != 0 {
		t.Fatal(w.Code)
	}
}

func TestAccountBioWritesAreRateLimited(t *testing.T) {
	g := newAccountWriteRig(t, true)
	var last int
	for i := 0; i < developerWritesPerMin+1; i++ {
		last = g.post(`{"bio":"x"}`).Code
	}
	if last != 429 {
		t.Fatal("expected 429, got", last)
	}
}

func TestAccountWriteStoreOnlyWritesBio(t *testing.T) {
	var store accountWriteStore = &fakeAccountWriteStore{}
	if _, ok := store.(interface {
		Save(context.Context, bson.ObjectID) error
	}); ok {
		t.Fatal("unexpected broad write method")
	}
}
