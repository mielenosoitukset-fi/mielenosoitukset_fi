package main

// Accepting an organization invitation, moved from Flask's GET
// /users/orgs/accept_invite?organization_id=<id> (users/BPs/orgs.py). Flask joins the
// organization in the GET itself, so a link scanner, a prefetch or an image tag can make
// a signed-in member join. The React page now reads the invitation, shows a button, and
// this route does the write behind the same guards as the other signed-in Go writes
// (session through the auth bridge, same-origin fetch metadata, account-bound CSRF token
// from the read, per-account rate limit). Shipped dark: ORG_INVITE_GO_ENABLED=true is
// needed, on top of the auth bridge, and Apache does not route to it
// (docs/org-invite-react.md).
//
//	GET  /api/organizations/<id>/invitation         read the caller's invitation, issue a CSRF token
//	POST /api/organizations/<id>/invitation/accept  join (JSON body {})
//
// Parity with Flask's accept_invite:
//   - the invitation is matched by the account's exact e-mail address against
//     organizations.invitations (plain strings, or {email, invited_at}); a dict older
//     than 30 days is expired and ignored (utils/invitations.py);
//   - accepting removes every entry with that address and adds a membership with the
//     invited role's default permissions (admin or owner only when the invitation records
//     role_granted_by, otherwise member); an existing membership is left alone;
//   - system teams (NATIONAL_ADMIN_ORG_ID, BOARD_ORG_ID) refuse ordinary joining with the
//     same message as forbid_ordinary_team_write.
// Differences, on purpose: the removal is one conditional update on the exact invitation
// array, so two clicks cannot both join; the membership is only inserted when missing
// ($setOnInsert), so a racing role change is never overwritten; a failed membership write
// puts the invitation back; the join is audited in api_usage like the other Go writes.

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"os"
	"regexp"
	"strings"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

const (
	orgInviteLife = 30 * 24 * time.Hour
	orgInviteBody = 1 << 10
)

var orgInvitePath = regexp.MustCompile(`^/api/organizations/([0-9a-f]{24})/invitation(/accept)?$`)

// MemberShip.DEFAULT_PERMISSIONS["member"], sorted as MemberShip stores them.
var orgMemberPermissions = []string{"INVITE_TO_ORGANIZATION", "LIST_ORGANIZATIONS", "VIEW_ORGANIZATION"}

// MemberShip.DEFAULT_PERMISSIONS for every role, sorted as MemberShip stores them.
var orgRolePermissions = map[string][]string{
	"member": orgMemberPermissions,
	"admin": {"CREATE_DEMO", "CREATE_RECURRING_DEMO", "EDIT_DEMO", "EDIT_ORGANIZATION", "EDIT_RECURRING_DEMO",
		"GENERATE_EDIT_LINK", "INVITE_TO_ORGANIZATION", "LIST_DEMOS", "LIST_ORGANIZATIONS", "LIST_RECURRING_DEMOS",
		"VIEW_DEMO", "VIEW_ORGANIZATION", "VIEW_RECURRING_DEMO"},
	"owner": {"CREATE_DEMO", "CREATE_RECURRING_DEMO", "DELETE_DEMO", "DELETE_ORGANIZATION", "DELETE_RECURRING_DEMO",
		"EDIT_DEMO", "EDIT_ORGANIZATION", "EDIT_RECURRING_DEMO", "GENERATE_EDIT_LINK", "INVITE_TO_ORGANIZATION",
		"LIST_DEMOS", "LIST_ORGANIZATIONS", "LIST_RECURRING_DEMOS", "VIEW_DEMO", "VIEW_ORGANIZATION", "VIEW_RECURRING_DEMO"},
}

const orgProtectedMessage = "Tämän organisaation jäsenyyttä hallitaan hallinnon hyväksyntöjen kautta."

func orgInviteEnabledFromEnv() bool {
	return strings.EqualFold(strings.TrimSpace(os.Getenv("ORG_INVITE_GO_ENABLED")), "true")
}

// orgProtectedIDs reads the system team organizations. Both ids must be set and valid:
// a missing or malformed one disables the route, because refusing to run is safer than
// guessing which organization is a team (the security review of 20 November 2026).
func orgProtectedIDs() (map[bson.ObjectID]bool, bool) {
	ids := map[bson.ObjectID]bool{}
	for _, name := range []string{"NATIONAL_ADMIN_ORG_ID", "BOARD_ORG_ID"} {
		raw := strings.TrimSpace(os.Getenv(name))
		if raw == "" {
			return nil, false
		}
		id, err := bson.ObjectIDFromHex(raw)
		if err != nil || len(raw) != 24 {
			return nil, false
		}
		ids[id] = true
	}
	return ids, true
}

type orgInviteOrg struct {
	Name        string
	Invitations bson.A
}

type orgInviteStore interface {
	// AccountEmail returns the signed-in account's address; found is false when the account is gone.
	AccountEmail(ctx context.Context, user bson.ObjectID) (email string, found bool, err error)
	Organization(ctx context.Context, id bson.ObjectID) (*orgInviteOrg, error)
	// GovernanceManaged reports whether the organization carries governance-approved
	// memberships, the marker of a system team, whatever the configured ids say.
	GovernanceManaged(ctx context.Context, id bson.ObjectID) (bool, error)
	// ReplaceInvitations sets invitations to next only while they still equal expected.
	ReplaceInvitations(ctx context.Context, id bson.ObjectID, expected, next bson.A) (bool, error)
	// AddMember inserts a membership with the role's default permissions unless one
	// exists. A new admin or owner gets the admin panel (users.role "admin").
	AddMember(ctx context.Context, user, org bson.ObjectID, role string) error
	Audit(ctx context.Context, doc bson.M) error
}

type mongoOrgInviteStore struct{ db *mongo.Database }

func (s mongoOrgInviteStore) AccountEmail(ctx context.Context, user bson.ObjectID) (string, bool, error) {
	var doc struct {
		Email string `bson:"email"`
	}
	err := s.db.Collection("users").FindOne(ctx, bson.M{"_id": user}, options.FindOne().SetProjection(bson.M{"email": 1})).Decode(&doc)
	if errors.Is(err, mongo.ErrNoDocuments) {
		return "", false, nil
	}
	return doc.Email, err == nil, err
}

func (s mongoOrgInviteStore) Organization(ctx context.Context, id bson.ObjectID) (*orgInviteOrg, error) {
	var doc struct {
		Name        string `bson:"name"`
		Invitations bson.A `bson:"invitations"`
	}
	err := s.db.Collection("organizations").FindOne(ctx, bson.M{"_id": id}, options.FindOne().SetProjection(bson.M{"name": 1, "invitations": 1})).Decode(&doc)
	if errors.Is(err, mongo.ErrNoDocuments) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &orgInviteOrg{Name: doc.Name, Invitations: doc.Invitations}, nil
}

func (s mongoOrgInviteStore) GovernanceManaged(ctx context.Context, id bson.ObjectID) (bool, error) {
	count, err := s.db.Collection("memberships").CountDocuments(ctx,
		bson.M{"organization_id": bson.M{"$in": bson.A{id, id.Hex()}}, "governance_approved": true}, options.Count().SetLimit(1))
	return count > 0, err
}

func (s mongoOrgInviteStore) ReplaceInvitations(ctx context.Context, id bson.ObjectID, expected, next bson.A) (bool, error) {
	result, err := s.db.Collection("organizations").UpdateOne(ctx,
		bson.M{"_id": id, "invitations": expected}, bson.M{"$set": bson.M{"invitations": next}})
	if err != nil {
		return false, err
	}
	return result.ModifiedCount == 1, nil
}

func (s mongoOrgInviteStore) AddMember(ctx context.Context, user, org bson.ObjectID, role string) error {
	permissions, ok := orgRolePermissions[role]
	if !ok {
		role, permissions = "member", orgMemberPermissions
	}
	result, err := s.db.Collection("memberships").UpdateOne(ctx,
		bson.M{"user_id": user, "organization_id": org},
		bson.M{"$setOnInsert": bson.M{"role": role, "permissions": permissions}},
		options.UpdateOne().SetUpsert(true))
	if mongo.IsDuplicateKeyError(err) {
		return nil
	}
	if err != nil || result.UpsertedCount == 0 || role == "member" {
		return err
	}
	// change_access_level and Flask's accept give org admins and owners the admin panel.
	_, err = s.db.Collection("users").UpdateOne(ctx,
		bson.M{"_id": user, "role": bson.M{"$in": bson.A{"user", "member"}}}, bson.M{"$set": bson.M{"role": "admin"}})
	return err
}

func (s mongoOrgInviteStore) Audit(ctx context.Context, doc bson.M) error {
	_, err := s.db.Collection("api_usage").InsertOne(ctx, doc)
	return err
}

// invitationEmail returns the address of one stored invitation and whether it is
// still valid (Flask's _get_invited_emails and invitation_expired).
func invitationEntry(entry any, now time.Time) (email string, live bool) {
	switch value := entry.(type) {
	case string:
		return value, true
	case bson.D:
		fields := map[string]any{}
		for _, item := range value {
			fields[item.Key] = item.Value
		}
		address, ok := fields["email"].(string)
		if !ok {
			return "", false
		}
		if invited, ok := fields["invited_at"].(bson.DateTime); ok && now.Sub(invited.Time()) > orgInviteLife {
			return address, false
		}
		return address, true
	case bson.M:
		address, ok := value["email"].(string)
		if !ok {
			return "", false
		}
		if invited, ok := value["invited_at"].(bson.DateTime); ok && now.Sub(invited.Time()) > orgInviteLife {
			return address, false
		}
		return address, true
	}
	return "", false
}

// invitationRole is the role a stored invitation grants (Flask's invited_role): admin
// or owner only when set_invite_role recorded who checked it (role_granted_by).
func invitationRole(entry any) string {
	var fields map[string]any
	switch value := entry.(type) {
	case bson.D:
		fields = map[string]any{}
		for _, item := range value {
			fields[item.Key] = item.Value
		}
	case bson.M:
		fields = value
	default:
		return "member"
	}
	role, _ := fields["role"].(string)
	if _, known := orgRolePermissions[role]; !known || fields["role_granted_by"] == nil {
		return "member"
	}
	return role
}

// invitedRole is the role of the live invitation for email.
func invitedRole(invitations bson.A, email string, now time.Time) string {
	for _, entry := range invitations {
		if address, live := invitationEntry(entry, now); live && address == email && email != "" {
			return invitationRole(entry)
		}
	}
	return "member"
}

// invitationState says whether the address holds a live invitation and returns the
// invitation list without any entry for that address (Flask drops expired ones too).
func invitationState(invitations bson.A, email string, now time.Time) (invited bool, remaining bson.A) {
	remaining = bson.A{}
	for _, entry := range invitations {
		address, live := invitationEntry(entry, now)
		if address == email && email != "" {
			if live {
				invited = true
			}
			continue
		}
		// Flask keeps strings and dicts with another address; anything else it drops.
		switch entry.(type) {
		case string, bson.D, bson.M:
			remaining = append(remaining, entry)
		}
	}
	return invited, remaining
}

type orgInvites struct {
	store     orgInviteStore
	guard     *developerWrites // guards and CSRF only; its store is never used
	protected map[bson.ObjectID]bool
	now       func() time.Time
}

const (
	inviteStateInvited     = "invited"
	inviteStateNotInvited  = "not-invited"
	inviteStateNoOrg       = "organization-not-found"
	inviteStateProtected   = "protected"
	inviteStateJoined      = "joined"
	inviteStateUnavailable = "unavailable"
)

// resolve reads everything both routes need.
func (o *orgInvites) resolve(ctx context.Context, user, orgID bson.ObjectID) (state string, org *orgInviteOrg, email string, err error) {
	org, err = o.store.Organization(ctx, orgID)
	if err != nil {
		return "", nil, "", err
	}
	if org == nil {
		return inviteStateNoOrg, nil, "", nil
	}
	if o.protected[orgID] {
		return inviteStateProtected, org, "", nil
	}
	managed, err := o.store.GovernanceManaged(ctx, orgID)
	if err != nil {
		return "", nil, "", err
	}
	if managed {
		return inviteStateProtected, org, "", nil
	}
	email, found, err := o.store.AccountEmail(ctx, user)
	if err != nil {
		return "", nil, "", err
	}
	if !found {
		return "", nil, "", errors.New("account document missing")
	}
	invited, _ := invitationState(org.Invitations, email, o.now().UTC())
	if !invited {
		return inviteStateNotInvited, org, email, nil
	}
	return inviteStateInvited, org, email, nil
}

func orgInviteHandler(next http.Handler, store orgInviteStore, resolver *sessionResolver, origin string, protected map[bson.ObjectID]bool) http.Handler {
	guard := newDeveloperWrites(nil, origin)
	if guard == nil || resolver == nil {
		return next
	}
	invites := &orgInvites{store: store, guard: guard, protected: protected, now: time.Now}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		match := orgInvitePath.FindStringSubmatch(r.URL.Path)
		if match == nil {
			next.ServeHTTP(w, r)
			return
		}
		orgID, err := bson.ObjectIDFromHex(match[1])
		if err != nil {
			writeError(w, http.StatusNotFound, "not found")
			return
		}
		accept := match[2] != ""
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Vary", "Cookie")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		if accept && r.Method != http.MethodPost {
			w.Header().Set("Allow", "POST")
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		if !accept && r.Method != http.MethodGet && r.Method != http.MethodHead {
			w.Header().Set("Allow", "GET, HEAD")
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		resolver.RequireAuthenticated(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			principal, _ := PrincipalFromContext(r.Context())
			user, err := bson.ObjectIDFromHex(principal.ActorID)
			if err != nil {
				authDenied(w, http.StatusServiceUnavailable, "session unavailable")
				return
			}
			if accept {
				invites.guard.guarded(w, r, user, func(ctx context.Context) error { return invites.accept(ctx, w, r, user, orgID) })
				return
			}
			invites.read(w, r, user, orgID)
		})).ServeHTTP(w, r)
	})
}

func (o *orgInvites) read(w http.ResponseWriter, r *http.Request, user, orgID bson.ObjectID) {
	if site := r.Header.Get("Sec-Fetch-Site"); site != "" && site != "same-origin" && site != "none" {
		writeError(w, http.StatusForbidden, "forbidden")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()
	state, org, _, err := o.resolve(ctx, user, orgID)
	if err != nil {
		log.Printf("Invitation read failed (%s)", dbErrorKind(err))
		writeError(w, http.StatusServiceUnavailable, "service unavailable")
		return
	}
	payload := bson.M{"state": state}
	// The name is shown only to the invited account: never to anyone who merely guesses an id.
	if state == inviteStateInvited && org != nil {
		payload["organization"] = bson.M{"name": org.Name}
		payload["csrf"] = o.guard.csrfToken(user.Hex())
	}
	writeJSON(w, http.StatusOK, payload)
}

func (o *orgInvites) accept(ctx context.Context, w http.ResponseWriter, r *http.Request, user, orgID bson.ObjectID) error {
	// The body carries nothing: the organization is in the path. Refuse anything else.
	if body := http.MaxBytesReader(w, r.Body, orgInviteBody); body != nil {
		var empty map[string]any
		if err := decodeStrictJSON(body, &empty); err != nil || len(empty) != 0 {
			writeError(w, http.StatusBadRequest, "invalid request")
			return nil
		}
	}
	state, org, email, err := o.resolve(ctx, user, orgID)
	if err != nil {
		return err
	}
	if state != inviteStateInvited {
		status := http.StatusOK
		if state == inviteStateProtected {
			status = http.StatusForbidden
		}
		writeJSON(w, status, bson.M{"state": state, "success": false})
		return nil
	}
	_, remaining := invitationState(org.Invitations, email, o.now().UTC())
	role := invitedRole(org.Invitations, email, o.now().UTC())
	// One conditional update claims the invitation: a second click or a changed list loses.
	claimed, err := o.store.ReplaceInvitations(ctx, orgID, org.Invitations, remaining)
	if err != nil {
		return err
	}
	if !claimed {
		writeJSON(w, http.StatusOK, bson.M{"state": inviteStateNotInvited, "success": false})
		return nil
	}
	if err := o.store.AddMember(ctx, user, orgID, role); err != nil {
		// Put the invitation back so the visitor can try again; best effort.
		restoreCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 3*time.Second)
		defer cancel()
		if _, restoreErr := o.store.ReplaceInvitations(restoreCtx, orgID, remaining, org.Invitations); restoreErr != nil {
			log.Printf("Invitation could not be restored (%s)", dbErrorKind(restoreErr))
		}
		return err
	}
	doc := bson.M{"user_id": user, "action": "organization invitation accepted", "organization_id": orgID, "role": role,
		"ip_address": submissionClientAddress(r), "timestamp": bson.NewDateTimeFromTime(o.now().UTC()), "source": "go-api"}
	// A failed audit write must not hide a join that already happened.
	if err := o.store.Audit(ctx, doc); err != nil {
		log.Printf("Invitation audit write failed (%s)", dbErrorKind(err))
	}
	writeJSON(w, http.StatusOK, bson.M{"state": inviteStateJoined, "success": true, "organization": bson.M{"name": org.Name}})
	return nil
}

// orgInviteFromEnv wires the routes inside the auth bridge, or returns next unchanged.
func orgInviteFromEnv(next http.Handler, db *mongo.Database, resolver *sessionResolver, origin string) http.Handler {
	if !orgInviteEnabledFromEnv() || resolver == nil {
		return next
	}
	protected, ok := orgProtectedIDs()
	if !ok {
		log.Print("Organization invitations disabled: NATIONAL_ADMIN_ORG_ID and BOARD_ORG_ID must both be valid")
		return next
	}
	return orgInviteHandler(next, mongoOrgInviteStore{db}, resolver, origin, protected)
}

// decodeStrictJSON reads exactly one JSON object and nothing after it. An empty body
// counts as {} so a button can post without one.
func decodeStrictJSON(body io.Reader, into *map[string]any) error {
	decoder := json.NewDecoder(body)
	err := decoder.Decode(into)
	if errors.Is(err, io.EOF) {
		*into = map[string]any{}
		return nil
	}
	if err != nil {
		return err
	}
	if _, err := decoder.Token(); !errors.Is(err, io.EOF) {
		return errors.New("trailing data")
	}
	return nil
}
