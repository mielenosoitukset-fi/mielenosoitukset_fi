package main

// Organization access recovery requests (slice O5 of the Hallinto organization onboarding
// plan, docs/organization-requests.md). Someone who cannot reach an organization's account,
// or wants an owner changed or a member added, asks here. Go only stores the request and
// mails the applicant a private status link, like O2. Whether the organization exists, is
// protected, or already has an open request is never revealed: every accepted or
// quietly dropped request gets the same answer. The decision, the notice to the
// organization and the objection window belong to Hallinto; carrying out the decision is a
// Flask operation that only sends an invitation (docs/hallinto-city-grant.md).
//
//	POST /api/organization-requests/recovery   submit (JSON)
//
// The status link and the status read are O2's. Shipped dark: ORG_RECOVERY_GO_ENABLED=true
// is needed and Apache must route the path.

import (
	"bytes"
	"context"
	"errors"
	"html"
	"log"
	"net/http"
	"os"
	"regexp"
	"strings"
	"text/template"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

const (
	orgRecoveryPath        = "/api/organization-requests/recovery"
	orgRecoveryMailSubject = "Organisaation tilin käytön palautus Mielenosoitukset.fi-palvelussa"
	orgRecoveryCapRoute    = "org-recovery"
	// A request for an organization with an open one is kept, linked to it, but never reviewed twice.
	orgRecoveryMonthlyLimit = 3
	orgRecoveryPendingLimit = 2
)

var (
	orgRecoveryNeeds = map[string]bool{"owner_change": true, "add_member": true, "lost_access": true}
	orgRecoveryOrgID = regexp.MustCompile(`^[0-9a-f]{24}$`)
)

func orgRecoveryEnabledFromEnv() bool {
	return strings.EqualFold(strings.TrimSpace(os.Getenv("ORG_RECOVERY_GO_ENABLED")), "true")
}

type orgRecoveryInput struct {
	Need           string   `json:"need"`
	Name           string   `json:"organization_name"`
	OrganizationID string   `json:"organization_id"`
	Applicant      string   `json:"applicant_name"`
	Email          string   `json:"applicant_email"`
	Role           string   `json:"applicant_role"`
	PersonEmail    string   `json:"person_email"`
	PersonRole     string   `json:"person_role"`
	Links          []string `json:"links"`
	Description    string   `json:"description"`
	Consent        bool     `json:"consent"`
	Honeypot       string   `json:"homepage"`
	StartedAtMS    int64    `json:"started_at"`
}

// orgRecoveryKey names an organization for the duplicate checks: letters of any case and
// single spaces, so "Rauha  ry" and "rauha ry" are the same.
func orgRecoveryKey(name string) string {
	return strings.ToLower(strings.Join(strings.Fields(name), " "))
}

// validateOrgRecovery returns the stored fields. A signed-in applicant's address comes from
// the account. A lost-access request names the applicant as the person; an owner change
// and an added member name someone else, and only an added member has a chosen role.
func validateOrgRecovery(input orgRecoveryInput, signedIn bool) (bson.M, error) {
	var err error
	if !orgRecoveryNeeds[input.Need] {
		return nil, applicationError{"ORG_NEED", "Valitse, mitä tarvitset."}
	}
	fields := bson.M{"kind": "access_recovery", "need": input.Need, "represents": true, "onboarding": false}
	org := bson.M{}
	if org["name"], err = orgRequestText(input.Name, 2, 120, "ORG_NAME", "Kirjoita organisaation nimi."); err != nil {
		return nil, err
	}
	org["key"] = orgRecoveryKey(org["name"].(string))
	if id := strings.TrimSpace(input.OrganizationID); id != "" {
		if !orgRecoveryOrgID.MatchString(id) {
			return nil, applicationError{"ORG_ID", "Tarkista organisaation tunnus tai jätä kenttä tyhjäksi."}
		}
		fields["target_organization_id"] = id
	}
	if strings.TrimSpace(strings.Join(input.Links, "")) == "" {
		org["links"] = []string{}
	} else if org["links"], err = orgRequestLinks(input.Links); err != nil {
		return nil, err
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
	switch input.Need {
	case "lost_access":
		fields["person_role"] = "owner"
	case "owner_change":
		fields["person_role"] = "owner"
	case "add_member":
		if input.PersonRole != "admin" && input.PersonRole != "member" {
			return nil, applicationError{"ORG_PERSON_ROLE", "Valitse, millä roolilla henkilö lisätään."}
		}
		fields["person_role"] = input.PersonRole
	}
	if input.Need != "lost_access" {
		if fields["person_email"], err = orgRequestEmail(input.PersonEmail, "ORG_PERSON_EMAIL", "Tarkista sähköpostiosoite, jolle pääsy annetaan."); err != nil {
			return nil, err
		}
	}
	if !input.Consent {
		return nil, applicationError{"ORG_CONSENT", "Hyväksy tietojen käsittely, jotta voimme käsitellä pyynnön."}
	}
	return fields, nil
}

var orgRecoveryMailTemplate = template.Must(template.New("org-recovery").Funcs(template.FuncMap{"esc": html.EscapeString}).Parse(`<!DOCTYPE html>
<html lang="fi">
<head><meta charset="UTF-8"><title>{{esc .Subject}}</title></head>
<body style="font-family: Arial, sans-serif; color: #1b1b1b; line-height: 1.5;">
<p>Hei {{esc .Name}}!</p>
<p>Saimme pyynnön, joka koskee organisaation <strong>{{esc .Organization}}</strong> tilin käyttöä.</p>
<p>Vahvista sähköpostiosoitteesi ja seuraa pyynnön tilaa tästä linkistä:</p>
<p><a href="{{esc .Link}}" style="display: inline-block; padding: 10px 20px; background: #0033A0; color: #ffffff; text-decoration: none; border-radius: 5px;">Avaa pyyntö</a></p>
<p>Linkki on henkilökohtainen. Älä jaa sitä. Jos et lähettänyt pyyntöä, voit jättää tämän viestin huomiotta: vahvistamaton pyyntö poistetaan 7 päivän kuluttua.</p>
<p>Emme anna pääsyä pelkän pyynnön perusteella. Varmistamme ensin, että organisaatio on pyynnön takana, ja ilmoitamme asiasta organisaation tunnetuille osoitteille.</p>
<p>Mielenosoitukset.fi</p>
</body>
</html>`))

func renderOrgRecoveryMail(name, organization, link string) (string, error) {
	var out bytes.Buffer
	err := orgRecoveryMailTemplate.Execute(&out, struct{ Subject, Name, Organization, Link string }{orgRecoveryMailSubject, name, organization, link})
	return out.String(), err
}

func orgRecoveryMailJob(recipient, body string) bson.D {
	job := campaignWelcomeJob(recipient, body, contactUUID())
	for i := range job {
		if job[i].Key == "subject" {
			job[i].Value = orgRecoveryMailSubject
		}
	}
	return job
}

func orgRecoveryHandler(next http.Handler, db *mongo.Database, config submissionConfig) http.Handler {
	if !orgRecoveryEnabledFromEnv() {
		return next
	}
	o := &orgRequests{db: db, config: config, token: campaignToken, now: time.Now,
		actor: func(ctx context.Context, r *http.Request) (bson.ObjectID, error) {
			return submissionActor(ctx, r, config)
		},
		mailCap: func(ctx context.Context) (bool, error) {
			return signupMailCapAllowed(ctx, db, orgRecoveryCapRoute, signupMailCapAdminLog(db))
		}}
	return o.recoveryHandler(next)
}

func (o *orgRequests) recoveryHandler(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != orgRecoveryPath {
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
		if r.Method != "POST" {
			w.Header().Set("Allow", "POST")
			applicationFailure(w, 405, "ORG_METHOD", "Käytä sivuston lomaketta.")
			return
		}
		if r.Header.Get("Sec-Fetch-Site") == "cross-site" || !submissionOriginAllowed(r.Header.Get("Origin"), o.config.Origin) {
			applicationFailure(w, 403, "ORG_ORIGIN", "Lähetä pyyntö sivuston lomakkeella.")
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 8*time.Second)
		defer cancel()
		actor, err := o.actor(ctx, r)
		if forcedStepPath(err) != "" {
			applicationFailure(w, 403, "ORG_ACCOUNT_STEP", "Viimeistele tilisi pakollinen muutos ensin.")
			return
		}
		if err != nil {
			applicationFailure(w, 503, "ORG_SESSION", "Istunnon tarkistaminen epäonnistui. Yritä uudelleen.")
			return
		}
		o.serveRecovery(ctx, w, r, actor)
	})
}

// serveRecovery stores one request. Everything that depends on the organization or on what
// else exists (a duplicate, a full quota, the site's mail cap) is dropped quietly and
// answered like success, so the form cannot be used to probe. Only the client's own rate
// limit, a broken form and a failing service are answered differently.
func (o *orgRequests) serveRecovery(ctx context.Context, w http.ResponseWriter, r *http.Request, actor bson.ObjectID) {
	if active, err := panicSwitchActive(ctx, o.db); err != nil || active {
		applicationFailure(w, 503, "ORG_UNAVAILABLE", "Palvelu ei ole juuri nyt käytettävissä.")
		return
	}
	allowed, err := windowedRate(ctx, o.db, "org-recovery:"+submissionClient(r, o.config.Secret), 2, 5)
	if err != nil {
		applicationFailure(w, 503, "ORG_UNAVAILABLE", "Yritä uudelleen hetken kuluttua.")
		return
	}
	if !allowed {
		w.Header().Set("Retry-After", "60")
		applicationFailure(w, 429, "ORG_RATE_LIMITED", "Lähetysyrityksiä on liian monta. Odota hetki ja yritä uudelleen.")
		return
	}
	var input orgRecoveryInput
	err = readOrgRequestJSON(w, r, &input)
	var fields bson.M
	if err == nil {
		fields, err = validateOrgRecovery(input, !actor.IsZero())
	}
	var invalid applicationError
	if errors.As(err, &invalid) {
		applicationFailure(w, 400, invalid.code, invalid.message)
		return
	}
	now := o.now().UTC()
	status := "unconfirmed"
	if !actor.IsZero() {
		status = "received"
	}
	answer := func() { writeJSON(w, 201, bson.M{"success": true, "status": status}) }
	if strings.TrimSpace(input.Honeypot) != "" || (input.StartedAtMS > 0 && now.Sub(time.UnixMilli(input.StartedAtMS)) < 2*time.Second) {
		answer()
		return
	}
	applicant := fields["applicant"].(bson.M)
	if !actor.IsZero() {
		var account bson.M
		err := o.db.Collection("users").FindOne(ctx, bson.M{"_id": actor},
			options.FindOne().SetProjection(bson.M{"email": 1, "active": 1, "banned": 1, "confirmed": 1, "security_suspension": 1})).Decode(&account)
		if err != nil {
			applicationFailure(w, 503, "ORG_UNAVAILABLE", "Yritä uudelleen hetken kuluttua.")
			return
		}
		if !activeAccount(account) {
			applicationFailure(w, 403, "ORG_ACCOUNT", "Vahvista tilisi sähköpostiosoite ennen pyyntöä.")
			return
		}
		email, _ := account["email"].(string)
		applicant["user_id"] = actor
		applicant["email"] = strings.ToLower(email)
	}
	email, _ := applicant["email"].(string)
	if fields["need"] == "lost_access" {
		fields["person_email"] = email
	}
	requests := o.db.Collection("organization_requests")
	unavailable := func() { applicationFailure(w, 503, "ORG_UNAVAILABLE", "Yritä uudelleen hetken kuluttua.") }
	if status == "unconfirmed" {
		pending, err := requests.CountDocuments(ctx, bson.M{"kind": "access_recovery", "applicant.email": email,
			"status": "unconfirmed", "expires_at": bson.M{"$gt": now}}, options.Count().SetLimit(orgRecoveryPendingLimit))
		if err != nil {
			unavailable()
			return
		}
		if pending >= orgRecoveryPendingLimit {
			answer()
			return
		}
	}
	key := fields["organization"].(bson.M)["key"]
	month, err := requests.CountDocuments(ctx, bson.M{"kind": "access_recovery", "organization.key": key,
		"created_at": bson.M{"$gt": now.AddDate(0, 0, -30)}}, options.Count().SetLimit(orgRecoveryMonthlyLimit))
	if err != nil {
		unavailable()
		return
	}
	if month >= orgRecoveryMonthlyLimit {
		answer()
		return
	}
	var open struct {
		ID bson.ObjectID `bson:"_id"`
	}
	err = requests.FindOne(ctx, bson.M{"kind": "access_recovery", "organization.key": key, "duplicate_of": bson.M{"$exists": false},
		"$or": bson.A{bson.M{"status": bson.M{"$in": bson.A{"received", "verifying", "in_review", "needs_info"}}},
			bson.M{"status": "unconfirmed", "expires_at": bson.M{"$gt": now}}}},
		options.FindOne().SetProjection(bson.M{"_id": 1})).Decode(&open)
	if err != nil && !errors.Is(err, mongo.ErrNoDocuments) {
		unavailable()
		return
	}
	if err == nil {
		fields["duplicate_of"] = open.ID
		fields["status_message"] = "Samasta organisaatiosta on jo avoin pyyntö. Käsittelemme ne yhdessä."
	}
	if status == "unconfirmed" {
		capped, err := o.mailCap(ctx)
		if err != nil {
			unavailable()
			return
		}
		if !capped {
			answer()
			return
		}
	}
	token, err := o.token()
	if err != nil {
		unavailable()
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
	if status == "unconfirmed" {
		fields["expires_at"] = now.Add(orgRequestUnconfirmedLife)
	} else {
		fields["email_confirmed_at"] = now
		// Hallinto's intake reads requests with this flag and clears it.
		fields["case_pending"] = true
	}
	if _, err := requests.InsertOne(ctx, fields); err != nil {
		log.Print("Organization recovery request could not be stored")
		applicationFailure(w, 503, "ORG_SAVE_FAILED", "Pyyntöä ei voitu tallentaa. Yritä uudelleen.")
		return
	}
	if status == "unconfirmed" {
		link := strings.TrimRight(o.config.Origin, "/") + orgRequestStatusPage + token
		body, err := renderOrgRecoveryMail(applicant["name"].(string), fields["organization"].(bson.M)["name"].(string), link)
		if err == nil {
			_, err = o.db.Collection("email_queue").InsertOne(ctx, orgRecoveryMailJob(email, body))
		}
		if err != nil {
			_, _ = requests.DeleteOne(context.WithoutCancel(ctx), bson.M{"_id": id})
			log.Print("Organization recovery request mail could not be queued")
			applicationFailure(w, 503, "ORG_SAVE_FAILED", "Pyyntöä ei voitu lähettää. Yritä uudelleen.")
			return
		}
	}
	answer()
}
