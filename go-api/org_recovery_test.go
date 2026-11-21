package main

import (
	"context"
	"strings"
	"testing"

	"go.mongodb.org/mongo-driver/v2/bson"
)

func recoveryRig(t *testing.T, actor bson.ObjectID) (*orgRequests, func(string) (int, string)) {
	o, _ := orgRequestRig(t, actor)
	h := o.recoveryHandler(nil)
	return o, func(body string) (int, string) {
		if !strings.Contains(body, `"need":"x"`) {
			// The client rate limit has its own test below; the flow test posts more often than it allows.
			_, _ = o.db.Collection("demo_submission_tokens").DeleteMany(context.Background(), bson.M{"status": "go_rate"})
		}
		r := orgCall(h, "POST", orgRecoveryPath, body)
		return r.Code, r.Body.String()
	}
}

const recoveryBody = `{"need":"owner_change","organization_name":"Rauhan  Ystävät ry","applicant_name":"Testi Hakija",
"applicant_email":"hakija@example.com","person_email":"Uusi@Example.com","links":["rauha.example"],"consent":true,"homepage":"","started_at":0}`

func TestValidateOrgRecovery(t *testing.T) {
	good := orgRecoveryInput{Need: "owner_change", Name: "Rauha ry", Applicant: "Hakija", Email: "H@Example.com", PersonEmail: "uusi@example.com", Consent: true}
	fields, err := validateOrgRecovery(good, false)
	if err != nil || fields["person_role"] != "owner" || fields["person_email"] != "uusi@example.com" || fields["kind"] != "access_recovery" {
		t.Fatalf("%v %v", fields, err)
	}
	if links := fields["organization"].(bson.M)["links"].([]string); len(links) != 0 {
		t.Fatalf("links are optional: %v", links)
	}
	lost := good
	lost.Need, lost.PersonEmail = "lost_access", "ignored@example.com"
	if fields, err := validateOrgRecovery(lost, false); err != nil || fields["person_email"] != nil || fields["person_role"] != "owner" {
		t.Fatalf("lost access names the applicant, not another person: %v %v", fields, err)
	}
	for name, mutate := range map[string]func(*orgRecoveryInput){
		"need":         func(i *orgRecoveryInput) { i.Need = "delete_everything" },
		"name":         func(i *orgRecoveryInput) { i.Name = "x" },
		"organization": func(i *orgRecoveryInput) { i.OrganizationID = "../x" },
		"person email": func(i *orgRecoveryInput) { i.PersonEmail = "nope" },
		"member role":  func(i *orgRecoveryInput) { i.Need, i.PersonRole = "add_member", "owner" },
		"consent":      func(i *orgRecoveryInput) { i.Consent = false },
		"link":         func(i *orgRecoveryInput) { i.Links = []string{"javascript:alert(1)"} },
	} {
		bad := good
		mutate(&bad)
		if _, err := validateOrgRecovery(bad, false); err == nil {
			t.Errorf("%s accepted", name)
		}
	}
	member := good
	member.Need, member.PersonRole = "add_member", "member"
	if fields, err := validateOrgRecovery(member, false); err != nil || fields["person_role"] != "member" {
		t.Fatalf("%v %v", fields, err)
	}
}

// Needs a disposable MongoDB (REMINDER_TEST_MONGO).
func TestOrgRecoveryFlow(t *testing.T) {
	o, post := recoveryRig(t, bson.NilObjectID)
	ctx := context.Background()
	code, body := post(recoveryBody)
	if code != 201 || !strings.Contains(body, `"status":"unconfirmed"`) {
		t.Fatalf("%d %s", code, body)
	}
	var stored bson.M
	if err := o.db.Collection("organization_requests").FindOne(ctx, bson.M{}).Decode(&stored); err != nil {
		t.Fatal(err)
	}
	if stored["kind"] != "access_recovery" || stored["need"] != "owner_change" || stored["person_email"] != "uusi@example.com" ||
		stored["person_role"] != "owner" || stored["status"] != "unconfirmed" || stored["expires_at"] == nil {
		t.Fatalf("stored=%v", stored)
	}
	if n, _ := o.db.Collection("organization_requests").CountDocuments(ctx, bson.M{"organization.key": "rauhan ystävät ry"}); n != 1 {
		t.Fatalf("key: %v", stored["organization"])
	}
	var job bson.M
	if err := o.db.Collection("email_queue").FindOne(ctx, bson.M{}).Decode(&job); err != nil {
		t.Fatal(err)
	}
	if job["recipients"].(bson.A)[0] != "hakija@example.com" || strings.Contains(job["html"].(string), "uusi@example.com") {
		t.Fatalf("the mail goes to the applicant and never names the other person: %v", job)
	}
	// A second request for the same organization is kept, linked to the first, and answered
	// the same. A third and fourth hit the per-address cap and the monthly cap quietly.
	for i := 0; i < 2; i++ {
		other := strings.Replace(recoveryBody, "hakija@example.com", "toinen"+string(rune('a'+i))+"@example.com", 1)
		if code, body := post(other); code != 201 || !strings.Contains(body, `"status":"unconfirmed"`) {
			t.Fatalf("duplicate %d: %d %s", i, code, body)
		}
	}
	requests := o.db.Collection("organization_requests")
	if n, _ := requests.CountDocuments(ctx, bson.M{"duplicate_of": bson.M{"$exists": true}}); n != 2 {
		t.Fatalf("duplicates: %d", n)
	}
	if n, _ := requests.CountDocuments(ctx, bson.M{}); n != 3 {
		t.Fatalf("stored: %d", n)
	}
	// The monthly cap drops further requests silently with the same answer.
	over := strings.Replace(recoveryBody, "hakija@example.com", "kolmas@example.com", 1)
	if code, body := post(over); code != 201 || !strings.Contains(body, `"status":"unconfirmed"`) {
		t.Fatalf("capped: %d %s", code, body)
	}
	if n, _ := requests.CountDocuments(ctx, bson.M{}); n != 3 {
		t.Fatalf("capped request stored: %d", n)
	}
	// Bots get the normal answer and nothing is stored.
	if code, _ := post(strings.Replace(recoveryBody, `"homepage":""`, `"homepage":"x"`, 1)); code != 201 {
		t.Fatalf("bot: %d", code)
	}
	if n, _ := requests.CountDocuments(ctx, bson.M{}); n != 3 {
		t.Fatalf("bot stored: %d", n)
	}
	// A broken form is refused, and the client's own rate limit answers 429.
	if code, _ := post(`{"need":"x"}`); code != 400 {
		t.Fatalf("invalid: %d", code)
	}
}

// Needs a disposable MongoDB (REMINDER_TEST_MONGO).
func TestOrgRecoveryClientRateLimit(t *testing.T) {
	_, post := recoveryRig(t, bson.NilObjectID)
	for i, want := range []int{400, 400, 429} {
		if code, _ := post(`{"need":"x"}`); code != want {
			t.Fatalf("attempt %d: %d, want %d", i, code, want)
		}
	}
}
