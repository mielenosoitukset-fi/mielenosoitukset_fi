package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

// Campaign opening interest signup (docs/campaign-opening-interest.md).
//
// A third list on the fundraising reminder system (docs/fundraising-reminder.md):
// the same guards, rate limits, double opt-in, hashed one-time tokens and
// one-click removal, with its own collection, routes and fields. Go stores the
// signup and consumes the links; Flask mints the tokens and sends the mail.

const (
	openingPath            = "/api/campaign-opening/interest"
	openingConfirmPath     = openingPath + "/confirm"
	openingUnsubscribePath = openingPath + "/unsubscribe"
	openingCollection      = "campaign_opening_interest"
	openingMaxBody         = 4 << 10
	// openingConsentVersion names the consent text on the form; change it together
	// with the consent line in frontend/src/components/OpeningInterestForm.tsx.
	openingConsentVersion = "2026-11-16"
)

var openingAcceptedMessage = "Jos osoite on kelvollinen, lähetämme siihen vahvistusviestin. Ilmoittautuminen tulee voimaan vasta, kun olet vahvistanut sen viestin linkistä."

var openingWilling = map[string]bool{"": true, "yes": true, "maybe": true, "no": true}
var openingBands = map[string]bool{"": true, "under10": true, "10to20": true, "over20": true}

func openingEnabledFromEnv() bool {
	return strings.EqualFold(strings.TrimSpace(os.Getenv("OPENING_INTEREST_SIGNUP_ENABLED")), "true")
}

// startOpeningInterestSignup decides at startup whether the routes are on. Like the
// reminder, they need public submissions and the unique e-mail index.
func startOpeningInterestSignup(db *mongo.Database, config submissionConfig) *reminderState {
	state := &reminderState{}
	if !openingEnabledFromEnv() {
		return state
	}
	if config.Origin == "" {
		log.Print("Opening interest signup disabled: public submissions are not configured")
		return state
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := checkListEmailIndex(ctx, db, openingCollection, "opening interest signup"); err != nil {
		log.Print("Opening interest signup disabled: ", err)
		return state
	}
	state.available.Store(true)
	return state
}

func openingInterestHandler(next http.Handler, db *mongo.Database, config submissionConfig, state *reminderState) http.Handler {
	return reminderListHandler(next, db, config, state, reminderList{
		signupPath: openingPath, confirmPath: openingConfirmPath, unsubscribePath: openingUnsubscribePath,
		collection: openingCollection, rateScope: "opening",
		unavailable: "Ilmoittautuminen ei ole käytettävissä.",
		signup:      serveOpeningSignup,
	})
}

type openingSignupInput struct {
	Email   string `json:"email"`
	City    string `json:"city"`
	Willing string `json:"willing"`
	Band    string `json:"band"`
	Consent bool   `json:"consent"`
	Locale  string `json:"locale"`
	Website string `json:"website"`
}

// cleanOpeningText trims the value, drops control characters (a line break is
// kept in the note only) and reports false when it is not valid UTF-8 or longer
// than max characters. Nothing is rewritten beyond that: the page escapes text on
// output and the mails never include it.
func cleanOpeningText(value string, max int, multiline bool) (string, bool) {
	if !utf8.ValidString(value) {
		return "", false
	}
	value = strings.Map(func(r rune) rune {
		if r == '\n' && multiline {
			return r
		}
		if r == '\r' && multiline {
			return -1
		}
		// Format characters (right-to-left overrides, zero-width marks) can disguise text.
		if unicode.IsControl(r) || unicode.Is(unicode.Cf, r) || r == ' ' || r == ' ' {
			if multiline && r == '\t' {
				return ' '
			}
			return -1
		}
		return r
	}, value)
	value = strings.TrimSpace(value)
	return value, utf8.RuneCountInString(value) <= max
}

func serveOpeningSignup(ctx context.Context, w http.ResponseWriter, r *http.Request, db *mongo.Database, config submissionConfig) {
	var input openingSignupInput
	if err := readListJSON(w, r, &input, openingMaxBody); err != nil {
		reminderFailure(w, http.StatusBadRequest, "REMINDER_FORMAT", "Tarkista tiedot ja yritä uudelleen.")
		return
	}
	// A filled honeypot gets the normal answer and stores nothing.
	if strings.TrimSpace(input.Website) != "" {
		writeJSON(w, http.StatusAccepted, bson.M{"success": true, "message": openingAcceptedMessage})
		return
	}
	if !input.Consent {
		reminderFailure(w, http.StatusBadRequest, "OPENING_CONSENT", "Anna lupa tietojen tallentamiseen ennen lähettämistä.")
		return
	}
	city, cityOK := cleanOpeningText(input.City, 80, false)
	if !cityOK || !openingWilling[input.Willing] || !openingBands[input.Band] {
		reminderFailure(w, http.StatusBadRequest, "OPENING_FORMAT", "Tarkista tiedot ja yritä uudelleen.")
		return
	}
	email := normalizeReminderEmail(input.Email)
	if email == "" {
		reminderFailure(w, http.StatusBadRequest, "REMINDER_EMAIL", "Tarkista sähköpostiosoite.")
		return
	}
	// The price band only means something when the visitor might pay.
	band := input.Band
	if input.Willing != "yes" && input.Willing != "maybe" {
		band = ""
	}
	locale := "fi"
	if config.Locales[input.Locale] {
		locale = input.Locale
	}
	// Past the per-address limit the answer stays the same: nothing is stored
	// and the caller learns nothing about the address.
	perAddress, err := windowedRate(ctx, db, listEmailKey("opening-email", email, config.Secret), 2, 4)
	if err != nil {
		reminderFailure(w, http.StatusServiceUnavailable, "REMINDER_UNAVAILABLE", "Yritä uudelleen hetken kuluttua.")
		return
	}
	if perAddress {
		if err := storeOpeningSignup(ctx, db, openingRow{Email: email, City: city, Willing: input.Willing, Band: band, Locale: locale}); err != nil {
			log.Print("Opening interest signup could not be stored")
			reminderFailure(w, http.StatusServiceUnavailable, "REMINDER_SAVE_FAILED", "Ilmoittautumista ei voitu tallentaa. Yritä uudelleen.")
			return
		}
	}
	// The same answer for a new, a pending and an already confirmed address.
	writeJSON(w, http.StatusAccepted, bson.M{"success": true, "message": openingAcceptedMessage})
}

type openingRow struct {
	Email, City, Willing, Band, Locale string
}

// storeOpeningSignup is idempotent like storeReminderSignup: a new address gets
// a pending row; a repeat of a pending one may get its confirmation re-sent (at
// most three times, 15 minutes apart) and keeps the first answers; a confirmed
// one is left alone. Flask sends the mail.
func storeOpeningSignup(ctx context.Context, db *mongo.Database, row openingRow) error {
	rows := db.Collection(openingCollection)
	now := time.Now().UTC()
	result, err := rows.UpdateOne(ctx, bson.M{"email": row.Email}, bson.M{"$setOnInsert": bson.M{
		"email": row.Email, "city": row.City, "willing": row.Willing, "band": row.Band,
		"status": "pending", "locale": row.Locale,
		"consent_at": now, "consent_version": openingConsentVersion,
		"created_at": now, "confirmation_requested_at": now, "confirmation_sent_at": nil, "confirmation_count": 0,
	}}, options.UpdateOne().SetUpsert(true))
	if mongo.IsDuplicateKeyError(err) {
		err = nil
		result = &mongo.UpdateResult{UpsertedCount: 0}
	}
	if err != nil {
		return err
	}
	if result.UpsertedCount > 0 {
		return nil
	}
	_, err = rows.UpdateOne(ctx, bson.M{
		"email": row.Email, "status": "pending",
		"confirmation_sent_at": bson.M{"$lt": now.Add(-reminderResendAfter)},
		"confirmation_count":   bson.M{"$lt": reminderMaxSends},
	}, bson.M{"$set": bson.M{"confirmation_requested_at": now, "confirmation_sent_at": nil}})
	return err
}
