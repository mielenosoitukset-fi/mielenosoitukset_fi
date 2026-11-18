package main

// General settings write for the React settings page (docs/account-pages-react.md, step 3b).
//
// POST /api/account/settings changes display_name, city, language and dark_mode
// in user_settings, like Flask's settings_api_v2, and queues the same change
// notice mail in email_queue. Go never sends mail: Flask's worker does.
// subscribe_newsletter stays in Flask. Same guards and flags as the bio write.

import (
	"context"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"reflect"
	"strings"
	"unicode"
	"unicode/utf8"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

const (
	accountDisplayNameMax = 40
	accountCityMax        = 100
	settingsMailSubject   = "Käyttäjäasetusten muutokset"
	settingsReservedText  = "Admin-nimitys on varattu palvelun sisäiseen käyttöön."
)

// settingsFieldOrder is the order of the fields in the mail and the answer.
// Flask walks a set, so its order changes between processes; Go fixes one.
var settingsFieldOrder = []string{"city", "display_name", "language", "dark_mode"}

// accountSettingsState is what the general write needs to know about one account.
type accountSettingsState struct {
	Email       string
	Username    string
	Displayname string
	Settings    map[string]any // nil when the account has no user_settings document
}

var reservedIdentityNames = map[string]bool{"admin": true, "administrator": true}

// isReservedIdentityName is validators.is_reserved_identity_name: strip, casefold
// (Python's str.casefold, so "adminiſtrator" folds to "administrator"), drop one
// leading "@" and strip again. Fullwidth letters do not fold to ASCII in either.
func isReservedIdentityName(value string) bool {
	text := contactCasefold(strings.TrimSpace(value))
	if strings.HasPrefix(text, "@") {
		text = strings.TrimSpace(text[1:])
	}
	return reservedIdentityNames[text]
}

var accountCitySet = func() map[string]bool {
	set := map[string]bool{}
	for _, name := range accountCityNames {
		set[name] = true
	}
	return set
}()

// parseGeneralSettings validates the body; message is a Finnish text for a 400.
func parseGeneralSettings(raw map[string]json.RawMessage) (values map[string]any, message string) {
	const bad = "Virheellinen pyyntö."
	values = map[string]any{}
	for key, value := range raw {
		if string(value) == "null" {
			return nil, bad
		}
		switch key {
		case "display_name":
			var text string
			if json.Unmarshal(value, &text) != nil {
				return nil, bad
			}
			if isReservedIdentityName(text) {
				return nil, settingsReservedText
			}
			text = strings.TrimSpace(text)
			count := utf8.RuneCountInString(text)
			if count < 1 || count > accountDisplayNameMax {
				return nil, "Näyttönimessä saa olla 1–40 merkkiä."
			}
			if strings.IndexFunc(text, unicode.IsControl) >= 0 {
				return nil, "Näyttönimi sisältää kiellettyjä merkkejä."
			}
			values[key] = text
		case "city":
			var text string
			if json.Unmarshal(value, &text) != nil || utf8.RuneCountInString(text) > accountCityMax {
				return nil, bad
			}
			if text != "" && !accountCitySet[text] {
				return nil, "Valitse kaupunki luettelosta."
			}
			values[key] = text
		case "language":
			var text string
			if json.Unmarshal(value, &text) != nil || text != "fi" {
				return nil, bad
			}
			values[key] = text
		case "dark_mode":
			var flag bool
			if json.Unmarshal(value, &flag) != nil {
				return nil, bad
			}
			values[key] = flag
		default:
			return nil, bad
		}
	}
	return values, ""
}

// settingsMailValue is {{ values.old if values.old is not none else "ei asetettu" }}.
func settingsMailValue(value any) string {
	if value == nil {
		return "ei asetettu"
	}
	return jinjaEscape.Replace(pyStr(value))
}

// renderSettingsMail is auth/settings_changed.html.
func renderSettingsMail(userName string, fields []string, changed map[string]map[string]any) string {
	var out strings.Builder
	out.WriteString(settingsMailHead + jinjaEscape.Replace(userName) + settingsMailMid)
	for _, field := range fields {
		pair := changed[field]
		out.WriteString("\n                <li>\n                    <strong>" + jinjaEscape.Replace(field) + "</strong><br>\n" +
			"                    <span class=\"value-label\">Vanha arvo:</span> " + settingsMailValue(pair["old"]) + "<br>\n" +
			"                    <span class=\"value-label\">Uusi arvo:</span> " + settingsMailValue(pair["new"]) + "\n                </li>\n                ")
	}
	out.WriteString(settingsMailTail)
	return out.String()
}

func (a *accountWrites) updateGeneral(ctx context.Context, w http.ResponseWriter, r *http.Request, owner bson.ObjectID) error {
	var raw map[string]json.RawMessage
	data, err := io.ReadAll(http.MaxBytesReader(w, r.Body, accountWriteBody))
	if err == nil {
		err = json.Unmarshal(data, &raw)
	}
	if err != nil || raw == nil {
		writeError(w, http.StatusBadRequest, "Virheellinen pyyntö.")
		return nil
	}
	values, message := parseGeneralSettings(raw)
	if message != "" {
		writeError(w, http.StatusBadRequest, message)
		return nil
	}
	state, err := a.store.LoadSettings(ctx, owner)
	if err != nil {
		return err
	}
	if state == nil {
		writeError(w, http.StatusNotFound, "not found")
		return nil
	}
	current := state.Settings
	defaults := current == nil
	if defaults {
		// Flask inserts this document on first access.
		name := state.Displayname
		if name == "" {
			name = state.Username
		}
		current = map[string]any{"city": nil, "display_name": name, "language": nil, "dark_mode": nil, "subscribe_newsletter": nil}
	}
	changed := map[string]map[string]any{}
	set := bson.M{}
	fields := []string{}
	for _, field := range settingsFieldOrder {
		next, ok := values[field]
		if !ok {
			continue
		}
		old := current[field]
		if reflect.DeepEqual(old, next) {
			continue
		}
		changed[field] = map[string]any{"old": old, "new": next}
		set[field] = next
		fields = append(fields, field)
	}
	if len(changed) == 0 {
		writeJSON(w, http.StatusOK, map[string]any{"status": "success", "message": "Ei muutoksia asetuksissa.", "changed_fields": map[string]any{}})
		return nil
	}
	insert := bson.M{}
	if defaults {
		for key, value := range current {
			if _, taken := set[key]; !taken {
				insert[key] = value
			}
		}
	}
	var newName *string
	if name, ok := set["display_name"].(string); ok {
		newName = &name
	}
	if err := a.store.SaveSettings(ctx, owner, set, insert, newName); err != nil {
		return err
	}
	// Flask names the greeting after the stored displayname from before the change.
	userName := state.Displayname
	if userName == "" {
		userName = state.Username
	}
	if state.Email != "" {
		html := renderSettingsMail(userName, fields, changed)
		if err := a.store.QueueMail(ctx, settingsMailJob(state.Email, html, a.instance)); err != nil {
			log.Printf("Settings change mail not queued (%s)", dbErrorKind(err))
		}
	}
	// Field names only, never the values.
	doc := bson.M{"user_id": owner, "action": "settings updated", "fields": fields, "ip_address": submissionClientAddress(r),
		"timestamp": bson.NewDateTimeFromTime(a.guard.now().UTC()), "source": "go-api"}
	_ = a.store.Audit(ctx, doc)
	writeJSON(w, http.StatusOK, map[string]any{"status": "success", "message": "Asetukset päivitetty ja ilmoitus lähetetty.", "changed_fields": changed})
	return nil
}

// settingsMailJob has the shape of EmailJob.to_dict.
func settingsMailJob(recipient, html, instance string) bson.D {
	return bson.D{
		{Key: "subject", Value: settingsMailSubject}, {Key: "recipients", Value: bson.A{recipient}},
		{Key: "body", Value: html}, {Key: "html", Value: html}, {Key: "sender", Value: nil},
		{Key: "attachments", Value: bson.A{}}, {Key: "extra_headers", Value: bson.D{}},
		{Key: "instance_id", Value: instance}, {Key: "status", Value: "pending"}, {Key: "attempts", Value: 0},
	}
}

func (s mongoAccountWriteStore) LoadSettings(ctx context.Context, owner bson.ObjectID) (*accountSettingsState, error) {
	var user bson.M
	err := s.db.Collection("users").FindOne(ctx, bson.M{"_id": owner},
		options.FindOne().SetProjection(bson.M{"username": 1, "displayname": 1, "email": 1})).Decode(&user)
	if err == mongo.ErrNoDocuments {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	state := &accountSettingsState{Email: stringField(user, "email"), Username: stringField(user, "username"), Displayname: stringField(user, "displayname")}
	var settings bson.M
	err = s.db.Collection("user_settings").FindOne(ctx, bson.M{"user_id": owner}).Decode(&settings)
	if err == nil {
		state.Settings = map[string]any{}
		for _, field := range settingsFieldOrder {
			state.Settings[field] = settings[field]
		}
	} else if err != mongo.ErrNoDocuments {
		return nil, err
	}
	return state, nil
}

func (s mongoAccountWriteStore) SaveSettings(ctx context.Context, owner bson.ObjectID, set, onInsert bson.M, displayName *string) error {
	// users.displayname goes first: the change is computed against user_settings, so
	// if the second write fails a retry still sees the change and writes both again.
	if displayName != nil {
		if _, err := s.db.Collection("users").UpdateOne(ctx, bson.M{"_id": owner}, bson.M{"$set": bson.M{"displayname": *displayName}}); err != nil {
			return err
		}
	}
	update := bson.M{"$set": set}
	if len(onInsert) > 0 {
		update["$setOnInsert"] = onInsert
	}
	_, err := s.db.Collection("user_settings").UpdateOne(ctx, bson.M{"user_id": owner}, update, options.UpdateOne().SetUpsert(true))
	return err
}

func (s mongoAccountWriteStore) QueueMail(ctx context.Context, doc bson.D) error {
	_, err := s.db.Collection("email_queue").InsertOne(ctx, doc)
	return err
}
