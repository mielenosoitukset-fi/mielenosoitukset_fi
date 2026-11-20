package main

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
)

var inviteNow = time.Date(2026, 11, 20, 10, 0, 0, 0, time.UTC)

type fakeInviteStore struct {
	mu          sync.Mutex
	email       string
	accountGone bool
	org         *orgInviteOrg
	managed     bool
	members     map[string]string // user hex -> role
	audits      []bson.M
	memberErr   error
	readErr     error
}

func (f *fakeInviteStore) AccountEmail(context.Context, bson.ObjectID) (string, bool, error) {
	return f.email, !f.accountGone, nil
}
func (f *fakeInviteStore) Organization(context.Context, bson.ObjectID) (*orgInviteOrg, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.readErr != nil || f.org == nil {
		return nil, f.readErr
	}
	copied := *f.org
	copied.Invitations = append(bson.A{}, f.org.Invitations...)
	return &copied, nil
}
func (f *fakeInviteStore) GovernanceManaged(context.Context, bson.ObjectID) (bool, error) {
	return f.managed, nil
}
func (f *fakeInviteStore) ReplaceInvitations(_ context.Context, _ bson.ObjectID, expected, next bson.A) (bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if !reflect.DeepEqual(f.org.Invitations, expected) {
		return false, nil
	}
	f.org.Invitations = next
	return true, nil
}
func (f *fakeInviteStore) AddMember(_ context.Context, user, _ bson.ObjectID) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.memberErr != nil {
		return f.memberErr
	}
	if _, ok := f.members[user.Hex()]; !ok {
		f.members[user.Hex()] = "member"
	}
	return nil
}
func (f *fakeInviteStore) Audit(_ context.Context, doc bson.M) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.audits = append(f.audits, doc)
	return nil
}

type inviteRig struct {
	h     http.Handler
	store *fakeInviteStore
}

func newInviteRig(t *testing.T, invitations bson.A, protected map[bson.ObjectID]bool) inviteRig {
	t.Helper()
	config, _ := fakeFlask(t, flaskAnswer)
	resolver, _ := newSessionResolver(config)
	store := &fakeInviteStore{email: "member@example.com", org: &orgInviteOrg{Name: "Rauhan ystävät", Invitations: invitations}, members: map[string]string{}}
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(299) })
	h := orgInviteHandler(next, store, resolver, "https://example.test", protected)
	return inviteRig{h: h, store: store}
}

// guardToken is a CSRF token for cases where the read would not issue one (no valid
// invitation): it asks a fresh rig built on the same handler for an invited account.
func (g inviteRig) guardToken() string {
	original := g.store.org.Invitations
	g.store.org.Invitations = bson.A{g.store.email}
	defer func() { g.store.org.Invitations = original }()
	return g.csrf(nil)
}

func (g inviteRig) call(method, path, body string, headers ...string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	r.Header.Set("Cookie", "session=member")
	r.Header.Set("Sec-Fetch-Site", "same-origin")
	r.Header.Set("Origin", "https://example.test")
	r.Header.Set("Content-Type", "application/json")
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

const (
	invitePathRead   = "/api/organizations/" + testOrg + "/invitation"
	invitePathAccept = invitePathRead + "/accept"
)

// csrf reads the invitation like the page does and returns the token.
func (g inviteRig) csrf(t *testing.T) string {
	if t != nil {
		t.Helper()
	}
	read := g.call("GET", invitePathRead, "")
	body := read.Body.String()
	i := strings.Index(body, `"csrf":"`)
	if read.Code != 200 || i < 0 {
		panic("read: " + read.Body.String())
	}
	rest := body[i+len(`"csrf":"`):]
	return rest[:strings.Index(rest, `"`)]
}

func (g inviteRig) accept(t *testing.T, headers ...string) *httptest.ResponseRecorder {
	t.Helper()
	return g.call("POST", invitePathAccept, "{}", append([]string{developerCSRFHeader, g.csrf(t)}, headers...)...)
}

func dictInvite(email string, age time.Duration) bson.D {
	return bson.D{{Key: "email", Value: email}, {Key: "invited_at", Value: bson.NewDateTimeFromTime(time.Now().Add(-age))}}
}

func TestInvitationMatchingMirrorsFlask(t *testing.T) {
	now := time.Now()
	cases := []struct {
		name        string
		list        bson.A
		email       string
		invited     bool
		remainCount int
	}{
		{"legacy string", bson.A{"member@example.com", "other@example.com"}, "member@example.com", true, 1},
		{"dict inside 30 days", bson.A{dictInvite("member@example.com", 29*24*time.Hour)}, "member@example.com", true, 0},
		{"dict older than 30 days is expired", bson.A{dictInvite("member@example.com", 31*24*time.Hour)}, "member@example.com", false, 0},
		{"dict without invited_at never expires", bson.A{bson.D{{Key: "email", Value: "member@example.com"}}}, "member@example.com", true, 0},
		{"exact match only (case differs)", bson.A{"Member@Example.com"}, "member@example.com", false, 1},
		{"other address", bson.A{"other@example.com"}, "member@example.com", false, 1},
		{"empty list", bson.A{}, "member@example.com", false, 0},
		{"nil list", nil, "member@example.com", false, 0},
		{"junk entries are ignored and dropped", bson.A{nil, int32(5), bson.D{{Key: "name", Value: "x"}}}, "member@example.com", false, 1},
		{"empty account address never matches", bson.A{""}, "", false, 1},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			invited, remaining := invitationState(c.list, c.email, now)
			if invited != c.invited {
				t.Fatalf("invited=%v", invited)
			}
			if len(remaining) != c.remainCount {
				t.Fatalf("remaining=%v want %d entries", remaining, c.remainCount)
			}
		})
	}
}

func TestInvitationReadStates(t *testing.T) {
	protectedOrg, _ := bson.ObjectIDFromHex(testOrg)
	t.Run("invited: name and csrf for this account only", func(t *testing.T) {
		g := newInviteRig(t, bson.A{"member@example.com"}, nil)
		r := g.call("GET", invitePathRead, "")
		body := r.Body.String()
		if r.Code != 200 || !strings.Contains(body, `"state":"invited"`) || !strings.Contains(body, `"name":"Rauhan ystävät"`) || !strings.Contains(body, `"csrf"`) {
			t.Fatalf("%d %s", r.Code, body)
		}
		if r.Header().Get("Cache-Control") != "no-store" || !strings.Contains(r.Header().Get("Vary"), "Cookie") {
			t.Fatal("must be no-store and vary on the cookie")
		}
		if len(g.store.audits) != 0 || len(g.store.members) != 0 {
			t.Fatal("a read must not write")
		}
	})
	t.Run("not invited: no name, no csrf", func(t *testing.T) {
		g := newInviteRig(t, bson.A{"other@example.com"}, nil)
		body := g.call("GET", invitePathRead, "").Body.String()
		if !strings.Contains(body, `"not-invited"`) || strings.Contains(body, "Rauhan") || strings.Contains(body, "csrf") {
			t.Fatal(body)
		}
	})
	t.Run("expired invitation reads as not invited", func(t *testing.T) {
		g := newInviteRig(t, bson.A{dictInvite("member@example.com", 40*24*time.Hour)}, nil)
		if body := g.call("GET", invitePathRead, "").Body.String(); !strings.Contains(body, `"not-invited"`) {
			t.Fatal(body)
		}
	})
	t.Run("organization missing", func(t *testing.T) {
		g := newInviteRig(t, nil, nil)
		g.store.org = nil
		if body := g.call("GET", invitePathRead, "").Body.String(); !strings.Contains(body, `"organization-not-found"`) {
			t.Fatal(body)
		}
	})
	t.Run("configured system team", func(t *testing.T) {
		g := newInviteRig(t, bson.A{"member@example.com"}, map[bson.ObjectID]bool{protectedOrg: true})
		body := g.call("GET", invitePathRead, "").Body.String()
		if !strings.Contains(body, `"protected"`) || strings.Contains(body, "csrf") {
			t.Fatal(body)
		}
	})
	t.Run("governance-managed organization even when not configured", func(t *testing.T) {
		g := newInviteRig(t, bson.A{"member@example.com"}, nil)
		g.store.managed = true
		if body := g.call("GET", invitePathRead, "").Body.String(); !strings.Contains(body, `"protected"`) {
			t.Fatal(body)
		}
	})
	t.Run("signed out is 401, never a state", func(t *testing.T) {
		g := newInviteRig(t, bson.A{"member@example.com"}, nil)
		if r := g.call("GET", invitePathRead, "", "Cookie", "session=guest"); r.Code != 401 || strings.Contains(r.Body.String(), "state") {
			t.Fatalf("%d %s", r.Code, r.Body.String())
		}
	})
	t.Run("cross-site read refused", func(t *testing.T) {
		g := newInviteRig(t, bson.A{"member@example.com"}, nil)
		if r := g.call("GET", invitePathRead, "", "Sec-Fetch-Site", "cross-site"); r.Code != 403 {
			t.Fatalf("%d", r.Code)
		}
	})
	t.Run("store failure is 503", func(t *testing.T) {
		g := newInviteRig(t, nil, nil)
		g.store.readErr = errors.New("down")
		if r := g.call("GET", invitePathRead, ""); r.Code != 503 {
			t.Fatalf("%d", r.Code)
		}
	})
	t.Run("account document missing is a failure", func(t *testing.T) {
		g := newInviteRig(t, bson.A{"member@example.com"}, nil)
		g.store.accountGone = true
		if r := g.call("GET", invitePathRead, ""); r.Code != 503 {
			t.Fatalf("%d", r.Code)
		}
	})
}

func TestInvitationAcceptJoinsOnce(t *testing.T) {
	g := newInviteRig(t, bson.A{"member@example.com", "other@example.com"}, nil)
	token := g.csrf(t)
	r := g.call("POST", invitePathAccept, "{}", developerCSRFHeader, token)
	if r.Code != 200 || !strings.Contains(r.Body.String(), `"state":"joined"`) {
		t.Fatalf("%d %s", r.Code, r.Body.String())
	}
	if g.store.members[testActor] != "member" {
		t.Fatalf("members=%v", g.store.members)
	}
	if !reflect.DeepEqual(g.store.org.Invitations, bson.A{"other@example.com"}) {
		t.Fatalf("invitations=%v", g.store.org.Invitations)
	}
	if len(g.store.audits) != 1 || g.store.audits[0]["action"] != "organization invitation accepted" || g.store.audits[0]["source"] != "go-api" {
		t.Fatalf("audits=%v", g.store.audits)
	}
	// Replaying the click (or the same request) finds no invitation, as Flask does.
	again := g.call("POST", invitePathAccept, "{}", developerCSRFHeader, token)
	if !strings.Contains(again.Body.String(), `"not-invited"`) || len(g.store.audits) != 1 {
		t.Fatalf("replay: %s", again.Body.String())
	}
}

func TestInvitationAcceptConcurrentClicksJoinOnce(t *testing.T) {
	g := newInviteRig(t, bson.A{"member@example.com"}, nil)
	token := g.csrf(t)
	var wg sync.WaitGroup
	var mu sync.Mutex
	joined := 0
	for i := 0; i < 15; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			r := g.call("POST", invitePathAccept, "{}", developerCSRFHeader, token)
			if strings.Contains(r.Body.String(), `"joined"`) {
				mu.Lock()
				joined++
				mu.Unlock()
			}
		}()
	}
	wg.Wait()
	if joined != 1 || len(g.store.audits) != 1 {
		t.Fatalf("joined %d times, %d audits", joined, len(g.store.audits))
	}
}

func TestInvitationAcceptRefusals(t *testing.T) {
	protectedOrg, _ := bson.ObjectIDFromHex(testOrg)
	invited := bson.A{"member@example.com"}
	unchanged := func(t *testing.T, g inviteRig) {
		t.Helper()
		if len(g.store.members) != 0 || !reflect.DeepEqual(g.store.org.Invitations, invited) || len(g.store.audits) != 0 {
			t.Fatalf("state changed: members=%v invitations=%v", g.store.members, g.store.org.Invitations)
		}
	}
	t.Run("GET on the accept path never writes", func(t *testing.T) {
		g := newInviteRig(t, invited, nil)
		for _, method := range []string{"GET", "HEAD", "PUT", "DELETE"} {
			if r := g.call(method, invitePathAccept, ""); r.Code != 405 || r.Header().Get("Allow") != "POST" {
				t.Fatalf("%s: %d", method, r.Code)
			}
		}
		unchanged(t, g)
	})
	t.Run("POST on the read path", func(t *testing.T) {
		g := newInviteRig(t, invited, nil)
		if r := g.call("POST", invitePathRead, "{}"); r.Code != 405 {
			t.Fatalf("%d", r.Code)
		}
		unchanged(t, g)
	})
	t.Run("missing or foreign csrf token", func(t *testing.T) {
		g := newInviteRig(t, invited, nil)
		for _, token := range []string{"-", "garbage", "9999999999.aa.bb"} {
			if r := g.call("POST", invitePathAccept, "{}", developerCSRFHeader, token); r.Code != 403 {
				t.Fatalf("%q: %d", token, r.Code)
			}
		}
		unchanged(t, g)
	})
	for name, headers := range map[string][]string{
		"cross-site":      {"Sec-Fetch-Site", "cross-site"},
		"no fetch header": {"Sec-Fetch-Site", "-"},
		"foreign origin":  {"Origin", "https://evil.example"},
	} {
		t.Run(name, func(t *testing.T) {
			g := newInviteRig(t, invited, nil)
			if r := g.accept(t, headers...); r.Code != 403 {
				t.Fatalf("%d", r.Code)
			}
			unchanged(t, g)
		})
	}
	t.Run("signed out", func(t *testing.T) {
		g := newInviteRig(t, invited, nil)
		if r := g.call("POST", invitePathAccept, "{}", "Cookie", "session=guest"); r.Code != 401 {
			t.Fatalf("%d", r.Code)
		}
		unchanged(t, g)
	})
	t.Run("not JSON", func(t *testing.T) {
		g := newInviteRig(t, invited, nil)
		if r := g.accept(t, "Content-Type", "text/plain"); r.Code != 415 {
			t.Fatalf("%d", r.Code)
		}
		unchanged(t, g)
	})
	for name, body := range map[string]string{"unexpected field": `{"role":"admin"}`, "trailing data": `{}{}`, "array": `[]`, "text": `x`} {
		t.Run("body "+name, func(t *testing.T) {
			g := newInviteRig(t, invited, nil)
			if r := g.call("POST", invitePathAccept, body, developerCSRFHeader, g.csrf(t)); r.Code != 400 {
				t.Fatalf("%d", r.Code)
			}
			unchanged(t, g)
		})
	}
	t.Run("a system team refuses with 403", func(t *testing.T) {
		teams := map[bson.ObjectID]bool{}
		g := newInviteRig(t, invited, teams)
		token := g.csrf(t) // minted while the organization is still ordinary
		teams[protectedOrg] = true
		r := g.call("POST", invitePathAccept, "{}", developerCSRFHeader, token)
		if r.Code != 403 || !strings.Contains(r.Body.String(), `"protected"`) {
			t.Fatalf("%d %s", r.Code, r.Body.String())
		}
		unchanged(t, g)
	})
	t.Run("governance-managed organization refuses", func(t *testing.T) {
		g := newInviteRig(t, invited, nil)
		token := g.csrf(t)
		g.store.managed = true
		if r := g.call("POST", invitePathAccept, "{}", developerCSRFHeader, token); r.Code != 403 {
			t.Fatalf("%d", r.Code)
		}
		unchanged(t, g)
	})
	t.Run("another account's invitation is not usable", func(t *testing.T) {
		g := newInviteRig(t, bson.A{"other@example.com"}, nil)
		token := g.guardToken()
		if r := g.call("POST", invitePathAccept, "{}", developerCSRFHeader, token); !strings.Contains(r.Body.String(), `"not-invited"`) {
			t.Fatal(r.Body.String())
		}
		if len(g.store.members) != 0 {
			t.Fatal("joined without an invitation")
		}
	})
	t.Run("expired invitation", func(t *testing.T) {
		g := newInviteRig(t, bson.A{dictInvite("member@example.com", 31*24*time.Hour)}, nil)
		if r := g.call("POST", invitePathAccept, "{}", developerCSRFHeader, g.guardToken()); !strings.Contains(r.Body.String(), `"not-invited"`) {
			t.Fatal(r.Body.String())
		}
		if len(g.store.members) != 0 {
			t.Fatal("joined with an expired invitation")
		}
	})
	t.Run("a failed membership write puts the invitation back", func(t *testing.T) {
		g := newInviteRig(t, invited, nil)
		token := g.csrf(t)
		g.store.memberErr = errors.New("down")
		r := g.call("POST", invitePathAccept, "{}", developerCSRFHeader, token)
		if r.Code != 503 || strings.Contains(r.Body.String(), "joined") {
			t.Fatalf("%d %s", r.Code, r.Body.String())
		}
		unchanged(t, g)
	})
	t.Run("existing membership keeps its role", func(t *testing.T) {
		g := newInviteRig(t, invited, nil)
		g.store.members[testActor] = "admin"
		if r := g.call("POST", invitePathAccept, "{}", developerCSRFHeader, g.guardToken()); r.Code != 200 {
			t.Fatalf("%d", r.Code)
		}
		if g.store.members[testActor] != "admin" {
			t.Fatal("a racing role was overwritten")
		}
	})
	t.Run("rate limit per account", func(t *testing.T) {
		g := newInviteRig(t, bson.A{"other@example.com"}, nil)
		token := g.guardToken()
		last := 0
		for i := 0; i < developerWritesPerMin+1; i++ {
			last = g.call("POST", invitePathAccept, "{}", developerCSRFHeader, token).Code
		}
		if last != 429 {
			t.Fatalf("%d", last)
		}
	})
	t.Run("other paths pass through", func(t *testing.T) {
		g := newInviteRig(t, invited, nil)
		if r := g.call("GET", "/api/organizations/"+testOrg, ""); r.Code != 299 {
			t.Fatalf("%d", r.Code)
		}
	})
}

func TestInvitationOffByDefault(t *testing.T) {
	next := http.NewServeMux()
	config, _ := fakeFlask(t, flaskAnswer)
	resolver, _ := newSessionResolver(config)
	t.Setenv("ORG_INVITE_GO_ENABLED", "")
	if orgInviteFromEnv(next, nil, resolver, "https://example.test") != http.Handler(next) {
		t.Fatal("flag off must return next")
	}
	t.Setenv("ORG_INVITE_GO_ENABLED", "true")
	t.Setenv("BOARD_ORG_ID", "not-an-id")
	if orgInviteFromEnv(next, nil, resolver, "https://example.test") != http.Handler(next) {
		t.Fatal("an invalid system team id must disable the route")
	}
	t.Setenv("BOARD_ORG_ID", "")
	t.Setenv("NATIONAL_ADMIN_ORG_ID", testOrg)
	ids, ok := orgProtectedIDs()
	if !ok || len(ids) != 1 {
		t.Fatalf("ids=%v ok=%v", ids, ok)
	}
}

// Needs a disposable MongoDB (REMINDER_TEST_MONGO): the real conditional update, upsert and count.
func TestInvitationMongoStore(t *testing.T) {
	db := reminderTestDB(t)
	ctx := context.Background()
	org, user := bson.NewObjectID(), bson.NewObjectID()
	dated := dictInvite("dated@example.com", 24*time.Hour)
	list := bson.A{"member@example.com", dated, "other@example.com"}
	if _, err := db.Collection("organizations").InsertOne(ctx, bson.M{"_id": org, "name": "Testi", "invitations": list}); err != nil {
		t.Fatal(err)
	}
	store := mongoOrgInviteStore{db}
	read, err := store.Organization(ctx, org)
	if err != nil || read == nil || read.Name != "Testi" || len(read.Invitations) != 3 {
		t.Fatalf("read: %v %v", read, err)
	}
	// The invitation list as read is what the conditional update must match, mixed types included.
	invited, remaining := invitationState(read.Invitations, "member@example.com", time.Now())
	if !invited || len(remaining) != 2 {
		t.Fatalf("invited=%v remaining=%v", invited, remaining)
	}
	if ok, err := store.ReplaceInvitations(ctx, org, bson.A{"stale"}, remaining); err != nil || ok {
		t.Fatalf("a stale list must not win: %v %v", ok, err)
	}
	if ok, err := store.ReplaceInvitations(ctx, org, read.Invitations, remaining); err != nil || !ok {
		t.Fatalf("the exact list must win: %v %v", ok, err)
	}
	if ok, _ := store.ReplaceInvitations(ctx, org, read.Invitations, remaining); ok {
		t.Fatal("the same claim must not win twice")
	}
	after, _ := store.Organization(ctx, org)
	if len(after.Invitations) != 2 {
		t.Fatalf("after=%v", after.Invitations)
	}
	// Membership: inserted once with the member permissions, never overwritten.
	if err := store.AddMember(ctx, user, org); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Collection("memberships").UpdateOne(ctx, bson.M{"user_id": user, "organization_id": org}, bson.M{"$set": bson.M{"role": "admin"}}); err != nil {
		t.Fatal(err)
	}
	if err := store.AddMember(ctx, user, org); err != nil {
		t.Fatal(err)
	}
	var rows []bson.M
	cursor, _ := db.Collection("memberships").Find(ctx, bson.M{"organization_id": org})
	if err := cursor.All(ctx, &rows); err != nil || len(rows) != 1 || rows[0]["role"] != "admin" {
		t.Fatalf("rows=%v err=%v", rows, err)
	}
	if perms, _ := rows[0]["permissions"].(bson.A); len(perms) != 3 || perms[0] != "INVITE_TO_ORGANIZATION" {
		t.Fatalf("permissions=%v", rows[0]["permissions"])
	}
	// The governance marker.
	if managed, err := store.GovernanceManaged(ctx, org); err != nil || managed {
		t.Fatalf("managed=%v %v", managed, err)
	}
	if _, err := db.Collection("memberships").InsertOne(ctx, bson.M{"organization_id": org.Hex(), "user_id": bson.NewObjectID(), "governance_approved": true}); err != nil {
		t.Fatal(err)
	}
	if managed, _ := store.GovernanceManaged(ctx, org); !managed {
		t.Fatal("a governance-approved membership must mark the organization")
	}
	// Account address lookup.
	if _, err := db.Collection("users").InsertOne(ctx, bson.M{"_id": user, "email": "member@example.com"}); err != nil {
		t.Fatal(err)
	}
	if email, found, err := store.AccountEmail(ctx, user); err != nil || !found || email != "member@example.com" {
		t.Fatalf("%q %v %v", email, found, err)
	}
	if _, found, _ := store.AccountEmail(ctx, bson.NewObjectID()); found {
		t.Fatal("unknown account found")
	}
}
