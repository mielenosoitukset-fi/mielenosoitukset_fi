package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"go.mongodb.org/mongo-driver/v2/bson"
)

const (
	otherActor = "111111111111111111111111"
	ghostActor = "222222222222222222222222"
)

type fakeProfileStore struct {
	users    map[string]*profileUser
	accounts map[string]*profileAccount
	people   map[string]profileSummary
	err      error
	listErr  error
}

func (f *fakeProfileStore) User(_ context.Context, username string) (*profileUser, error) {
	return f.users[username], f.err
}
func (f *fakeProfileStore) Account(_ context.Context, id bson.ObjectID) (*profileAccount, error) {
	return f.accounts[id.Hex()], f.err
}
func (f *fakeProfileStore) Summaries(_ context.Context, ids []string) ([]profileSummary, error) {
	out := []profileSummary{}
	for _, id := range ids {
		if person, ok := f.people[id]; ok {
			out = append(out, person)
		}
	}
	return out, f.err
}
func (f *fakeProfileStore) Organizations(context.Context, []string) ([]profileOrg, error) {
	return []profileOrg{{ID: testOrg, Name: "Yhdistys", City: "Turku"}}, f.listErr
}
func (f *fakeProfileStore) Recurring(context.Context, []string) ([]profileRecurring, error) {
	return []profileRecurring{{ID: testOrg, Title: "Viikoittainen", Date: "joka viikko", City: "Turku"}}, f.listErr
}

func profileID24(t *testing.T, hex string) bson.ObjectID {
	t.Helper()
	id, err := bson.ObjectIDFromHex(hex)
	if err != nil {
		t.Fatal(err)
	}
	return id
}

func newProfileStore(t *testing.T) *fakeProfileStore {
	owner := profileID24(t, testActor)
	return &fakeProfileStore{
		users: map[string]*profileUser{
			"maija": {ID: owner, Username: "maija", Displayname: "Maija", Bio: "Hei", AdminBadge: true,
				FollowersCount: 3, FollowingCount: 1, Followers: []string{otherActor, ghostActor}, Following: []string{otherActor},
				FollowedOrgs: []string{testOrg}, FollowedRecurring: []string{testOrg}, FriendRequests: []string{otherActor}},
			"pekka": {ID: profileID24(t, otherActor), Username: "pekka", Displayname: "", FriendRequests: []string{}},
		},
		accounts: map[string]*profileAccount{
			testActor: {Following: []string{otherActor}, Friends: []string{}, FriendRequests: []string{}},
		},
		people: map[string]profileSummary{otherActor: profileSummaryFor("pekka", "", "https://cdn.test/p.jpg")},
	}
}

func profileRig(t *testing.T, store *fakeProfileStore, bridgeOn bool) http.Handler {
	var resolver *sessionResolver
	if bridgeOn {
		config, _ := fakeFlask(t, flaskAnswer)
		resolver, _ = newSessionResolver(config)
	}
	return profileHandler(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(299) }), store, resolver)
}

func profileJSON(t *testing.T, w *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	var body map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err, w.Body.String())
	}
	return body
}

func TestProfileAnonymousGetsOnlyPublicFields(t *testing.T) {
	h := profileRig(t, newProfileStore(t), true)
	w := get(h, "/api/profile/maija", "")
	if w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	body := profileJSON(t, w)
	for _, key := range []string{"email", "_id", "id", "role", "password_hash", "followed_organizations", "followed_recurring"} {
		if _, found := body[key]; found {
			t.Errorf("anonymous answer carries %q", key)
		}
	}
	if strings.Contains(w.Body.String(), testActor) {
		t.Error("account id leaked")
	}
	if body["username"] != "maija" || body["displayname"] != "Maija" || body["admin_badge"] != true || body["bio"] != "Hei" ||
		body["profile_picture"] != nil || body["posts_count"] != float64(0) || body["self"] != false || body["viewer"] != nil {
		t.Fatalf("unexpected body %v", body)
	}
	if body["followers_count"] != float64(3) || len(body["followers"].([]any)) != 1 {
		t.Fatalf("count follows the stored list, entries only resolvable accounts: %v", body)
	}
	if got := w.Header().Get("Cache-Control"); got != "public, max-age=60" || w.Header().Get("Vary") != "Cookie" ||
		w.Header().Get("X-Content-Type-Options") != "nosniff" {
		t.Fatalf("headers %v", w.Header())
	}
	other := profileJSON(t, get(h, "/api/profile/pekka", ""))
	if other["displayname"] != "pekka" {
		t.Fatal("display name falls back to the username")
	}
}

func TestProfileOwnerGetsOwnLists(t *testing.T) {
	h := profileRig(t, newProfileStore(t), true)
	w := get(h, "/api/profile/maija", "session=member")
	body := profileJSON(t, w)
	if body["self"] != true || body["viewer"] != nil || len(body["followed_organizations"].([]any)) != 1 ||
		len(body["followed_recurring"].([]any)) != 1 {
		t.Fatalf("unexpected body %v", body)
	}
	if w.Header().Get("Cache-Control") != "no-store" || w.Header().Get("Vary") != "Cookie" {
		t.Fatalf("headers %v", w.Header())
	}
	// Another account's own lists are never shown to a visitor.
	if profileJSON(t, get(h, "/api/profile/pekka", "session=member"))["self"] != false {
		t.Fatal("only the owner is self")
	}
}

func TestProfileViewerStates(t *testing.T) {
	store := newProfileStore(t)
	h := profileRig(t, store, true)
	cases := []struct {
		name    string
		account *profileAccount
		other   []string
		want    string
	}{
		{"none", &profileAccount{}, nil, "none"},
		{"friends", &profileAccount{Friends: []string{otherActor}}, nil, "friends"},
		{"incoming", &profileAccount{FriendRequests: []string{otherActor}}, nil, "incoming_request"},
		{"outgoing", &profileAccount{}, []string{testActor}, "outgoing_request"},
	}
	for _, c := range cases {
		store.accounts[testActor] = c.account
		store.users["pekka"].FriendRequests = c.other
		viewer, _ := profileJSON(t, get(h, "/api/profile/pekka", "session=member"))["viewer"].(map[string]any)
		if viewer["friend_state"] != c.want || viewer["following"] != false {
			t.Errorf("%s: %v", c.name, viewer)
		}
	}
	store.accounts[testActor] = &profileAccount{Following: []string{otherActor}}
	viewer := profileJSON(t, get(h, "/api/profile/pekka", "session=member"))["viewer"].(map[string]any)
	if viewer["following"] != true {
		t.Fatal(viewer)
	}
	if body := profileJSON(t, get(h, "/api/profile/pekka", "session=guest")); body["viewer"] != nil || body["self"] != false {
		t.Fatal("a guest cookie is anonymous", body)
	}
}

func TestProfileBridgeFailureKeepsThePublicPart(t *testing.T) {
	h := profileRig(t, newProfileStore(t), true)
	for _, cookie := range []string{"session=error", "session=broken", "session=badactor"} {
		w := get(h, "/api/profile/maija", cookie)
		body := profileJSON(t, w)
		if w.Code != 200 || body["self"] != false || body["viewer"] != nil || body["viewer_unverified"] != true || body["username"] != "maija" {
			t.Errorf("%s: %d %v", cookie, w.Code, body)
		}
		if w.Header().Get("Cache-Control") != "no-store" {
			t.Errorf("%s: a cookie answer must not be cached", cookie)
		}
	}
}

func TestProfileWithoutTheBridgeIsPublicOnly(t *testing.T) {
	h := profileRig(t, newProfileStore(t), false)
	body := profileJSON(t, get(h, "/api/profile/maija", "session=member"))
	if body["self"] != false || body["viewer"] != nil || body["viewer_unverified"] != nil {
		t.Fatal(body)
	}
	if _, found := body["followed_organizations"]; found {
		t.Fatal("own lists need the bridge")
	}
}

func TestProfileRejectsBadRequests(t *testing.T) {
	h := profileRig(t, newProfileStore(t), true)
	for _, path := range []string{"/api/profile/", "/api/profile/nobody", "/api/profile/a%2Fb", "/api/profile/" + strings.Repeat("a", 65),
		"/api/profile/a%00b", "/api/profile/a/b"} {
		if w := get(h, path, ""); w.Code != 404 || strings.TrimSpace(w.Body.String()) != `{"error":"not found"}` {
			t.Errorf("%s: %d %s", path, w.Code, w.Body.String())
		}
	}
	for _, site := range []string{"cross-site", "same-site"} {
		if w := get(h, "/api/profile/maija", "", "Sec-Fetch-Site", site); w.Code != 403 {
			t.Errorf("%s: %d", site, w.Code)
		}
	}
	for _, site := range []string{"same-origin", "none"} {
		if w := get(h, "/api/profile/maija", "", "Sec-Fetch-Site", site); w.Code != 200 {
			t.Errorf("%s: %d", site, w.Code)
		}
	}
	post := httptest.NewRecorder()
	h.ServeHTTP(post, httptest.NewRequest("POST", "/api/profile/maija", nil))
	if post.Code != 405 || post.Header().Get("Allow") != "GET, HEAD" {
		t.Fatal(post.Code)
	}
	head := httptest.NewRecorder()
	h.ServeHTTP(head, httptest.NewRequest("HEAD", "/api/profile/maija", nil))
	if head.Code != 200 {
		t.Fatal(head.Code)
	}
	if w := get(h, "/other", ""); w.Code != 299 {
		t.Fatal("other paths pass through")
	}
}

func TestProfileDatabaseFailureIs503(t *testing.T) {
	store := newProfileStore(t)
	store.err = errors.New("db down")
	h := profileRig(t, store, true)
	w := get(h, "/api/profile/maija", "")
	if w.Code != 503 || w.Header().Get("Cache-Control") != "no-store" {
		t.Fatal(w.Code, w.Header())
	}
	store = newProfileStore(t)
	store.listErr = errors.New("db down")
	if w := get(profileRig(t, store, true), "/api/profile/maija", "session=member"); w.Code != 503 {
		t.Fatal("own list failure", w.Code)
	}
}

func TestProfileFlagOffPassesThrough(t *testing.T) {
	t.Setenv("PROFILE_READS_ENABLED", "")
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(404) })
	if w := get(profileReadHandler(next, nil, authBridgeConfig{}), "/api/profile/maija", ""); w.Code != 404 {
		t.Fatal(w.Code)
	}
}

func TestProfileHelpers(t *testing.T) {
	id := bson.NewObjectID()
	ids, count := profileIDs(bson.A{id, id.Hex(), "bad", bson.M{"$oid": id.Hex()}, bson.M{"user_id": id}}, "user_id")
	if count != 5 || len(ids) != 4 {
		t.Fatal(ids, count)
	}
	long := strings.Repeat("sana ", 40)
	cut := profileTruncate(long)
	if len([]rune(cut)) > 120 || !strings.HasSuffix(cut, "…") || profileTruncate("lyhyt") != "lyhyt" {
		t.Fatal(cut)
	}
}
