package main

import (
	"context"
	"testing"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
)

func TestSignupMailCapCountsPerRouteAndAlertsOnce(t *testing.T) {
	db := reminderTestDB(t)
	ctx := context.Background()
	var alerts []string
	alert := func(_ context.Context, route, window string, limit int64, _ time.Time) {
		alerts = append(alerts, route+"/"+window)
	}
	limit := signupMailCaps[0].limit
	if time.Now().Unix()%signupMailCaps[0].seconds > signupMailCaps[0].seconds-5 {
		time.Sleep(6 * time.Second) // stay inside one 10-minute bucket
	}
	for i := int64(0); i < limit; i++ {
		if ok, err := signupMailCapAllowed(ctx, db, "reminder", alert); err != nil || !ok {
			t.Fatalf("signup %d refused: %v %v", i, ok, err)
		}
	}
	for i := 0; i < 3; i++ {
		if ok, err := signupMailCapAllowed(ctx, db, "reminder", alert); err != nil || ok {
			t.Fatalf("over the cap allowed: %v %v", ok, err)
		}
	}
	if len(alerts) != 1 || alerts[0] != "reminder/10m" {
		t.Fatalf("alerts %v", alerts)
	}
	// Another form has its own counters.
	if ok, err := signupMailCapAllowed(ctx, db, "banner", alert); err != nil || !ok {
		t.Fatalf("other route refused: %v %v", ok, err)
	}
	// The counters are Go rate rows, which the existing cleanup removes.
	n, err := db.Collection("demo_submission_tokens").CountDocuments(ctx, bson.M{"token": bson.M{"$regex": "^go-rate:signup-cap-"}, "status": "go_rate"})
	if err != nil || n != 4 {
		t.Fatalf("rows %d %v", n, err)
	}
	// The production alert writes an admin log entry.
	signupMailCapAdminLog(db)(ctx, "reminder", "10m", limit, time.Now().UTC())
	var entry bson.M
	if err := db.Collection("admin_logs").FindOne(ctx, bson.M{"event": "signup_mail_cap_reached"}).Decode(&entry); err != nil {
		t.Fatal(err)
	}
}
