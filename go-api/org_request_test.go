package main

import (
	"bytes"
	"context"
	"image"
	"image/jpeg"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
)

func validOrgRequest() orgRequestInput {
	return orgRequestInput{Represents: true, Name: "Rauhan ystävät ry", OrgEmail: "Info@Rauha.example",
		Links: []string{"rauha.example", " https://some.example/rauha "}, Description: "Rauhanjärjestö.",
		Applicant: "Testi Hakija", Email: "hakija@example.com", Access: "organization_account", Consent: true}
}

func TestValidateOrgRequest(t *testing.T) {
	fields, err := validateOrgRequest(validOrgRequest(), false)
	if err != nil {
		t.Fatal(err)
	}
	org := fields["organization"].(bson.M)
	if org["email"] != "info@rauha.example" || len(org["links"].([]string)) != 2 || org["links"].([]string)[0] != "https://rauha.example" {
		t.Fatalf("org=%v", org)
	}
	if fields["access"] != "organization_account" || fields["applicant"].(bson.M)["email"] != "hakija@example.com" {
		t.Fatalf("fields=%v", fields)
	}
	cases := map[string]func(*orgRequestInput){
		"ORG_NAME":            func(i *orgRequestInput) { i.Name = "x" },
		"ORG_LINK_COUNT":      func(i *orgRequestInput) { i.Links = nil },
		"ORG_LINK":            func(i *orgRequestInput) { i.Links = []string{"javascript:alert(1)"} },
		"ORG_EMAIL":           func(i *orgRequestInput) { i.OrgEmail = "" },
		"ORG_BUSINESS_ID":     func(i *orgRequestInput) { i.BusinessID = "123" },
		"ORG_APPLICANT_EMAIL": func(i *orgRequestInput) { i.Email = "nope" },
		"ORG_ACCESS":          func(i *orgRequestInput) { i.Access = "god" },
		"ORG_ACCESS_SIGN_IN":  func(i *orgRequestInput) { i.Access = "own_account" },
		"ORG_INVITE_EMAIL":    func(i *orgRequestInput) { i.Access = "invite" },
		"ORG_CONSENT":         func(i *orgRequestInput) { i.Consent = false },
	}
	for code, change := range cases {
		input := validOrgRequest()
		change(&input)
		_, err := validateOrgRequest(input, false)
		if invalid, ok := err.(applicationError); !ok || invalid.code != code {
			t.Errorf("%s: got %v", code, err)
		}
	}
	// A suggestion needs no organization email and stores no access choice.
	suggestion := validOrgRequest()
	suggestion.Represents, suggestion.OrgEmail, suggestion.Access = false, "", "invite"
	fields, err = validateOrgRequest(suggestion, false)
	if err != nil || fields["access"] != nil {
		t.Fatalf("suggestion: %v %v", fields, err)
	}
	// Signed in: own account is allowed and the form's address is ignored.
	own := validOrgRequest()
	own.Access, own.Email = "own_account", ""
	if fields, err = validateOrgRequest(own, true); err != nil || fields["applicant"].(bson.M)["email"] != nil {
		t.Fatalf("own: %v %v", fields, err)
	}
}

func TestOrgRequestMailEscapes(t *testing.T) {
	body, err := renderOrgRequestMail("<b>Nimi</b>", "A & B", "https://example.test/organisaatiot/hakemus#tok")
	if err != nil || strings.Contains(body, "<b>Nimi") || !strings.Contains(body, "A &amp; B") || !strings.Contains(body, "hakemus#tok") {
		t.Fatalf("%v %s", err, body)
	}
}

func orgRequestRig(t *testing.T, actor bson.ObjectID) (*orgRequests, http.Handler) {
	db := reminderTestDB(t)
	tokens := 0
	o := &orgRequests{db: db, config: submissionConfig{Origin: "https://example.test", Secret: "s"},
		actor: func(context.Context, *http.Request) (bson.ObjectID, error) { return actor, nil },
		token: func() (string, error) { tokens++; return strings.Repeat("t", 21) + string(rune('a'+tokens)), nil },
		now:   time.Now, mailCap: func(context.Context) (bool, error) { return true, nil }}
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(299) })
	return o, o.handler(next)
}

func orgCall(h http.Handler, method, path, body string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	r.Header.Set("Origin", "https://example.test")
	r.Header.Set("Content-Type", "application/json")
	r.RemoteAddr = "192.0.2.1:1234"
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

const orgRequestBody = `{"represents":true,"organization_name":"Rauhan ystävät ry","organization_email":"info@rauha.example",
"links":["rauha.example"],"applicant_name":"Testi Hakija","applicant_email":"hakija@example.com",
"access":"invite","invite_email":"pj@rauha.example","onboarding":true,"consent":true,"homepage":"","started_at":0}`

// Needs a disposable MongoDB (REMINDER_TEST_MONGO).
func TestOrgRequestAnonymousFlow(t *testing.T) {
	o, h := orgRequestRig(t, bson.NilObjectID)
	ctx := context.Background()
	if r := orgCall(h, "GET", "/api/other", ""); r.Code != 299 {
		t.Fatalf("other path: %d", r.Code)
	}
	r := orgCall(h, "POST", orgRequestsPath, orgRequestBody)
	if r.Code != 201 || !strings.Contains(r.Body.String(), `"status":"unconfirmed"`) {
		t.Fatalf("%d %s", r.Code, r.Body.String())
	}
	var stored bson.M
	if err := o.db.Collection("organization_requests").FindOne(ctx, bson.M{}).Decode(&stored); err != nil {
		t.Fatal(err)
	}
	if stored["status"] != "unconfirmed" || stored["invite_email"] != "pj@rauha.example" || stored["onboarding"] != true || stored["case_pending"] != nil {
		t.Fatalf("stored=%v", stored)
	}
	token := strings.Repeat("t", 21) + "b"
	if stored["status_token_hash"] != campaignDigest(token) || strings.Contains(r.Body.String(), token) {
		t.Fatal("only the hash is stored and the token is never answered")
	}
	var job bson.M
	if err := o.db.Collection("email_queue").FindOne(ctx, bson.M{}).Decode(&job); err != nil {
		t.Fatal(err)
	}
	if job["recipients"].(bson.A)[0] != "hakija@example.com" || !strings.Contains(job["html"].(string), "https://example.test/organisaatiot/hakemus#"+token) {
		t.Fatalf("job=%v", job)
	}
	// A wrong token finds nothing; the right one confirms and shows the status.
	if r := orgCall(h, "POST", orgRequestsStatusPath, `{"token":"`+strings.Repeat("x", 22)+`"}`); r.Code != 404 {
		t.Fatalf("wrong token: %d", r.Code)
	}
	r = orgCall(h, "POST", orgRequestsStatusPath, `{"token":"`+token+`"}`)
	if r.Code != 200 || !strings.Contains(r.Body.String(), `"status":"received"`) || !strings.Contains(r.Body.String(), "Rauhan ystävät ry") {
		t.Fatalf("%d %s", r.Code, r.Body.String())
	}
	stored = bson.M{}
	_ = o.db.Collection("organization_requests").FindOne(ctx, bson.M{}).Decode(&stored)
	if stored["case_pending"] != true || stored["email_confirmed_at"] == nil || stored["expires_at"] != nil {
		t.Fatalf("confirmed=%v", stored)
	}
	// The applicant data never comes back on the status read.
	if strings.Contains(r.Body.String(), "hakija@example.com") {
		t.Fatal("status leaks the applicant")
	}
	// The honeypot answers like success and stores nothing.
	bot := strings.Replace(orgRequestBody, `"homepage":""`, `"homepage":"x"`, 1)
	if r := orgCall(h, "POST", orgRequestsPath, bot); r.Code != 201 {
		t.Fatalf("bot: %d", r.Code)
	}
	if n, _ := o.db.Collection("organization_requests").CountDocuments(ctx, bson.M{}); n != 1 {
		t.Fatalf("bot stored: %d", n)
	}
	// Cross-site posts are refused, and mine needs a session.
	cross := httptest.NewRequest("POST", orgRequestsPath, strings.NewReader(orgRequestBody))
	cross.Header.Set("Origin", "https://evil.example")
	cross.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	h.ServeHTTP(w, cross)
	if w.Code != 403 {
		t.Fatalf("cross: %d", w.Code)
	}
	if r := orgCall(h, "GET", orgRequestsMinePath, ""); r.Code != 401 {
		t.Fatalf("mine anonymous: %d", r.Code)
	}
}

func TestOrgRequestSignedIn(t *testing.T) {
	actor := bson.NewObjectID()
	o, h := orgRequestRig(t, actor)
	ctx := context.Background()
	if _, err := o.db.Collection("users").InsertOne(ctx, bson.M{"_id": actor, "email": "Jasen@Example.com", "confirmed": true, "active": true}); err != nil {
		t.Fatal(err)
	}
	body := strings.Replace(orgRequestBody, `"access":"invite"`, `"access":"own_account"`, 1)
	r := orgCall(h, "POST", orgRequestsPath, body)
	if r.Code != 201 || !strings.Contains(r.Body.String(), `"status":"received"`) {
		t.Fatalf("%d %s", r.Code, r.Body.String())
	}
	var stored struct {
		Applicant struct {
			Email  string        `bson:"email"`
			UserID bson.ObjectID `bson:"user_id"`
		} `bson:"applicant"`
		CasePending bool `bson:"case_pending"`
	}
	if err := o.db.Collection("organization_requests").FindOne(ctx, bson.M{}).Decode(&stored); err != nil {
		t.Fatal(err)
	}
	if stored.Applicant.Email != "jasen@example.com" || stored.Applicant.UserID != actor || !stored.CasePending {
		t.Fatalf("stored=%+v", stored)
	}
	if n, _ := o.db.Collection("email_queue").CountDocuments(ctx, bson.M{}); n != 0 {
		t.Fatal("a signed-in applicant needs no confirmation mail")
	}
	for i := 0; i < 4; i++ {
		_, _ = o.db.Collection("organization_requests").InsertOne(ctx, bson.M{"applicant": bson.M{"user_id": actor}, "status": "in_review"})
	}
	if r := orgCall(h, "POST", orgRequestsPath, body); r.Code != 429 || !strings.Contains(r.Body.String(), "ORG_OPEN_LIMIT") {
		t.Fatalf("open limit: %d %s", r.Code, r.Body.String())
	}
	r = orgCall(h, "GET", orgRequestsMinePath, "")
	if r.Code != 200 || !strings.Contains(r.Body.String(), `"access":"own_account"`) {
		t.Fatalf("%d %s", r.Code, r.Body.String())
	}
}

func TestOrgRequestCity(t *testing.T) {
	input := validOrgRequest()
	input.City = "Tampere"
	fields, err := validateOrgRequest(input, false)
	if err != nil || fields["organization"].(bson.M)["city"] != "tampere" {
		t.Fatalf("%v %v", fields, err)
	}
	input.City = "Atlantis"
	if _, err := validateOrgRequest(input, false); err == nil || err.(applicationError).code != "ORG_CITY" {
		t.Fatalf("unknown city: %v", err)
	}
}

func orgMultipart(t *testing.T, request string, logo []byte) (*bytes.Buffer, string) {
	t.Helper()
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	part, _ := writer.CreateFormField("request")
	part.Write([]byte(request))
	if logo != nil {
		file, _ := writer.CreateFormFile("logo", "logo.jpg")
		file.Write(logo)
	}
	writer.Close()
	return &body, writer.FormDataContentType()
}

func TestOrgRequestLogo(t *testing.T) {
	o, h := orgRequestRig(t, bson.NilObjectID)
	ctx := context.Background()
	picture := image.NewRGBA(image.Rect(0, 0, 40, 20))
	var jpg bytes.Buffer
	if err := jpeg.Encode(&jpg, picture, nil); err != nil {
		t.Fatal(err)
	}
	send := func(logo []byte) *httptest.ResponseRecorder {
		body, contentType := orgMultipart(t, orgRequestBody, logo)
		r := httptest.NewRequest("POST", orgRequestsPath, body)
		r.Header.Set("Origin", "https://example.test")
		r.Header.Set("Content-Type", contentType)
		r.RemoteAddr = "192.0.2.9:1"
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		return w
	}
	if r := send(append([]byte("GIF89a"), make([]byte, 20)...)); r.Code != 400 || !strings.Contains(r.Body.String(), "ORG_LOGO") {
		t.Fatalf("gif: %d %s", r.Code, r.Body.String())
	}
	if r := send(make([]byte, orgRequestLogoMax+10)); r.Code != 400 {
		t.Fatalf("too big: %d %s", r.Code, r.Body.String())
	}
	if r := send(jpg.Bytes()); r.Code != 201 {
		t.Fatalf("jpeg: %d %s", r.Code, r.Body.String())
	}
	var stored struct {
		Logo struct {
			ContentType string `bson:"content_type"`
			Data        []byte `bson:"data"`
		} `bson:"logo"`
	}
	if err := o.db.Collection("organization_requests").FindOne(ctx, bson.M{}).Decode(&stored); err != nil {
		t.Fatal(err)
	}
	if stored.Logo.ContentType != "image/png" || !bytes.HasPrefix(stored.Logo.Data, []byte("\x89PNG")) {
		t.Fatalf("logo=%v", stored.Logo.ContentType)
	}
}

// Needs a disposable MongoDB (REMINDER_TEST_MONGO). An invitation token links the request
// to the invitation's case once; a wrong, spent or expired token changes nothing.
func TestOrgRequestInvitationToken(t *testing.T) {
	o, h := orgRequestRig(t, bson.NilObjectID)
	ctx := context.Background()
	token := strings.Repeat("k", 43)
	now := time.Now().UTC()
	_, err := o.db.Collection("org_invitations").InsertMany(ctx, []any{
		bson.M{"invitation_id": "inv-1", "case_id": "case-1", "token_hash": campaignDigest(token), "status": "sent", "expires_at": now.Add(time.Hour)},
		bson.M{"invitation_id": "inv-2", "case_id": "case-2", "token_hash": campaignDigest(strings.Repeat("e", 43)), "status": "sent", "expires_at": now.Add(-time.Hour)},
	})
	if err != nil {
		t.Fatal(err)
	}
	with := func(value string) string {
		return strings.Replace(orgRequestBody, `"started_at":0}`, `"started_at":0,"invitation":"`+value+`"}`, 1)
	}
	for _, value := range []string{token, token, strings.Repeat("e", 43)} { // three posts: the form allows three in ten minutes
		if r := orgCall(h, "POST", orgRequestsPath, with(value)); r.Code != 201 {
			t.Fatalf("%s: %d %s", value, r.Code, r.Body.String())
		}
	}
	linked, err := o.db.Collection("organization_requests").CountDocuments(ctx, bson.M{"invitation_case": "case-1", "invitation_id": "inv-1"})
	if err != nil || linked != 1 {
		t.Fatalf("linked=%d err=%v", linked, err)
	}
	if n, _ := o.db.Collection("organization_requests").CountDocuments(ctx, bson.M{"invitation_case": bson.M{"$exists": true}}); n != 1 {
		t.Fatalf("only the first use links: %d", n)
	}
}
