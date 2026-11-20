package main

// POST /api/account/confirm-email: the write behind the React page at
// /auth/confirm_email/<token>. Flask's GET sets users.confirmed in the GET itself, so
// a mail scanner that opens the registration link confirms an account nobody
// confirmed. The React page now shows a button and this route does the write.
// Shipped dark: the route answers only when EMAIL_CONFIRM_GO_ENABLED=true, a
// verification key is set and public submissions are configured; Apache does not send
// the React page there yet (docs/email-confirm-react.md).
//
// Parity with Flask's confirm_email (mielenosoitukset_fi/users/BPs/auth.py):
//   - the token is the HS256 JWT from utils/auth.py generate_confirmation_token,
//     carrying the address in "email"; a token with a "purpose" other than
//     "email_confirmation" (a password reset token) is refused, one without a
//     purpose still works for its hour;
//   - exp, iat and nbf are checked with PyJWT's 10 second leeway;
//   - the account is found by lower-cased "email_canonical", falling back to a
//     case-insensitive match on "email", and gets confirmed=true. Replaying the
//     link inside its hour is harmless and answers success again, as in Flask;
//   - a bad or expired token and an unknown account are different outcomes, but an
//     expired and a forged token look the same, as in Flask's one warning.
// Differences, on purpose: a token without "exp" is refused (Flask never mints one),
// and Go cannot clear the visitor's session["registration_email"].

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"regexp"
	"strings"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
	"golang.org/x/text/cases"
)

const (
	emailConfirmPath    = "/api/account/confirm-email"
	emailConfirmMaxBody = 4 << 10
	// jwtLeeway is PyJWT's leeway in utils/auth.py _verify_token.
	jwtLeeway = 10 * time.Second
)

var emailConfirmTokenPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,1000}\.[A-Za-z0-9_-]{1,1000}\.[A-Za-z0-9_-]{1,1000}$`)

func emailConfirmEnabledFromEnv() bool {
	return strings.EqualFold(strings.TrimSpace(os.Getenv("EMAIL_CONFIRM_GO_ENABLED")), "true")
}

// emailConfirmKeyFromEnv is the key Flask signs these tokens with (its SECRET_KEY).
// Anything shorter than 32 bytes is refused, so a typo cannot turn the route on
// with a guessable key.
func emailConfirmKeyFromEnv() []byte {
	key := strings.TrimSpace(os.Getenv("EMAIL_CONFIRM_JWT_KEY"))
	if len(key) < 32 {
		return nil
	}
	return []byte(key)
}

// confirmTokenResult is the verdict on a token before any database access.
type confirmTokenResult int

const (
	confirmTokenInvalid confirmTokenResult = iota
	confirmTokenValid
)

// verifyFlaskConfirmationToken mirrors verify_confirmation_token. It returns the
// address in the token when the signature, the algorithm, the times and the purpose
// all pass.
func verifyFlaskConfirmationToken(token string, key []byte, now time.Time) (string, confirmTokenResult) {
	if len(key) == 0 || !emailConfirmTokenPattern.MatchString(token) {
		return "", confirmTokenInvalid
	}
	parts := strings.Split(token, ".")
	headerBytes, err1 := base64.RawURLEncoding.DecodeString(parts[0])
	payloadBytes, err2 := base64.RawURLEncoding.DecodeString(parts[1])
	signature, err3 := base64.RawURLEncoding.DecodeString(parts[2])
	if err1 != nil || err2 != nil || err3 != nil {
		return "", confirmTokenInvalid
	}
	var header struct {
		Alg string `json:"alg"`
	}
	if json.Unmarshal(headerBytes, &header) != nil || header.Alg != "HS256" {
		return "", confirmTokenInvalid
	}
	mac := hmac.New(sha256.New, key)
	mac.Write([]byte(parts[0] + "." + parts[1]))
	if !hmac.Equal(mac.Sum(nil), signature) {
		return "", confirmTokenInvalid
	}
	var claims struct {
		Email   any      `json:"email"`
		Purpose *string  `json:"purpose"`
		Exp     *float64 `json:"exp"`
		Iat     *float64 `json:"iat"`
		Nbf     *float64 `json:"nbf"`
	}
	if json.Unmarshal(payloadBytes, &claims) != nil || claims.Exp == nil {
		return "", confirmTokenInvalid
	}
	seconds := float64(now.Unix())
	leeway := jwtLeeway.Seconds()
	if *claims.Exp < seconds-leeway {
		return "", confirmTokenInvalid
	}
	if claims.Iat != nil && *claims.Iat > seconds+leeway {
		return "", confirmTokenInvalid
	}
	if claims.Nbf != nil && *claims.Nbf > seconds+leeway {
		return "", confirmTokenInvalid
	}
	if claims.Purpose != nil && *claims.Purpose != "email_confirmation" {
		return "", confirmTokenInvalid
	}
	email, ok := claims.Email.(string)
	if !ok || email == "" {
		return "", confirmTokenInvalid
	}
	return email, confirmTokenValid
}

var emailFold = cases.Fold()

// normalizeAccountEmail mirrors validators.normalize_email: trimmed and case-folded.
func normalizeAccountEmail(email string) string {
	return emailFold.String(strings.TrimSpace(email))
}

type emailConfirmStore interface {
	// Confirm sets confirmed=true on the account with this address and reports
	// whether there is one.
	Confirm(ctx context.Context, canonicalEmail string) (bool, error)
}

type mongoEmailConfirm struct{ db *mongo.Database }

func (s mongoEmailConfirm) Confirm(ctx context.Context, email string) (bool, error) {
	users := s.db.Collection("users")
	var user struct {
		ID bson.ObjectID `bson:"_id"`
	}
	projection := options.FindOne().SetProjection(bson.M{"_id": 1})
	err := users.FindOne(ctx, bson.M{"email_canonical": email}, projection).Decode(&user)
	if errors.Is(err, mongo.ErrNoDocuments) {
		// Older accounts may have no canonical address: match like Flask's fallback.
		err = users.FindOne(ctx, bson.M{"email": bson.M{"$regex": "^" + regexp.QuoteMeta(email) + "$", "$options": "i"}}, projection).Decode(&user)
	}
	if errors.Is(err, mongo.ErrNoDocuments) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	// Setting an already true flag changes nothing, so a replayed link is harmless.
	if _, err := users.UpdateOne(ctx, bson.M{"_id": user.ID}, bson.M{"$set": bson.M{"confirmed": true}}); err != nil {
		return false, err
	}
	return true, nil
}

func emailConfirmHandler(next http.Handler, store emailConfirmStore, key []byte, guards tokenConfirmGuards) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != emailConfirmPath {
			next.ServeHTTP(w, r)
			return
		}
		ctx, cancel, ok := tokenConfirmPreflight(w, r, guards, "EMAIL_CONFIRM")
		if !ok {
			return
		}
		defer cancel()
		var input reminderTokenInput
		if err := readListJSON(w, r, &input, emailConfirmMaxBody); err != nil {
			reminderFailure(w, http.StatusBadRequest, "EMAIL_CONFIRM_FORMAT", "Tarkista tiedot ja yritä uudelleen.")
			return
		}
		email, verdict := verifyFlaskConfirmationToken(input.Token, key, guards.now().UTC())
		if verdict != confirmTokenValid {
			writeJSON(w, http.StatusBadRequest, bson.M{"success": false, "status": "invalid"})
			return
		}
		found, err := store.Confirm(ctx, normalizeAccountEmail(email))
		if err != nil {
			reminderFailure(w, http.StatusServiceUnavailable, "EMAIL_CONFIRM_UNAVAILABLE", "Yritä uudelleen hetken kuluttua.")
			return
		}
		if !found {
			writeJSON(w, http.StatusNotFound, bson.M{"success": false, "status": "user-not-found"})
			return
		}
		writeJSON(w, http.StatusOK, bson.M{"success": true, "status": "confirmed"})
	})
}

// emailConfirmFromEnv wires the route, or returns next unchanged while the flag is
// off, the key is missing or public submissions are not configured.
func emailConfirmFromEnv(next http.Handler, db *mongo.Database, config submissionConfig) http.Handler {
	key := emailConfirmKeyFromEnv()
	if !emailConfirmEnabledFromEnv() || key == nil || config.Origin == "" || config.Secret == "" {
		return next
	}
	return emailConfirmHandler(next, mongoEmailConfirm{db}, key, tokenConfirmGuardsFromDB(db, config, "email-confirm"))
}
