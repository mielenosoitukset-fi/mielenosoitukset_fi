package main

import (
	"context"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"

	"go.mongodb.org/mongo-driver/v2/bson"
)

// Runs the real store against a disposable MongoDB (REMINDER_TEST_MONGO).
func TestSuggestionStoreAgainstMongo(t *testing.T) {
	db := reminderTestDB(t)
	ctx := context.Background()
	orgID := bson.NewObjectID()
	if _, err := db.Collection("organizations").InsertOne(ctx, bson.D{{Key: "_id", Value: orgID}, {Key: "name", Value: "Esimerkki Ry"}}); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Collection("cases").InsertOne(ctx, bson.D{{Key: "running_num", Value: int32(100_040)}}); err != nil {
		t.Fatal(err)
	}
	writes := newTestWrites(nil)
	writes.store = mongoSuggestionStore{db}
	form := url.Values{"name": {"Uusi"}, "social_platform[]": {"x"}, "social_url[]": {"https://x.example"}}
	request := httptest.NewRequest("POST", "/organization/"+orgID.Hex()+"/save_suggestion", strings.NewReader(form.Encode()))
	request.PostForm = form
	recorder := httptest.NewRecorder()
	writes.saveOrganization(ctx, recorder, request, orgID.Hex(), "203.0.113.7")
	if recorder.Code != 302 {
		t.Fatalf("status %d", recorder.Code)
	}
	var suggestion, created, mail bson.D
	if err := db.Collection("org_edit_suggestions").FindOne(ctx, bson.M{"organization_id": orgID}).Decode(&suggestion); err != nil {
		t.Fatal(err)
	}
	if err := db.Collection("cases").FindOne(ctx, bson.M{"type": "organization_edit_suggestion"}).Decode(&created); err != nil {
		t.Fatal(err)
	}
	if number, _ := documentValue(created, "running_num"); number != int64(100_041) {
		t.Errorf("running_num %v", number)
	}
	if err := db.Collection("email_queue").FindOne(ctx, bson.M{}).Decode(&mail); err != nil {
		t.Fatal(err)
	}
	if status, _ := documentValue(mail, "status"); status != "pending" {
		t.Errorf("mail status %v", status)
	}
	if recipients, _ := documentValue(mail, "recipients"); !strings.Contains(pyRepr(recipients), suggestionRecipient) {
		t.Errorf("recipients %v", recipients)
	}
}
