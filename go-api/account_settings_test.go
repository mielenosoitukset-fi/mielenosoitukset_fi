package main

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"go.mongodb.org/mongo-driver/v2/bson"
)

type fakeAccountSettingsStore struct {
	data  *accountSettingsData
	err   error
	asked []bson.ObjectID
}

func (f *fakeAccountSettingsStore) Load(_ context.Context, owner bson.ObjectID) (*accountSettingsData, error) {
	f.asked = append(f.asked, owner)
	return f.data, f.err
}

func accountSettingsRig(t *testing.T, store *fakeAccountSettingsStore) http.Handler {
	config, _ := fakeFlask(t, flaskAnswer)
	resolver, _ := newSessionResolver(config)
	return accountSettingsHandler(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(299) }), store, resolver, nil)
}

func fullAccount() *accountSettingsData {
	city, lang, bio, dark := "Helsinki", "fi", "Hei", true
	return &accountSettingsData{Username: "maija", Displayname: "Maija", Bio: &bio, MFAEnabled: true,
		Settings: &accountGeneral{DisplayName: "Maija M", City: &city, Language: &lang, DarkMode: &dark},
		Devices:  []accountMFADevice{{ID: testOrg, Name: "Puhelin", CreatedAt: "2026-01-02T03:04:05Z"}},
		Passkeys: []accountPasskey{{ID: testOrg, Name: "Avain", Transports: []string{"usb"}}},
		Logins:   []accountLogin{{Time: "2026-01-02T03:04:05Z", IP: "192.0.2.1", UA: "Firefox", OK: false, Reason: "wrong code"}}}
}

func TestAccountSettingsRequireSignIn(t *testing.T) {
	store := &fakeAccountSettingsStore{data: fullAccount()}
	h := accountSettingsRig(t, store)
	for _, cookie := range []string{"", "session=guest"} {
		if w := get(h, accountSettingsPath, cookie); w.Code != 401 {
			t.Errorf("cookie %q: got %d", cookie, w.Code)
		}
	}
	if w := get(h, accountSettingsPath, "session=error"); w.Code != 503 {
		t.Fatalf("session failure must fail closed, got %d", w.Code)
	}
	if len(store.asked) != 0 {
		t.Fatal("the store must not be read for an anonymous request")
	}
	if w := get(h, "/other", ""); w.Code != 299 {
		t.Fatal("other paths pass through")
	}
}

func TestAccountSettingsAnswerHasNoSecretsAndUsesTheBridgeActor(t *testing.T) {
	store := &fakeAccountSettingsStore{data: fullAccount()}
	h := accountSettingsRig(t, store)
	w := get(h, accountSettingsPath, "session=member", "Sec-Fetch-Site", "same-origin")
	body := w.Body.String()
	if w.Code != 200 || !strings.Contains(body, `"Puhelin"`) || !strings.Contains(body, `"login_history"`) || !strings.Contains(body, `"Helsinki"`) {
		t.Fatal(w.Code, body)
	}
	for _, forbidden := range []string{"secret", "password", "email", "credential", "public_key", "user_id", testActor} {
		if strings.Contains(strings.ToLower(body), forbidden) {
			t.Errorf("answer contains %q: %s", forbidden, body)
		}
	}
	if len(store.asked) != 1 || store.asked[0].Hex() != testActor {
		t.Fatalf("store asked for %v", store.asked)
	}
	for header, want := range map[string]string{"Cache-Control": "no-store", "Vary": "Cookie", "X-Content-Type-Options": "nosniff"} {
		if w.Header().Get(header) != want {
			t.Errorf("%s = %q", header, w.Header().Get(header))
		}
	}
	if !strings.Contains(body, `"Akaa"`) {
		t.Error("the city list is missing")
	}
}

func TestAccountSettingsDefaultsWithoutASettingsDocument(t *testing.T) {
	store := &fakeAccountSettingsStore{data: &accountSettingsData{Username: "maija", Displayname: "", Devices: []accountMFADevice{},
		Passkeys: []accountPasskey{}, Logins: []accountLogin{}}}
	h := accountSettingsRig(t, store)
	w := get(h, accountSettingsPath, "session=member")
	body := w.Body.String()
	if w.Code != 200 || !strings.Contains(body, `"general":{"display_name":"maija","city":null,"language":null,"dark_mode":null}`) {
		t.Fatal(w.Code, body)
	}
	if !strings.Contains(body, `"profile":{"bio":null,"profile_picture":null}`) || !strings.Contains(body, `"mfa":{"devices":[],"enabled":false}`) {
		t.Fatal(body)
	}
}

func TestAccountSettingsStoreIsReadOnlyByType(t *testing.T) {
	// The store interface has one read method; this keeps a write from being added to it unnoticed.
	var store accountSettingsStore = &fakeAccountSettingsStore{}
	if _, ok := store.(interface {
		Save(context.Context, bson.ObjectID) error
	}); ok {
		t.Fatal("the settings store must not write")
	}
}

func TestAccountSettingsMethodAndCrossSite(t *testing.T) {
	h := accountSettingsRig(t, &fakeAccountSettingsStore{data: fullAccount()})
	if w := get(h, accountSettingsPath, "session=member", "Sec-Fetch-Site", "cross-site"); w.Code != 403 {
		t.Fatal(w.Code)
	}
	r := httptest.NewRequest("POST", accountSettingsPath, nil)
	r.Header.Set("Cookie", "session=member")
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 405 || w.Header().Get("Allow") != "GET, HEAD" {
		t.Fatal(w.Code, w.Header())
	}
	r = httptest.NewRequest("HEAD", accountSettingsPath, nil)
	r.Header.Set("Cookie", "session=member")
	w = httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 200 {
		t.Fatal("HEAD", w.Code)
	}
}

func TestAccountSettingsStoreErrorAndMissingAccountAre503(t *testing.T) {
	for name, store := range map[string]*fakeAccountSettingsStore{"error": {err: errors.New("db down")}, "missing": {}} {
		h := accountSettingsRig(t, store)
		if w := get(h, accountSettingsPath, "session=member"); w.Code != 503 {
			t.Errorf("%s: %d", name, w.Code)
		}
	}
}

func TestAccountSettingsFlagOffPassesThrough(t *testing.T) {
	t.Setenv("ACCOUNT_SETTINGS_READS_ENABLED", "")
	config, _ := fakeFlask(t, flaskAnswer)
	resolver, _ := newSessionResolver(config)
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(299) })
	h := accountSettingsReadHandler(next, nil, resolver, "")
	if w := get(h, accountSettingsPath, "session=member"); w.Code != 299 {
		t.Fatalf("flag off must leave the route to the next handler, got %d", w.Code)
	}
	if h2 := accountSettingsReadHandler(next, nil, nil, ""); h2 == nil {
		t.Fatal("no resolver (bridge off) must still return a handler")
	}
}

func TestAccountSettingsTruncatesLongText(t *testing.T) {
	if got := truncateRunes(strings.Repeat("ä", 200), 120); len([]rune(got)) != 120 {
		t.Fatal(len([]rune(got)))
	}
}
