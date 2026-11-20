package main

// Bio write for the React settings page (docs/account-pages-react.md, step 3a).
//
// POST /api/account/profile exists only when ACCOUNT_SETTINGS_WRITES_ENABLED=true,
// ACCOUNT_SETTINGS_READS_ENABLED=true and the auth bridge is on. It uses the same
// guards as the /developer writes (developerWrites.guarded): bridge sign-in,
// same-origin fetch metadata, Origin, JSON content type, an account-bound CSRF
// token from the read answer, and a per-account rate limit. It updates only
// users.bio of the signed-in account. The profile picture stays in Flask (S3).
// Run a single Go instance: the CSRF key and the rate limit live in process memory.

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"unicode"
	"unicode/utf8"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
)

const (
	accountProfilePath = "/api/account/profile"
	accountBioMax      = 5000
	accountWriteBody   = 40 << 10 // 5000 characters, up to 4 bytes each, plus JSON framing
)

// accountWriteStore holds every account write. Each call is scoped to the owner.
type accountWriteStore interface {
	// SetBio stores bio (nil clears it) and reports whether the account exists.
	SetBio(ctx context.Context, owner bson.ObjectID, bio *string) (bool, error)
	Audit(ctx context.Context, doc bson.M) error
	// LoadSettings returns nil when the account does not exist.
	LoadSettings(ctx context.Context, owner bson.ObjectID) (*accountSettingsState, error)
	// SaveSettings upserts user_settings ($set plus onInsert defaults) and, when
	// displayName is not nil, copies it to users.displayname.
	SaveSettings(ctx context.Context, owner bson.ObjectID, set, onInsert bson.M, displayName *string) error
	// QueueMail inserts one finished mail into email_queue; Flask's worker sends it.
	QueueMail(ctx context.Context, doc bson.D) error
}

type accountWrites struct {
	guard *developerWrites // guards only; its store is never used
	store accountWriteStore
	// instance is the instance_id on queued mail, as in EmailJob.
	instance string
	// deletions opens self-service deletion requests (account_deletion_request.go).
	deletions accountDeletionStore
}

func newAccountWrites(store accountWriteStore, origin string) *accountWrites {
	guard := newDeveloperWrites(nil, origin)
	if guard == nil {
		return nil
	}
	return &accountWrites{guard: guard, store: store, instance: newInstanceID()}
}

func (a *accountWrites) serveProfile(w http.ResponseWriter, r *http.Request, resolver *sessionResolver) {
	a.serve(w, r, resolver, a.updateBio)
}

// serveSettings is the general settings write (account_settings_general.go).
func (a *accountWrites) serveSettings(w http.ResponseWriter, r *http.Request, resolver *sessionResolver) {
	a.serve(w, r, resolver, a.updateGeneral)
}

func (a *accountWrites) serve(w http.ResponseWriter, r *http.Request, resolver *sessionResolver,
	update func(context.Context, http.ResponseWriter, *http.Request, bson.ObjectID) error) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Vary", "Cookie")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", "POST")
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	resolver.RequireAuthenticated(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		principal, _ := PrincipalFromContext(r.Context())
		owner, err := bson.ObjectIDFromHex(principal.ActorID)
		if err != nil {
			authDenied(w, http.StatusServiceUnavailable, "session unavailable")
			return
		}
		a.guard.guarded(w, r, owner, func(ctx context.Context) error { return update(ctx, w, r, owner) })
	})).ServeHTTP(w, r)
}

// stripBidi removes the bidirectional override and isolate characters
// (U+202A-U+202E, U+2066-U+2069), which can make text on the public profile read
// in a different order than it was typed.
func stripBidi(text string) string {
	return strings.Map(func(c rune) rune {
		if (c >= 0x202A && c <= 0x202E) || (c >= 0x2066 && c <= 0x2069) {
			return -1
		}
		return c
	}, text)
}

// cleanBio strips bidi override characters, trims the text and applies the limits;
// ok is false when it is refused. Flask stores any string; the 5000 character
// limit, the bidi stripping and the control character rule are stricter on purpose
// (docs/decisions.md).
func cleanBio(raw string) (string, bool) {
	text := strings.TrimSpace(strings.ReplaceAll(stripBidi(raw), "\r\n", "\n"))
	if utf8.RuneCountInString(text) > accountBioMax {
		return "", false
	}
	for _, c := range text {
		if unicode.IsControl(c) && c != '\n' && c != '\t' {
			return "", false
		}
	}
	return text, true
}

func (a *accountWrites) updateBio(ctx context.Context, w http.ResponseWriter, r *http.Request, owner bson.ObjectID) error {
	var body struct {
		Bio *string `json:"bio"`
	}
	data, err := io.ReadAll(http.MaxBytesReader(w, r.Body, accountWriteBody))
	if err == nil {
		err = json.NewDecoder(strings.NewReader(string(data))).Decode(&body)
	}
	if err != nil || body.Bio == nil {
		writeError(w, http.StatusBadRequest, "Virheellinen pyyntö.")
		return nil
	}
	text, ok := cleanBio(*body.Bio)
	if !ok {
		writeError(w, http.StatusBadRequest, "Esittely on liian pitkä tai sisältää kiellettyjä merkkejä.")
		return nil
	}
	var stored *string
	if text != "" {
		stored = &text
	}
	found, err := a.store.SetBio(ctx, owner, stored)
	if err != nil {
		return err
	}
	if !found {
		writeError(w, http.StatusNotFound, "not found")
		return nil
	}
	// The audit row never carries the text. A failed audit write must not hide a change that happened.
	doc := bson.M{"user_id": owner, "action": "profile bio updated", "ip_address": submissionClientAddress(r),
		"timestamp": bson.NewDateTimeFromTime(a.guard.now().UTC()), "source": "go-api"}
	_ = a.store.Audit(ctx, doc)
	writeJSON(w, http.StatusOK, map[string]any{"status": "success", "bio": stored})
	return nil
}

type mongoAccountWriteStore struct{ db *mongo.Database }

func (s mongoAccountWriteStore) SetBio(ctx context.Context, owner bson.ObjectID, bio *string) (bool, error) {
	var value any
	if bio != nil {
		value = *bio
	}
	res, err := s.db.Collection("users").UpdateOne(ctx, bson.M{"_id": owner}, bson.M{"$set": bson.M{"bio": value}})
	return err == nil && res.MatchedCount == 1, err
}

func (s mongoAccountWriteStore) Audit(ctx context.Context, doc bson.M) error {
	_, err := s.db.Collection("api_usage").InsertOne(ctx, doc)
	return err
}
