package main

// Organization account requests (slice O2 of the Hallinto organization onboarding plan,
// docs/organization-requests.md). Anyone may ask for an organization account, suggest an
// organization, or ask for onboarding, signed in or not. Go only accepts and stores the
// request and mails the applicant a private status link. Review, the channel proof and the
// decision belong to Hallinto (O3); creating the organization stays with Flask (O4).
//
//	POST /api/organization-requests          submit (JSON)
//	GET  /api/organization-requests/mine     the signed-in account's requests
//	POST /api/organization-requests/status   read one request by its status token (JSON {"token"})
//
// Shipped dark: ORG_REQUESTS_GO_ENABLED=true is needed and Apache must route the paths.
//
// An anonymous request starts as "unconfirmed". Its applicant gets a mail with the status
// link /organisaatiot/hakemus#<token>; the token sits in the fragment so it never reaches
// a server log or a referrer. Opening the link proves the address and moves the request to
// "received". A signed-in applicant with a confirmed account skips that step. Only a hash
// of the token is stored.

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"html"
	"image"
	_ "image/jpeg"
	"image/png"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strings"
	"text/template"
	"time"
	"unicode/utf8"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

const (
	orgRequestsPath       = "/api/organization-requests"
	orgRequestsMinePath   = "/api/organization-requests/mine"
	orgRequestsStatusPath = "/api/organization-requests/status"
	orgRequestMaxBody     = 16 << 10
	orgRequestLogoMax     = 1 << 20
	orgRequestLogoSide    = 2000
	orgRequestStatusPage  = "/organisaatiot/hakemus#"
	orgRequestMailSubject = "Organisaatiohakemuksesi Mielenosoitukset.fi-palvelussa"
	orgRequestCapRoute    = "org-request"
	// Unconfirmed requests whose link was never opened are dropped after this.
	orgRequestUnconfirmedLife = 7 * 24 * time.Hour
)

var (
	orgRequestAccess     = map[string]bool{"organization_account": true, "own_account": true, "invite": true}
	orgRequestToken      = regexp.MustCompile(`^[A-Za-z0-9_-]{16,64}$`)
	orgRequestBusinessID = regexp.MustCompile(`^[0-9]{7}-[0-9]$`)
)

func orgRequestsEnabledFromEnv() bool {
	return strings.EqualFold(strings.TrimSpace(os.Getenv("ORG_REQUESTS_GO_ENABLED")), "true")
}

type orgRequestInput struct {
	Represents  bool     `json:"represents"`
	Name        string   `json:"organization_name"`
	OrgEmail    string   `json:"organization_email"`
	Links       []string `json:"links"`
	BusinessID  string   `json:"business_id"`
	City        string   `json:"city"`
	Description string   `json:"description"`
	Applicant   string   `json:"applicant_name"`
	Email       string   `json:"applicant_email"`
	Role        string   `json:"applicant_role"`
	Access      string   `json:"access"`
	InviteEmail string   `json:"invite_email"`
	Onboarding  bool     `json:"onboarding"`
	Consent     bool     `json:"consent"`
	Honeypot    string   `json:"homepage"`
	StartedAtMS int64    `json:"started_at"`
	// Invitation is the ?kutsu= token of an outbound invitation mail (O6). It only links
	// the request to the invitation's case; it proves nothing about the organization.
	Invitation string `json:"invitation"`
}

func orgRequestText(value string, min, max int, code, message string) (string, error) {
	return boundedText(value, min, max, code, message)
}

func orgRequestEmail(value, code, message string) (string, error) {
	value = strings.ToLower(strings.TrimSpace(value))
	if utf8.RuneCountInString(value) > 254 || !campaignEmailValid(value) {
		return "", applicationError{code, message}
	}
	return value, nil
}

// orgRequestLinks keeps 1–5 http(s) links to the organization's public presence.
func orgRequestLinks(values []string) ([]string, error) {
	links := []string{}
	seen := map[string]bool{}
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value == "" {
			continue
		}
		if !strings.Contains(value, "://") {
			value = "https://" + value
		}
		parsed, err := url.Parse(value)
		if err != nil || (parsed.Scheme != "https" && parsed.Scheme != "http") || parsed.Host == "" ||
			!strings.Contains(parsed.Hostname(), ".") || parsed.User != nil || len(value) > 300 {
			return nil, applicationError{"ORG_LINK", "Tarkista verkkosivun tai somen osoite."}
		}
		if !seen[parsed.String()] {
			seen[parsed.String()] = true
			links = append(links, parsed.String())
		}
	}
	if len(links) < 1 || len(links) > 5 {
		return nil, applicationError{"ORG_LINK_COUNT", "Anna 1–5 linkkiä organisaation verkkosivuille tai someen."}
	}
	return links, nil
}

// validateOrgRequest returns the stored fields. A signed-in applicant's address comes
// from the account, so the form's address is only read for anonymous applicants.
func validateOrgRequest(input orgRequestInput, signedIn bool) (bson.M, error) {
	var err error
	fields := bson.M{"represents": input.Represents, "onboarding": input.Onboarding}
	org := bson.M{}
	if org["name"], err = orgRequestText(input.Name, 2, 120, "ORG_NAME", "Kirjoita organisaation nimi."); err != nil {
		return nil, err
	}
	if strings.TrimSpace(input.OrgEmail) != "" {
		if org["email"], err = orgRequestEmail(input.OrgEmail, "ORG_EMAIL", "Tarkista organisaation sähköpostiosoite."); err != nil {
			return nil, err
		}
	} else if input.Represents {
		return nil, applicationError{"ORG_EMAIL", "Kirjoita organisaation julkinen sähköpostiosoite."}
	}
	if org["links"], err = orgRequestLinks(input.Links); err != nil {
		return nil, err
	}
	if id := strings.TrimSpace(input.BusinessID); id != "" {
		if !orgRequestBusinessID.MatchString(id) {
			return nil, applicationError{"ORG_BUSINESS_ID", "Kirjoita Y-tunnus muodossa 1234567-8 tai jätä kenttä tyhjäksi."}
		}
		org["business_id"] = id
	}
	if city := strings.TrimSpace(input.City); city != "" {
		key := normalizeCityKey(city)
		if _, known := cityCatalog[key]; !known {
			return nil, applicationError{"ORG_CITY", "Valitse paikkakunta listasta tai jätä kenttä tyhjäksi."}
		}
		org["city"] = key
	}
	if org["description"], err = orgRequestText(input.Description, 0, 1500, "ORG_DESCRIPTION", "Kuvaus voi olla enintään 1 500 merkkiä."); err != nil {
		return nil, err
	}
	fields["organization"] = org
	applicant := bson.M{}
	if applicant["name"], err = orgRequestText(input.Applicant, 2, 120, "ORG_APPLICANT", "Kirjoita nimesi."); err != nil {
		return nil, err
	}
	if !signedIn {
		if applicant["email"], err = orgRequestEmail(input.Email, "ORG_APPLICANT_EMAIL", "Tarkista sähköpostiosoitteesi."); err != nil {
			return nil, err
		}
	}
	if applicant["role"], err = orgRequestText(input.Role, 0, 120, "ORG_ROLE", "Rooli voi olla enintään 120 merkkiä."); err != nil {
		return nil, err
	}
	fields["applicant"] = applicant
	if input.Represents {
		if !orgRequestAccess[input.Access] {
			return nil, applicationError{"ORG_ACCESS", "Valitse, miten organisaation tiliä hallitaan."}
		}
		if input.Access == "own_account" && !signedIn {
			return nil, applicationError{"ORG_ACCESS_SIGN_IN", "Kirjaudu sisään, jos haluat hallita organisaatiota omalla tililläsi."}
		}
		if input.Access == "organization_account" && org["email"] == nil {
			return nil, applicationError{"ORG_EMAIL", "Organisaatiotili tarvitsee organisaation sähköpostiosoitteen."}
		}
		fields["access"] = input.Access
		if input.Access == "invite" {
			if fields["invite_email"], err = orgRequestEmail(input.InviteEmail, "ORG_INVITE_EMAIL", "Tarkista osoite, johon kutsu lähetetään."); err != nil {
				return nil, err
			}
		}
	}
	if !input.Consent {
		return nil, applicationError{"ORG_CONSENT", "Hyväksy tietojen käsittely, jotta voimme käsitellä hakemuksen."}
	}
	return fields, nil
}

// readOrgRequest reads the form: plain JSON, or multipart/form-data with the JSON in a
// "request" part and an optional "logo" file. The logo comes back re-encoded as PNG.
func readOrgRequest(w http.ResponseWriter, r *http.Request, into *orgRequestInput) ([]byte, error) {
	if !strings.HasPrefix(r.Header.Get("Content-Type"), "multipart/form-data") {
		return nil, readOrgRequestJSON(w, r, into)
	}
	r.Body = http.MaxBytesReader(w, r.Body, orgRequestLogoMax+orgRequestMaxBody+4<<10)
	reader, err := r.MultipartReader()
	if err != nil {
		return nil, applicationError{"ORG_FORMAT", "Hakemusta ei voitu lukea."}
	}
	var logo []byte
	seen := map[string]bool{}
	for {
		part, err := reader.NextPart()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return nil, applicationError{"ORG_LOGO_SIZE", "Logo voi olla enintään 1 Mt."}
		}
		name := part.FormName()
		if seen[name] || (name != "request" && name != "logo") {
			return nil, applicationError{"ORG_FORMAT", "Hakemusta ei voitu lukea."}
		}
		seen[name] = true
		if name == "request" {
			decoder := json.NewDecoder(io.LimitReader(part, orgRequestMaxBody))
			decoder.DisallowUnknownFields()
			if err := decoder.Decode(into); err != nil {
				return nil, applicationError{"ORG_FORMAT", "Hakemusta ei voitu lukea."}
			}
			continue
		}
		data, err := io.ReadAll(io.LimitReader(part, orgRequestLogoMax+1))
		if err != nil || len(data) > orgRequestLogoMax {
			return nil, applicationError{"ORG_LOGO_SIZE", "Logo voi olla enintään 1 Mt."}
		}
		if len(data) > 0 {
			if logo, err = orgRequestLogo(data); err != nil {
				return nil, err
			}
		}
	}
	if !seen["request"] {
		return nil, applicationError{"ORG_FORMAT", "Hakemusta ei voitu lukea."}
	}
	return logo, nil
}

// orgRequestLogo accepts a PNG or JPEG of at most 2000×2000 pixels and re-encodes it
// as PNG, which drops metadata and anything hidden after the image data.
func orgRequestLogo(data []byte) ([]byte, error) {
	invalid := applicationError{"ORG_LOGO", "Logon pitää olla PNG- tai JPEG-kuva, enintään 2000 × 2000 pikseliä."}
	config, format, err := image.DecodeConfig(bytes.NewReader(data))
	if err != nil || (format != "png" && format != "jpeg") || config.Width < 1 || config.Height < 1 ||
		config.Width > orgRequestLogoSide || config.Height > orgRequestLogoSide {
		return nil, invalid
	}
	decoded, _, err := image.Decode(bytes.NewReader(data))
	if err != nil {
		return nil, invalid
	}
	var out bytes.Buffer
	if err := png.Encode(&out, decoded); err != nil {
		return nil, invalid
	}
	return out.Bytes(), nil
}

func readOrgRequestJSON(w http.ResponseWriter, r *http.Request, into any) error {
	if !strings.HasPrefix(r.Header.Get("Content-Type"), "application/json") {
		return applicationError{"ORG_FORMAT", "Lähetä hakemus sivuston lomakkeella."}
	}
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, orgRequestMaxBody))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(into); err != nil {
		return applicationError{"ORG_FORMAT", "Hakemusta ei voitu lukea."}
	}
	if _, err := decoder.Token(); !errors.Is(err, io.EOF) {
		return applicationError{"ORG_FORMAT", "Hakemusta ei voitu lukea."}
	}
	return nil
}

var orgRequestMailTemplate = template.Must(template.New("org-request").Funcs(template.FuncMap{"esc": html.EscapeString}).Parse(`<!DOCTYPE html>
<html lang="fi">
<head><meta charset="UTF-8"><title>{{esc .Subject}}</title></head>
<body style="font-family: Arial, sans-serif; color: #1b1b1b; line-height: 1.5;">
<p>Hei {{esc .Name}}!</p>
<p>Saimme organisaatiota <strong>{{esc .Organization}}</strong> koskevan hakemuksen.</p>
<p>Vahvista sähköpostiosoitteesi ja seuraa hakemuksen tilaa tästä linkistä:</p>
<p><a href="{{esc .Link}}" style="display: inline-block; padding: 10px 20px; background: #0033A0; color: #ffffff; text-decoration: none; border-radius: 5px;">Avaa hakemus</a></p>
<p>Linkki on henkilökohtainen. Älä jaa sitä. Jos et lähettänyt hakemusta, voit jättää tämän viestin huomiotta: vahvistamaton hakemus poistetaan 7 päivän kuluttua.</p>
<p>Mielenosoitukset.fi</p>
</body>
</html>`))

func renderOrgRequestMail(name, organization, link string) (string, error) {
	var out bytes.Buffer
	err := orgRequestMailTemplate.Execute(&out, struct{ Subject, Name, Organization, Link string }{orgRequestMailSubject, name, organization, link})
	return out.String(), err
}

func orgRequestMailJob(recipient, body string) bson.D {
	job := campaignWelcomeJob(recipient, body, contactUUID())
	for i := range job {
		if job[i].Key == "subject" {
			job[i].Value = orgRequestMailSubject
		}
	}
	return job
}

type orgRequestSummary struct {
	ID           string    `json:"id"`
	Organization string    `json:"organization"`
	Represents   bool      `json:"represents"`
	Onboarding   bool      `json:"onboarding"`
	Access       string    `json:"access,omitempty"`
	Status       string    `json:"status"`
	Message      string    `json:"message,omitempty"`
	CreatedAt    time.Time `json:"created_at"`
	UpdatedAt    time.Time `json:"updated_at"`
}

type orgRequestRow struct {
	ID           bson.ObjectID `bson:"_id"`
	Organization struct {
		Name string `bson:"name"`
	} `bson:"organization"`
	Represents bool      `bson:"represents"`
	Onboarding bool      `bson:"onboarding"`
	Access     string    `bson:"access"`
	Status     string    `bson:"status"`
	Message    string    `bson:"status_message"`
	CreatedAt  time.Time `bson:"created_at"`
	UpdatedAt  time.Time `bson:"updated_at"`
}

func (row orgRequestRow) summary() orgRequestSummary {
	return orgRequestSummary{ID: row.ID.Hex(), Organization: row.Organization.Name, Represents: row.Represents,
		Onboarding: row.Onboarding, Access: row.Access, Status: row.Status, Message: row.Message,
		CreatedAt: row.CreatedAt, UpdatedAt: row.UpdatedAt}
}

var orgRequestProjection = bson.M{"organization.name": 1, "represents": 1, "onboarding": 1, "access": 1,
	"status": 1, "status_message": 1, "created_at": 1, "updated_at": 1}

type orgRequests struct {
	db     *mongo.Database
	config submissionConfig
	actor  func(ctx context.Context, r *http.Request) (bson.ObjectID, error)
	token  func() (string, error)
	now    func() time.Time
	// mailCap counts one outgoing mail against the site-wide signup caps.
	mailCap func(ctx context.Context) (bool, error)
}

func orgRequestHandler(next http.Handler, db *mongo.Database, config submissionConfig) http.Handler {
	if !orgRequestsEnabledFromEnv() {
		return next
	}
	o := &orgRequests{db: db, config: config, token: campaignToken, now: time.Now,
		actor: func(ctx context.Context, r *http.Request) (bson.ObjectID, error) {
			return submissionActor(ctx, r, config)
		},
		mailCap: func(ctx context.Context) (bool, error) {
			return signupMailCapAllowed(ctx, db, orgRequestCapRoute, signupMailCapAdminLog(db))
		}}
	return o.handler(next)
}

func (o *orgRequests) handler(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path := r.URL.Path
		if path != orgRequestsPath && path != orgRequestsMinePath && path != orgRequestsStatusPath {
			next.ServeHTTP(w, r)
			return
		}
		w.Header().Set("Cache-Control", "private, no-store")
		w.Header().Set("Vary", "Cookie")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Referrer-Policy", "no-referrer")
		if o.config.Origin == "" {
			applicationFailure(w, 503, "ORG_UNAVAILABLE", "Lomake ei ole juuri nyt käytettävissä.")
			return
		}
		mine := path == orgRequestsMinePath
		if (mine && r.Method != "GET") || (!mine && r.Method != "POST") {
			w.Header().Set("Allow", map[bool]string{true: "GET", false: "POST"}[mine])
			applicationFailure(w, 405, "ORG_METHOD", "Käytä sivuston lomaketta.")
			return
		}
		if r.Header.Get("Sec-Fetch-Site") == "cross-site" || (!mine && !submissionOriginAllowed(r.Header.Get("Origin"), o.config.Origin)) {
			applicationFailure(w, 403, "ORG_ORIGIN", "Lähetä hakemus sivuston lomakkeella.")
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 8*time.Second)
		defer cancel()
		switch path {
		case orgRequestsStatusPath:
			o.serveStatus(ctx, w, r)
			return
		}
		actor, err := o.actor(ctx, r)
		if forcedStepPath(err) != "" {
			applicationFailure(w, 403, "ORG_ACCOUNT_STEP", "Viimeistele tilisi pakollinen muutos ensin.")
			return
		}
		if err != nil {
			applicationFailure(w, 503, "ORG_SESSION", "Istunnon tarkistaminen epäonnistui. Yritä uudelleen.")
			return
		}
		if mine {
			if actor.IsZero() {
				applicationFailure(w, 401, "ORG_SIGN_IN", "Kirjaudu sisään nähdäksesi hakemuksesi.")
				return
			}
			o.serveMine(ctx, w, actor)
			return
		}
		o.serveSubmit(ctx, w, r, actor)
	})
}

func (o *orgRequests) serveMine(ctx context.Context, w http.ResponseWriter, actor bson.ObjectID) {
	cursor, err := o.db.Collection("organization_requests").Find(ctx, bson.M{"applicant.user_id": actor},
		options.Find().SetProjection(orgRequestProjection).SetSort(bson.D{{Key: "created_at", Value: -1}}).SetLimit(20))
	if err != nil {
		applicationFailure(w, 503, "ORG_UNAVAILABLE", "Hakemuksia ei voitu hakea.")
		return
	}
	var rows []orgRequestRow
	if err := cursor.All(ctx, &rows); err != nil {
		applicationFailure(w, 503, "ORG_UNAVAILABLE", "Hakemuksia ei voitu hakea.")
		return
	}
	items := []orgRequestSummary{}
	for _, row := range rows {
		items = append(items, row.summary())
	}
	writeJSON(w, 200, bson.M{"success": true, "requests": items})
}

// serveStatus reads one request by its token. The first read of an unconfirmed request
// confirms the applicant's address: only that mailbox received the token.
func (o *orgRequests) serveStatus(ctx context.Context, w http.ResponseWriter, r *http.Request) {
	allowed, err := windowedRate(ctx, o.db, "org-status:"+submissionClient(r, o.config.Secret), 20, 120)
	if err != nil {
		applicationFailure(w, 503, "ORG_UNAVAILABLE", "Yritä uudelleen hetken kuluttua.")
		return
	}
	if !allowed {
		w.Header().Set("Retry-After", "60")
		applicationFailure(w, 429, "ORG_RATE_LIMITED", "Liian monta yritystä. Odota hetki ja yritä uudelleen.")
		return
	}
	var input struct {
		Token string `json:"token"`
	}
	if err := readOrgRequestJSON(w, r, &input); err != nil || !orgRequestToken.MatchString(input.Token) {
		applicationFailure(w, 404, "ORG_NOT_FOUND", "Hakemusta ei löytynyt. Tarkista linkki.")
		return
	}
	now := o.now().UTC()
	requests := o.db.Collection("organization_requests")
	hash := campaignDigest(input.Token)
	// Confirm in place, only while the unconfirmed request is still within its life.
	_, err = requests.UpdateOne(ctx, bson.M{"status_token_hash": hash, "status": "unconfirmed", "expires_at": bson.M{"$gt": now}},
		bson.M{"$set": bson.M{"status": "received", "email_confirmed_at": now, "updated_at": now, "case_pending": true},
			"$unset": bson.M{"expires_at": ""}})
	if err != nil {
		applicationFailure(w, 503, "ORG_UNAVAILABLE", "Yritä uudelleen hetken kuluttua.")
		return
	}
	var row orgRequestRow
	err = requests.FindOne(ctx, bson.M{"status_token_hash": hash, "status": bson.M{"$ne": "unconfirmed"}},
		options.FindOne().SetProjection(orgRequestProjection)).Decode(&row)
	if errors.Is(err, mongo.ErrNoDocuments) {
		applicationFailure(w, 404, "ORG_NOT_FOUND", "Hakemusta ei löytynyt. Linkki voi olla vanhentunut.")
		return
	}
	if err != nil {
		applicationFailure(w, 503, "ORG_UNAVAILABLE", "Yritä uudelleen hetken kuluttua.")
		return
	}
	writeJSON(w, 200, bson.M{"success": true, "request": row.summary()})
}

func (o *orgRequests) serveSubmit(ctx context.Context, w http.ResponseWriter, r *http.Request, actor bson.ObjectID) {
	if active, err := panicSwitchActive(ctx, o.db); err != nil || active {
		applicationFailure(w, 503, "ORG_UNAVAILABLE", "Palvelu ei ole juuri nyt käytettävissä.")
		return
	}
	allowed, err := windowedRate(ctx, o.db, "org-request:"+submissionClient(r, o.config.Secret), 3, 10)
	if err != nil {
		applicationFailure(w, 503, "ORG_UNAVAILABLE", "Yritä uudelleen hetken kuluttua.")
		return
	}
	if !allowed {
		w.Header().Set("Retry-After", "60")
		applicationFailure(w, 429, "ORG_RATE_LIMITED", "Lähetysyrityksiä on liian monta. Odota hetki ja yritä uudelleen.")
		return
	}
	var input orgRequestInput
	logo, err := readOrgRequest(w, r, &input)
	var fields bson.M
	if err == nil {
		fields, err = validateOrgRequest(input, !actor.IsZero())
	}
	var invalid applicationError
	if errors.As(err, &invalid) {
		applicationFailure(w, 400, invalid.code, invalid.message)
		return
	}
	now := o.now().UTC()
	// A filled hidden field, or a form sent within two seconds of opening, is a bot. It
	// gets the normal answer and nothing is stored or mailed.
	if strings.TrimSpace(input.Honeypot) != "" || (input.StartedAtMS > 0 && now.Sub(time.UnixMilli(input.StartedAtMS)) < 2*time.Second) {
		writeJSON(w, 201, bson.M{"success": true, "status": "unconfirmed"})
		return
	}
	applicant := fields["applicant"].(bson.M)
	status := "unconfirmed"
	if !actor.IsZero() {
		var account bson.M
		err := o.db.Collection("users").FindOne(ctx, bson.M{"_id": actor},
			options.FindOne().SetProjection(bson.M{"email": 1, "active": 1, "banned": 1, "confirmed": 1, "security_suspension": 1})).Decode(&account)
		if err != nil {
			applicationFailure(w, 503, "ORG_UNAVAILABLE", "Yritä uudelleen hetken kuluttua.")
			return
		}
		if !activeAccount(account) {
			applicationFailure(w, 403, "ORG_ACCOUNT", "Vahvista tilisi sähköpostiosoite ennen hakemista.")
			return
		}
		open, err := o.db.Collection("organization_requests").CountDocuments(ctx, bson.M{"applicant.user_id": actor,
			"status": bson.M{"$in": bson.A{"received", "verifying", "in_review", "needs_info"}}}, options.Count().SetLimit(5))
		if err != nil {
			applicationFailure(w, 503, "ORG_UNAVAILABLE", "Yritä uudelleen hetken kuluttua.")
			return
		}
		if open >= 5 {
			applicationFailure(w, 429, "ORG_OPEN_LIMIT", "Sinulla on jo viisi käsittelyssä olevaa hakemusta. Odota, että ne käsitellään.")
			return
		}
		email, _ := account["email"].(string)
		applicant["user_id"] = actor
		applicant["email"] = strings.ToLower(email)
		status = "received"
	}
	email, _ := applicant["email"].(string)
	requests := o.db.Collection("organization_requests")
	// One address may hold at most three unconfirmed requests at a time.
	if status == "unconfirmed" {
		pending, err := requests.CountDocuments(ctx, bson.M{"applicant.email": email, "status": "unconfirmed", "expires_at": bson.M{"$gt": now}}, options.Count().SetLimit(3))
		if err != nil {
			applicationFailure(w, 503, "ORG_UNAVAILABLE", "Yritä uudelleen hetken kuluttua.")
			return
		}
		if pending >= 3 {
			applicationFailure(w, 429, "ORG_PENDING", "Tähän osoitteeseen on jo lähetetty vahvistuslinkkejä. Avaa aiempi linkki sähköpostistasi.")
			return
		}
		capped, err := o.mailCap(ctx)
		if err != nil || !capped {
			applicationFailure(w, 503, "ORG_UNAVAILABLE", "Lomake on hetken ruuhkautunut. Yritä myöhemmin uudelleen.")
			return
		}
	}
	token, err := o.token()
	if err != nil {
		applicationFailure(w, 503, "ORG_UNAVAILABLE", "Yritä uudelleen hetken kuluttua.")
		return
	}
	id := bson.NewObjectID()
	fields["_id"] = id
	fields["status"] = status
	fields["status_token_hash"] = campaignDigest(token)
	fields["consent_at"] = now
	fields["created_at"] = now
	fields["updated_at"] = now
	fields["source"] = "public-form"
	if logo != nil {
		// Kept with the request for the reviewers; O4 copies it to the organization.
		fields["logo"] = bson.M{"content_type": "image/png", "data": bson.Binary{Subtype: 0, Data: logo}, "size": len(logo)}
	}
	if status == "unconfirmed" {
		fields["expires_at"] = now.Add(orgRequestUnconfirmedLife)
	} else {
		fields["email_confirmed_at"] = now
		// Hallinto's intake (O3) reads requests with this flag and clears it.
		fields["case_pending"] = true
	}
	if _, err := requests.InsertOne(ctx, fields); err != nil {
		log.Print("Organization request could not be stored")
		applicationFailure(w, 503, "ORG_SAVE_FAILED", "Hakemusta ei voitu tallentaa. Yritä uudelleen.")
		return
	}
	o.linkInvitation(ctx, id, input.Invitation, now)
	if status == "unconfirmed" {
		link := strings.TrimRight(o.config.Origin, "/") + orgRequestStatusPage + token
		body, err := renderOrgRequestMail(applicant["name"].(string), fields["organization"].(bson.M)["name"].(string), link)
		if err == nil {
			_, err = o.db.Collection("email_queue").InsertOne(ctx, orgRequestMailJob(email, body))
		}
		if err != nil {
			// Without the mail nobody can confirm the request: remove it and say so.
			_, _ = requests.DeleteOne(context.WithoutCancel(ctx), bson.M{"_id": id})
			log.Print("Organization request mail could not be queued")
			applicationFailure(w, 503, "ORG_SAVE_FAILED", "Hakemusta ei voitu lähettää. Yritä uudelleen.")
			return
		}
	}
	writeJSON(w, 201, bson.M{"success": true, "id": id.Hex(), "status": status})
}

// linkInvitation ties a stored request to the outbound invitation its token belongs to
// (docs/hallinto-city-grant.md, O6). The token is single use: the first request to claim
// it records the invitation id and case, so Hallinto can link the two. A wrong, spent or
// expired token changes nothing and says nothing, so it cannot be probed. Best effort.
func (o *orgRequests) linkInvitation(ctx context.Context, request bson.ObjectID, token string, now time.Time) {
	if !orgRequestToken.MatchString(token) {
		return
	}
	var invitation struct {
		InvitationID string `bson:"invitation_id"`
		CaseID       string `bson:"case_id"`
	}
	err := o.db.Collection("org_invitations").FindOneAndUpdate(ctx,
		bson.M{"token_hash": campaignDigest(token), "status": "sent", "expires_at": bson.M{"$gt": now}, "used_at": bson.M{"$exists": false}},
		bson.M{"$set": bson.M{"used_at": now, "used_request": request}},
		options.FindOneAndUpdate().SetProjection(bson.M{"invitation_id": 1, "case_id": 1})).Decode(&invitation)
	if err != nil {
		if !errors.Is(err, mongo.ErrNoDocuments) {
			log.Print("Organization invitation could not be claimed")
		}
		return
	}
	if _, err := o.db.Collection("organization_requests").UpdateOne(context.WithoutCancel(ctx), bson.M{"_id": request},
		bson.M{"$set": bson.M{"invitation_id": invitation.InvitationID, "invitation_case": invitation.CaseID}}); err != nil {
		log.Print("Organization invitation could not be linked")
	}
}
