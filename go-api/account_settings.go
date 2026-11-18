package main

// Read-only data for the React settings page (docs/account-pages-react.md, step 2).
//
// GET /api/account/settings exists only when ACCOUNT_SETTINGS_READS_ENABLED=true
// and the auth bridge is on, because the signed-in account comes from the bridge.
// It answers for the caller's own account and never writes: Flask inserts a
// default user_settings row on first read, this route returns the same defaults
// without storing them.
//
// The answer never carries an email address, a password hash, an MFA secret, a
// passkey credential id or public key, or the account's own id.

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"os"
	"time"
	"unicode/utf8"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

const (
	accountSettingsPath     = "/api/account/settings"
	accountSettingsMaxItems = 50
	accountSettingsMaxLogs  = 30
	accountSettingsUAMax    = 120
	accountSettingsNameMax  = 100
	accountSettingsTextMax  = 5000
)

type accountGeneral struct {
	DisplayName string  `json:"display_name"`
	City        *string `json:"city"`
	Language    *string `json:"language"`
	DarkMode    *bool   `json:"dark_mode"`
}

type accountProfile struct {
	Bio            *string `json:"bio"`
	ProfilePicture *string `json:"profile_picture"`
}

type accountMFADevice struct {
	ID        string `json:"id"`
	Name      string `json:"name"`
	CreatedAt string `json:"created_at,omitempty"`
}

type accountPasskey struct {
	ID         string   `json:"id"`
	Name       string   `json:"name"`
	CreatedAt  string   `json:"created_at,omitempty"`
	LastUsedAt string   `json:"last_used_at,omitempty"`
	Transports []string `json:"transports"`
}

type accountLogin struct {
	Time   string `json:"time"`
	IP     string `json:"ip"`
	UA     string `json:"ua"`
	OK     bool   `json:"ok"`
	Reason string `json:"reason"`
}

// accountSettingsData is what the store knows about one account. The settings
// pointer is nil when the account has no user_settings document yet.
type accountSettingsData struct {
	Username    string
	Displayname string
	Bio         *string
	Picture     *string
	MFAEnabled  bool
	Settings    *accountGeneral
	Devices     []accountMFADevice
	Passkeys    []accountPasskey
	Logins      []accountLogin
}

// accountSettingsStore only reads; there is no write method to call by mistake.
type accountSettingsStore interface {
	// Load returns nil when the account has no document.
	Load(ctx context.Context, owner bson.ObjectID) (*accountSettingsData, error)
}

type mongoAccountSettingsStore struct{ db *mongo.Database }

func optionalString(doc bson.M, key string, max int) *string {
	s, ok := doc[key].(string)
	if !ok {
		return nil
	}
	s = truncateRunes(s, max)
	return &s
}

func truncateRunes(s string, max int) string {
	if utf8.RuneCountInString(s) <= max {
		return s
	}
	return string([]rune(s)[:max])
}

func findAll(ctx context.Context, db *mongo.Database, collection string, filter bson.M, opts *options.FindOptionsBuilder) ([]bson.M, error) {
	cursor, err := db.Collection(collection).Find(ctx, filter, opts)
	if err != nil {
		return nil, err
	}
	var docs []bson.M
	if err := cursor.All(ctx, &docs); err != nil {
		return nil, err
	}
	return docs, nil
}

func (s mongoAccountSettingsStore) Load(ctx context.Context, owner bson.ObjectID) (*accountSettingsData, error) {
	var user bson.M
	err := s.db.Collection("users").FindOne(ctx, bson.M{"_id": owner},
		options.FindOne().SetProjection(bson.M{"username": 1, "displayname": 1, "bio": 1, "profile_picture": 1, "mfa_enabled": 1})).Decode(&user)
	if errors.Is(err, mongo.ErrNoDocuments) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	data := &accountSettingsData{Username: stringField(user, "username"), Displayname: stringField(user, "displayname"),
		Bio: optionalString(user, "bio", accountSettingsTextMax), Picture: optionalString(user, "profile_picture", 2000)}
	data.MFAEnabled, _ = user["mfa_enabled"].(bool)

	var settings bson.M
	err = s.db.Collection("user_settings").FindOne(ctx, bson.M{"user_id": owner},
		options.FindOne().SetProjection(bson.M{"display_name": 1, "city": 1, "language": 1, "dark_mode": 1})).Decode(&settings)
	if err != nil && !errors.Is(err, mongo.ErrNoDocuments) {
		return nil, err
	}
	if err == nil {
		general := &accountGeneral{City: optionalString(settings, "city", 100), Language: optionalString(settings, "language", 10)}
		general.DisplayName = truncateRunes(stringField(settings, "display_name"), accountSettingsNameMax)
		if dark, ok := settings["dark_mode"].(bool); ok {
			general.DarkMode = &dark
		}
		data.Settings = general
	}

	devices, err := findAll(ctx, s.db, "mfas", bson.M{"user_id": owner}, options.Find().SetLimit(accountSettingsMaxItems).
		SetProjection(bson.M{"device_name": 1, "created_at": 1}))
	if err != nil {
		return nil, err
	}
	data.Devices = []accountMFADevice{}
	for _, doc := range devices {
		device := accountMFADevice{Name: truncateRunes(stringField(doc, "device_name"), accountSettingsNameMax), CreatedAt: isoTime(doc["created_at"])}
		if id, ok := doc["_id"].(bson.ObjectID); ok {
			device.ID = id.Hex()
		}
		data.Devices = append(data.Devices, device)
	}

	passkeys, err := findAll(ctx, s.db, "user_passkeys", bson.M{"user_id": owner}, options.Find().SetLimit(accountSettingsMaxItems).
		SetProjection(bson.M{"name": 1, "created_at": 1, "last_used_at": 1, "transports": 1}))
	if err != nil {
		return nil, err
	}
	data.Passkeys = []accountPasskey{}
	for _, doc := range passkeys {
		key := accountPasskey{Name: truncateRunes(stringField(doc, "name"), accountSettingsNameMax), CreatedAt: isoTime(doc["created_at"]),
			LastUsedAt: isoTime(doc["last_used_at"]), Transports: stringList(doc["transports"], []string{})}
		if id, ok := doc["_id"].(bson.ObjectID); ok {
			key.ID = id.Hex()
		}
		data.Passkeys = append(data.Passkeys, key)
	}

	logs, err := findAll(ctx, s.db, "login_logs", bson.M{"user_id": owner}, options.Find().SetLimit(accountSettingsMaxLogs).
		SetSort(bson.M{"timestamp": -1}).SetProjection(bson.M{"timestamp": 1, "ip": 1, "user_agent": 1, "success": 1, "reason": 1}))
	if err != nil {
		return nil, err
	}
	data.Logins = []accountLogin{}
	for _, doc := range logs {
		ok, _ := doc["success"].(bool)
		data.Logins = append(data.Logins, accountLogin{Time: isoTime(doc["timestamp"]), IP: truncateRunes(stringField(doc, "ip"), 64),
			UA: truncateRunes(stringField(doc, "user_agent"), accountSettingsUAMax), OK: ok, Reason: truncateRunes(stringField(doc, "reason"), 200)})
	}
	return data, nil
}

// accountCityNames lists the picker's cities in the catalog's order, which is
// the order of the Flask page's list.
var accountCityNames = func() []string {
	var rows []catalogCity
	if err := json.Unmarshal(cityCatalogJSON, &rows); err != nil {
		panic("invalid generated city catalog")
	}
	names := make([]string, 0, len(rows))
	for _, row := range rows {
		names = append(names, row.Name)
	}
	return names
}()

func accountSettingsPayload(data *accountSettingsData) map[string]any {
	general := accountGeneral{DisplayName: data.Displayname}
	if general.DisplayName == "" {
		general.DisplayName = data.Username
	}
	if data.Settings != nil {
		general = *data.Settings
	}
	general.DisplayName = truncateRunes(general.DisplayName, accountSettingsNameMax)
	return map[string]any{
		"general":       general,
		"profile":       accountProfile{Bio: data.Bio, ProfilePicture: data.Picture},
		"mfa":           map[string]any{"enabled": data.MFAEnabled, "devices": data.Devices},
		"passkeys":      data.Passkeys,
		"login_history": data.Logins,
		"cities":        accountCityNames,
	}
}

// accountSettingsReadHandler serves the route when its flag is on; it is only
// wired inside the auth bridge, so it cannot exist without the bridge.
func accountSettingsReadHandler(next http.Handler, db *mongo.Database, resolver *sessionResolver, origin string) http.Handler {
	if resolver == nil || os.Getenv("ACCOUNT_SETTINGS_READS_ENABLED") != "true" {
		return next
	}
	var writes *accountWrites
	if os.Getenv("ACCOUNT_SETTINGS_WRITES_ENABLED") == "true" {
		writes = newAccountWrites(mongoAccountWriteStore{db}, origin)
	}
	return accountSettingsHandler(next, mongoAccountSettingsStore{db}, resolver, writes)
}

// accountSettingsHandler serves the read route and, when writes is not nil, the
// write routes (account_settings_write.go). With nil they are unknown paths.
func accountSettingsHandler(next http.Handler, store accountSettingsStore, resolver *sessionResolver, writes *accountWrites) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if writes != nil && r.URL.Path == accountProfilePath {
			writes.serveProfile(w, r, resolver)
			return
		}
		if r.URL.Path != accountSettingsPath {
			next.ServeHTTP(w, r)
			return
		}
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Vary", "Cookie")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			w.Header().Set("Allow", "GET, HEAD")
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		if site := r.Header.Get("Sec-Fetch-Site"); site != "" && site != "same-origin" && site != "none" {
			writeError(w, http.StatusForbidden, "forbidden")
			return
		}
		resolver.RequireAuthenticated(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			principal, _ := PrincipalFromContext(r.Context())
			owner, err := bson.ObjectIDFromHex(principal.ActorID)
			if err != nil {
				authDenied(w, http.StatusServiceUnavailable, "session unavailable")
				return
			}
			ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
			defer cancel()
			data, err := store.Load(ctx, owner)
			if err == nil && data == nil {
				// The session names an account that has no document: not a page state, a failure.
				err = errors.New("account document missing")
			}
			if err != nil {
				log.Printf("Account settings read failed (%s)", dbErrorKind(err))
				writeError(w, http.StatusServiceUnavailable, "service unavailable")
				return
			}
			payload := accountSettingsPayload(data)
			if writes != nil {
				payload["writes"] = true
				payload["csrf"] = writes.guard.csrfToken(owner.Hex())
			}
			writeJSON(w, http.StatusOK, payload)
		})).ServeHTTP(w, r)
	})
}
