package main

// Read-only data for the React public profile page (docs/account-pages-react.md).
//
// GET /api/profile/<username> exists only when PROFILE_READS_ENABLED=true. The
// public part needs no session. The own part (followed organizations and
// recurring demonstrations) and the viewer part (follow and friend state) need
// the auth bridge, so with the bridge off they are simply absent. A bridge
// failure never fails the page: the public part is still returned.
//
// The answer never carries an email address, an account id, role names, flags
// or dates.

import (
	"context"
	"errors"
	"log"
	"net/http"
	"os"
	"strings"
	"time"
	"unicode/utf8"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

const (
	profilePathPrefix    = "/api/profile/"
	profileMaxUsername   = 64
	profileMaxFollowList = 500
	profileMaxOwnList    = 200
	// Stored lists can be long; only this many ids are looked up for one list.
	profileMaxLookups     = 2000
	profileDescriptionMax = 120
)

type profileUser struct {
	ID             bson.ObjectID
	Username       string
	Displayname    string
	ProfilePicture string
	Bio            string
	AdminBadge     bool
	// Counts are the stored list lengths (Flask parity), valid or not.
	FollowersCount    int
	FollowingCount    int
	Followers         []string
	Following         []string
	FollowedOrgs      []string
	FollowedRecurring []string
	FriendRequests    []string
}

// profileAccount is the signed-in viewer's own document, read by id.
type profileAccount struct {
	Following      []string
	Friends        []string
	FriendRequests []string
}

type profileSummary struct {
	Username       string  `json:"username"`
	Displayname    string  `json:"displayname"`
	ProfilePicture *string `json:"profile_picture"`
}

type profileOrg struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	Logo        string `json:"logo"`
	City        string `json:"city"`
	Description string `json:"description"`
}

type profileRecurring struct {
	ID      string `json:"id"`
	Title   string `json:"title"`
	Date    string `json:"date"`
	City    string `json:"city"`
	Address string `json:"address"`
}

type profileStore interface {
	// User returns nil when no account has exactly this username.
	User(ctx context.Context, username string) (*profileUser, error)
	// Account returns nil when the id has no document.
	Account(ctx context.Context, id bson.ObjectID) (*profileAccount, error)
	// Summaries resolves ids in the given order, skipping missing accounts and accounts without a username.
	Summaries(ctx context.Context, ids []string) ([]profileSummary, error)
	Organizations(ctx context.Context, ids []string) ([]profileOrg, error)
	Recurring(ctx context.Context, ids []string) ([]profileRecurring, error)
}

type mongoProfileStore struct{ db *mongo.Database }

// profileKey reads a key from a decoded document of any of the driver's document shapes.
func profileKey(value any, key string) any {
	switch doc := value.(type) {
	case bson.M:
		return doc[key]
	case map[string]any:
		return doc[key]
	case bson.D:
		for _, item := range doc {
			if item.Key == key {
				return item.Value
			}
		}
	}
	return nil
}

// profileID turns a stored reference (ObjectID, 24-character hex string or {"$oid": ...}) into lowercase hex.
func profileID(value any) string {
	switch id := value.(type) {
	case bson.ObjectID:
		return id.Hex()
	case string:
		if len(id) == 24 {
			if parsed, err := bson.ObjectIDFromHex(id); err == nil {
				return parsed.Hex()
			}
		}
	default:
		if oid := profileKey(value, "$oid"); oid != nil {
			if text, ok := oid.(string); ok {
				return profileID(text)
			}
		}
	}
	return ""
}

// profileIDs reads a stored list. Entries are either references or documents
// holding one under key (friends use user_id, friend requests use sent_by).
// The second result is the stored length, valid or not.
func profileIDs(value any, key string) ([]string, int) {
	list, ok := value.(bson.A)
	if !ok {
		return []string{}, 0
	}
	out := make([]string, 0, len(list))
	for _, item := range list {
		id := profileID(item)
		if id == "" && key != "" {
			id = profileID(profileKey(item, key))
		}
		if id != "" {
			out = append(out, id)
		}
	}
	return out, len(list)
}

func (s mongoProfileStore) User(ctx context.Context, username string) (*profileUser, error) {
	var doc bson.M
	err := s.db.Collection("users").FindOne(ctx, bson.M{"username": username}, options.FindOne().SetProjection(bson.M{
		"username": 1, "displayname": 1, "profile_picture": 1, "bio": 1, "global_admin": 1,
		"governance_managed": 1, "active": 1, "banned": 1, "confirmed": 1, "security_suspension": 1,
		"followers": 1, "following": 1, "followed_organizations": 1, "followed_recurring_demos": 1, "friend_requests": 1,
	})).Decode(&doc)
	if errors.Is(err, mongo.ErrNoDocuments) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	id, ok := doc["_id"].(bson.ObjectID)
	if !ok || profileAccountHidden(doc) {
		return nil, nil
	}
	national, err := userHasNationalAccess(ctx, s, doc, id)
	if err != nil {
		return nil, err
	}
	user := &profileUser{ID: id, Username: stringField(doc, "username"), Displayname: stringField(doc, "displayname"),
		ProfilePicture: stringField(doc, "profile_picture"), Bio: stringField(doc, "bio"),
		AdminBadge: profileAdminBadge(doc, national)}
	user.Followers, user.FollowersCount = profileIDs(doc["followers"], "")
	user.Following, user.FollowingCount = profileIDs(doc["following"], "")
	user.FollowedOrgs, _ = profileIDs(doc["followed_organizations"], "")
	user.FollowedRecurring, _ = profileIDs(doc["followed_recurring_demos"], "")
	user.FriendRequests, _ = profileIDs(doc["friend_requests"], "sent_by")
	return user, nil
}

func (s mongoProfileStore) OrgExists(ctx context.Context, org bson.ObjectID) (bool, error) {
	err := s.db.Collection("organizations").FindOne(ctx, bson.M{"_id": org}, options.FindOne().SetProjection(bson.M{"_id": 1})).Err()
	if errors.Is(err, mongo.ErrNoDocuments) {
		return false, nil
	}
	return err == nil, err
}

func (s mongoProfileStore) Memberships(ctx context.Context, user, org bson.ObjectID) ([]bson.M, error) {
	cursor, err := s.db.Collection("memberships").Find(ctx, bson.M{
		"user_id": bson.M{"$in": bson.A{user, user.Hex()}}, "organization_id": bson.M{"$in": bson.A{org, org.Hex()}},
		"governance_approved": true}, options.Find().SetProjection(bson.M{"grant_source": 1}))
	if err != nil {
		return nil, err
	}
	var rows []bson.M
	if err := cursor.All(ctx, &rows); err != nil {
		return nil, err
	}
	return rows, nil
}

func (s mongoProfileStore) Clearance(ctx context.Context, userID string) (bson.M, error) {
	var record bson.M
	err := s.db.Collection("board_clearances").FindOne(ctx, bson.M{"user_id": userID}).Decode(&record)
	if errors.Is(err, mongo.ErrNoDocuments) {
		return nil, nil
	}
	return record, err
}

// profileAccountHidden reports accounts whose public profile answers "not found",
// the same answer as for a missing user (maintainer decision, 18 November 2026):
// suspended, banned and deactivated accounts. No account document is read for
// deleted or anonymised accounts because the code has no such marker; a deleted
// account has no document and is already not found.
func profileAccountHidden(doc bson.M) bool {
	if banned, _ := doc["banned"].(bool); banned {
		return true
	}
	if active, present := doc["active"].(bool); present && !active {
		return true
	}
	suspension, _ := doc["security_suspension"].(bson.M)
	if on, _ := suspension["active"].(bool); on {
		return true
	}
	return false
}

func (s mongoProfileStore) Account(ctx context.Context, id bson.ObjectID) (*profileAccount, error) {
	var doc bson.M
	err := s.db.Collection("users").FindOne(ctx, bson.M{"_id": id}, options.FindOne().SetProjection(bson.M{
		"following": 1, "friends": 1, "friend_requests": 1,
	})).Decode(&doc)
	if errors.Is(err, mongo.ErrNoDocuments) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	account := &profileAccount{}
	account.Following, _ = profileIDs(doc["following"], "")
	account.Friends, _ = profileIDs(doc["friends"], "user_id")
	account.FriendRequests, _ = profileIDs(doc["friend_requests"], "sent_by")
	return account, nil
}

func profileObjectIDs(ids []string) []bson.ObjectID {
	out := make([]bson.ObjectID, 0, len(ids))
	for _, id := range ids {
		if parsed, err := bson.ObjectIDFromHex(id); err == nil {
			out = append(out, parsed)
		}
	}
	return out
}

func (s mongoProfileStore) docsByID(ctx context.Context, collection string, ids []string, projection bson.M) (map[string]bson.M, error) {
	ids = firstN(ids, profileMaxLookups)
	if len(ids) == 0 {
		return map[string]bson.M{}, nil
	}
	cursor, err := s.db.Collection(collection).Find(ctx, bson.M{"_id": bson.M{"$in": profileObjectIDs(ids)}},
		options.Find().SetProjection(projection))
	if err != nil {
		return nil, err
	}
	var docs []bson.M
	if err := cursor.All(ctx, &docs); err != nil {
		return nil, err
	}
	byID := make(map[string]bson.M, len(docs))
	for _, doc := range docs {
		if id, ok := doc["_id"].(bson.ObjectID); ok {
			byID[id.Hex()] = doc
		}
	}
	return byID, nil
}

func firstN[T any](items []T, n int) []T {
	if len(items) > n {
		return items[:n]
	}
	return items
}

func (s mongoProfileStore) Summaries(ctx context.Context, ids []string) ([]profileSummary, error) {
	byID, err := s.docsByID(ctx, "users", ids, bson.M{"username": 1, "displayname": 1, "profile_picture": 1})
	if err != nil {
		return nil, err
	}
	out := []profileSummary{}
	for _, id := range firstN(ids, profileMaxLookups) {
		doc, ok := byID[id]
		if !ok || stringField(doc, "username") == "" {
			continue
		}
		out = append(out, profileSummaryFor(stringField(doc, "username"), stringField(doc, "displayname"), stringField(doc, "profile_picture")))
	}
	return out, nil
}

func profileSummaryFor(username, displayname, picture string) profileSummary {
	summary := profileSummary{Username: username, Displayname: displayname}
	if summary.Displayname == "" {
		summary.Displayname = username
	}
	if picture != "" {
		summary.ProfilePicture = &picture
	}
	return summary
}

func (s mongoProfileStore) Organizations(ctx context.Context, ids []string) ([]profileOrg, error) {
	byID, err := s.docsByID(ctx, "organizations", ids, bson.M{"name": 1, "logo": 1, "city": 1, "description": 1})
	if err != nil {
		return nil, err
	}
	out := []profileOrg{}
	for _, id := range firstN(ids, profileMaxLookups) {
		if doc, ok := byID[id]; ok {
			out = append(out, profileOrg{ID: id, Name: stringField(doc, "name"), Logo: stringField(doc, "logo"),
				City: stringField(doc, "city"), Description: profileTruncate(stringField(doc, "description"))})
		}
	}
	return out, nil
}

func (s mongoProfileStore) Recurring(ctx context.Context, ids []string) ([]profileRecurring, error) {
	byID, err := s.docsByID(ctx, "recu_demos", ids, bson.M{"title": 1, "date": 1, "city": 1, "address": 1})
	if err != nil {
		return nil, err
	}
	out := []profileRecurring{}
	for _, id := range firstN(ids, profileMaxLookups) {
		if doc, ok := byID[id]; ok {
			date := stringField(doc, "date")
			if stamp, ok := doc["date"].(bson.DateTime); ok {
				date = stamp.Time().UTC().Format("2006-01-02")
			}
			out = append(out, profileRecurring{ID: id, Title: stringField(doc, "title"), Date: date,
				City: stringField(doc, "city"), Address: stringField(doc, "address")})
		}
	}
	return out, nil
}

// profileTruncate cuts a description to 120 characters at a word boundary and
// adds an ellipsis, like the Flask template's truncate(120).
func profileTruncate(text string) string {
	if utf8.RuneCountInString(text) <= profileDescriptionMax {
		return text
	}
	cut := string([]rune(text)[:profileDescriptionMax-1])
	if index := strings.LastIndex(cut, " "); index > 0 {
		cut = cut[:index]
	}
	return cut + "…"
}

func profileContains(list []string, id string) bool {
	for _, item := range list {
		if item == id {
			return true
		}
	}
	return false
}

// profileFriendState follows Flask's friend_state view for the viewer and the profile owner.
func profileFriendState(viewer *profileAccount, viewerID string, owner *profileUser) string {
	ownerID := owner.ID.Hex()
	switch {
	case profileContains(viewer.Friends, ownerID):
		return "friends"
	case profileContains(viewer.FriendRequests, ownerID):
		return "incoming_request"
	case profileContains(owner.FriendRequests, viewerID):
		return "outgoing_request"
	}
	return "none"
}

func profileUsername(path string) (string, bool) {
	name := strings.TrimPrefix(path, profilePathPrefix)
	if len(name) < 1 || len(name) > profileMaxUsername || !utf8.ValidString(name) {
		return "", false
	}
	for _, r := range name {
		if r == '/' || r < 0x20 || r == 0x7f {
			return "", false
		}
	}
	return name, true
}

func nullable(text string) any {
	if text == "" {
		return nil
	}
	return text
}

// profileReadHandler serves GET /api/profile/<username> when PROFILE_READS_ENABLED=true.
// The auth bridge is optional: without it only the public part is served.
func profileReadHandler(next http.Handler, db *mongo.Database, bridge authBridgeConfig) http.Handler {
	if os.Getenv("PROFILE_READS_ENABLED") != "true" {
		return next
	}
	var resolver *sessionResolver
	if bridge.Enabled {
		if created, err := newSessionResolver(bridge); err == nil {
			resolver = created
		} else {
			log.Print("Profile viewer state disabled: invalid bridge configuration")
		}
	}
	return profileHandler(next, mongoProfileStore{db}, resolver)
}

func profileHandler(next http.Handler, store profileStore, resolver *sessionResolver) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.URL.Path, profilePathPrefix) {
			next.ServeHTTP(w, r)
			return
		}
		cookie := r.Header.Get("Cookie") != ""
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Vary", "Cookie")
		w.Header().Set("Cache-Control", "no-store") // replaced only on success
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			w.Header().Set("Allow", "GET, HEAD")
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		if site := r.Header.Get("Sec-Fetch-Site"); site != "" && site != "same-origin" && site != "none" {
			writeError(w, http.StatusForbidden, "forbidden")
			return
		}
		username, ok := profileUsername(r.URL.Path)
		if !ok {
			writeError(w, http.StatusNotFound, "not found")
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		body, status := profileAnswer(ctx, r, store, resolver, username)
		if status != http.StatusOK {
			if status == http.StatusNotFound {
				writeError(w, status, "not found")
			} else {
				writeError(w, status, "service unavailable")
			}
			return
		}
		if !cookie {
			w.Header().Set("Cache-Control", "public, max-age=60")
		}
		writeJSON(w, http.StatusOK, body)
	})
}

// profileAnswer builds the JSON body, or returns 404 or 503.
func profileAnswer(ctx context.Context, r *http.Request, store profileStore, resolver *sessionResolver, username string) (map[string]any, int) {
	user, err := store.User(ctx, username)
	if err != nil {
		log.Printf("Profile read failed (%s)", dbErrorKind(err))
		return nil, http.StatusServiceUnavailable
	}
	if user == nil {
		return nil, http.StatusNotFound
	}
	followers, err := store.Summaries(ctx, user.Followers)
	if err == nil {
		var following []profileSummary
		if following, err = store.Summaries(ctx, user.Following); err == nil {
			return profileBody(ctx, r, store, resolver, user, firstN(followers, profileMaxFollowList), firstN(following, profileMaxFollowList))
		}
	}
	log.Printf("Profile read failed (%s)", dbErrorKind(err))
	return nil, http.StatusServiceUnavailable
}

func profileBody(ctx context.Context, r *http.Request, store profileStore, resolver *sessionResolver, user *profileUser,
	followers, following []profileSummary) (map[string]any, int) {
	body := map[string]any{
		"username": user.Username, "displayname": user.Displayname, "profile_picture": nullable(user.ProfilePicture),
		"bio": nullable(user.Bio), "admin_badge": user.AdminBadge, "posts_count": 0,
		"followers": followers, "following": following,
		"followers_count": user.FollowersCount, "following_count": user.FollowingCount,
		"self": false, "viewer": nil,
	}
	if body["displayname"] == "" {
		body["displayname"] = user.Username
	}
	if r.Header.Get("Cookie") == "" || resolver == nil {
		return body, http.StatusOK
	}
	resolveCtx, cancel := context.WithTimeout(ctx, 1500*time.Millisecond)
	principal, err := resolver.Resolve(resolveCtx, r, authScope{})
	cancel()
	if err != nil {
		log.Print("Profile could not read the session")
		body["viewer_unverified"] = true
		return body, http.StatusOK
	}
	if !principal.Authenticated {
		return body, http.StatusOK
	}
	viewerID, parseErr := bson.ObjectIDFromHex(principal.ActorID)
	if parseErr != nil {
		body["viewer_unverified"] = true
		return body, http.StatusOK
	}
	if viewerID == user.ID {
		orgs, err := store.Organizations(ctx, firstN(user.FollowedOrgs, profileMaxOwnList))
		if err == nil {
			var recurring []profileRecurring
			if recurring, err = store.Recurring(ctx, firstN(user.FollowedRecurring, profileMaxOwnList)); err == nil {
				body["self"] = true
				body["followed_organizations"] = firstN(orgs, profileMaxOwnList)
				body["followed_recurring"] = firstN(recurring, profileMaxOwnList)
				return body, http.StatusOK
			}
		}
		log.Printf("Profile own lists failed (%s)", dbErrorKind(err))
		return nil, http.StatusServiceUnavailable
	}
	account, err := store.Account(ctx, viewerID)
	if err != nil || account == nil {
		if err != nil {
			log.Printf("Profile viewer read failed (%s)", dbErrorKind(err))
		}
		body["viewer_unverified"] = true
		return body, http.StatusOK
	}
	body["viewer"] = map[string]any{
		"following":    profileContains(account.Following, user.ID.Hex()),
		"friend_state": profileFriendState(account, viewerID.Hex(), user),
	}
	return body, http.StatusOK
}
