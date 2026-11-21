package main

// POST /api/campaign-volunteers/confirm: the write behind the React page at
// /kampanja/confirm/<token>. Flask's GET /kampanja/confirm/<token> confirms the
// volunteer signup in the GET itself, so a link scanner or prefetch can confirm
// a signup by accident. The React page now shows a button and this route does
// the write. Shipped dark: the route answers only when
// VOLUNTEER_CONFIRM_GO_ENABLED=true and public submissions are configured, and
// Apache does not send the React page there yet (docs/volunteer-confirm-react.md).
//
// Parity with Flask's confirm_volunteer (mielenosoitukset_fi/kampanja/routes.py):
//   - the token matches the stored SHA-256 hash, or an older document's plain
//     confirmation_token;
//   - unknown or already used token: "invalid", HTTP 400;
//   - expired link (confirmation_expires < now): "expired", HTTP 400, the row is
//     left as it is;
//   - success: confirmed=true, token fields removed, HTTP 200.
// Unlike Flask the check and the write are one atomic update, so two clicks at
// once cannot both succeed. Go sends no mail and writes nothing else.

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
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
	volunteerConfirmPath    = "/api/campaign-volunteers/confirm"
	volunteerConfirmMaxBody = 1 << 10
)

// Flask issues secrets.token_urlsafe(16): 22 characters from this alphabet.
var volunteerTokenPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)

func volunteerConfirmEnabledFromEnv() bool {
	return strings.EqualFold(strings.TrimSpace(os.Getenv("VOLUNTEER_CONFIRM_GO_ENABLED")), "true")
}

const (
	volunteerConfirmed = "success"
	volunteerInvalid   = "invalid"
	volunteerExpired   = "expired"
)

// volunteerConfirmStore is the one write. Consume reports "success", "invalid"
// or "expired" and changes the row only on success.
type volunteerConfirmStore interface {
	Consume(ctx context.Context, tokenHash, plainToken string, now time.Time) (string, error)
}

// tokenConfirmGuards are the checks that run before a token-confirm write. They
// are a struct so the tests can drive a handler without a database. The same guards
// serve every confirm-by-POST route (volunteers, e-mail confirmation, invites).
type tokenConfirmGuards struct {
	origin string
	// panic reports an active global or security panic switch.
	panic func(ctx context.Context) (bool, error)
	// allow counts the request against the per-client limits.
	allow func(ctx context.Context, r *http.Request) (bool, error)
	now   func() time.Time
}

type mongoVolunteerConfirm struct{ db *mongo.Database }

func (s mongoVolunteerConfirm) Consume(ctx context.Context, tokenHash, plainToken string, now time.Time) (string, error) {
	rows := s.db.Collection("volunteers")
	byToken := bson.M{"$or": bson.A{bson.M{"confirmation_token_hash": tokenHash}, bson.M{"confirmation_token": plainToken}}}
	// One atomic step both checks the expiry and consumes the token. Flask treats
	// an expiry equal to now as still valid, so the bound is inclusive.
	err := rows.FindOneAndUpdate(ctx,
		bson.M{"$and": bson.A{byToken, bson.M{"confirmation_expires": bson.M{"$gte": now}}}},
		bson.M{
			"$set":   bson.M{"confirmed": true},
			"$unset": bson.M{"confirmation_token": "", "confirmation_token_hash": "", "confirmation_expires": ""},
		},
		options.FindOneAndUpdate().SetProjection(bson.M{"_id": 1})).Err()
	if err == nil {
		return volunteerConfirmed, nil
	}
	if !errors.Is(err, mongo.ErrNoDocuments) {
		return "", err
	}
	// Nothing was updated: tell an expired link from an unknown or used one.
	err = rows.FindOne(ctx, byToken, options.FindOne().SetProjection(bson.M{"_id": 1})).Err()
	if err == nil {
		return volunteerExpired, nil
	}
	if errors.Is(err, mongo.ErrNoDocuments) {
		return volunteerInvalid, nil
	}
	return "", err
}

func volunteerConfirmHandler(next http.Handler, store volunteerConfirmStore, guards tokenConfirmGuards) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != volunteerConfirmPath {
			next.ServeHTTP(w, r)
			return
		}
		ctx, cancel, ok := tokenConfirmPreflight(w, r, guards, "VOLUNTEER")
		if !ok {
			return
		}
		defer cancel()
		var input reminderTokenInput
		if err := readListJSON(w, r, &input, volunteerConfirmMaxBody); err != nil {
			reminderFailure(w, http.StatusBadRequest, "VOLUNTEER_FORMAT", "Tarkista tiedot ja yritä uudelleen.")
			return
		}
		if !volunteerTokenPattern.MatchString(input.Token) {
			writeJSON(w, http.StatusBadRequest, bson.M{"success": false, "status": volunteerInvalid})
			return
		}
		sum := sha256.Sum256([]byte(input.Token))
		status, err := store.Consume(ctx, hex.EncodeToString(sum[:]), input.Token, guards.now().UTC())
		if err != nil {
			reminderFailure(w, http.StatusServiceUnavailable, "VOLUNTEER_UNAVAILABLE", "Yritä uudelleen hetken kuluttua.")
			return
		}
		if status != volunteerConfirmed {
			writeJSON(w, http.StatusBadRequest, bson.M{"success": false, "status": status})
			return
		}
		writeJSON(w, http.StatusOK, bson.M{"success": true, "status": volunteerConfirmed})
	})
}

// volunteerConfirmFromEnv wires the route to MongoDB, or returns next unchanged
// while the flag is off.
func volunteerConfirmFromEnv(next http.Handler, db *mongo.Database, config submissionConfig) http.Handler {
	if !volunteerConfirmEnabledFromEnv() || config.Origin == "" || config.Secret == "" {
		return next
	}
	return volunteerConfirmHandler(next, mongoVolunteerConfirm{db}, tokenConfirmGuardsFromDB(db, config, "volunteer-confirm"))
}

// tokenConfirmPreflight runs the checks every confirm-by-POST route shares and
// answers the request itself when one fails: no-store headers, POST only, same
// origin, the panic switch, then the per-client rate limit. The returned context
// bounds the rest of the request; call cancel when done. prefix names the error
// codes, for example VOLUNTEER gives VOLUNTEER_ORIGIN.
func tokenConfirmPreflight(w http.ResponseWriter, r *http.Request, guards tokenConfirmGuards, prefix string) (context.Context, context.CancelFunc, bool) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", "POST")
		reminderFailure(w, http.StatusMethodNotAllowed, prefix+"_METHOD", "Käytä sivun painiketta.")
		return nil, nil, false
	}
	if r.Header.Get("Sec-Fetch-Site") == "cross-site" || !submissionOriginAllowed(r.Header.Get("Origin"), guards.origin) {
		reminderFailure(w, http.StatusForbidden, prefix+"_ORIGIN", "Lähetä tiedot sivuston painikkeella.")
		return nil, nil, false
	}
	ctx, cancel := context.WithTimeout(r.Context(), 8*time.Second)
	if active, err := guards.panic(ctx); err != nil || active {
		cancel()
		w.Header().Set("Retry-After", "60")
		reminderFailure(w, http.StatusServiceUnavailable, prefix+"_UNAVAILABLE", "Palvelu ei ole juuri nyt käytettävissä.")
		return nil, nil, false
	}
	allowed, err := guards.allow(ctx, r)
	if err != nil {
		cancel()
		reminderFailure(w, http.StatusServiceUnavailable, prefix+"_UNAVAILABLE", "Yritä uudelleen hetken kuluttua.")
		return nil, nil, false
	}
	if !allowed {
		cancel()
		w.Header().Set("Retry-After", "60")
		reminderFailure(w, http.StatusTooManyRequests, prefix+"_RATE_LIMITED", "Yrityksiä on liian monta. Odota hetki ja yritä uudelleen.")
		return nil, nil, false
	}
	return ctx, cancel, true
}

// tokenConfirmGuardsFromDB builds the production guards: the global/security panic
// switch and a per-client limit of 10 a minute and 60 an hour under scope.
func tokenConfirmGuardsFromDB(db *mongo.Database, config submissionConfig, scope string) tokenConfirmGuards {
	return tokenConfirmGuards{
		origin: config.Origin,
		panic: func(ctx context.Context) (bool, error) {
			var mode struct {
				Panic bool `bson:"panic"`
			}
			// Ask for an active switch: the collection holds one document per
			// switch, and the first one found may be the one that is off.
			err := db.Collection("panic").FindOne(ctx, bson.M{"name": bson.M{"$in": bson.A{"global", "security"}}, "panic": true}).Decode(&mode)
			if errors.Is(err, mongo.ErrNoDocuments) {
				return false, nil
			}
			return mode.Panic, err
		},
		allow: func(ctx context.Context, r *http.Request) (bool, error) {
			return windowedRate(ctx, db, scope+":"+submissionClient(r, config.Secret), 10, 60)
		},
		now: time.Now,
	}
}
