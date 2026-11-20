package main

import (
	"context"
	"log"
	"strconv"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

// Site-wide caps for public signups that make the site mail an address the
// visitor typed (campaign volunteers, fundraising reminders, the Pride banner
// list, campaign opening interest). The per-address and per-IP limits stop one
// source; these stop many sources together from using the forms to send mail
// to third parties, as the contact form's global limit has since INC-2026-0001.
// Each route has its own counters, so a flood on one form leaves the others open.
var signupMailCaps = []struct {
	name    string
	seconds int64
	limit   int64
}{{"10m", 600, 30}, {"day", 86400, 200}}

// signupMailCapAlert writes one admin log entry when a cap is first reached in
// a window, so the team sees it without a mail per refused request.
type signupMailCapAlert func(ctx context.Context, route, window string, limit int64, now time.Time)

// signupMailCapAllowed counts this signup against the route's caps and reports
// whether it may go on. The counters reuse the Go rate rows in
// demo_submission_tokens ("go-rate:" prefix), which the submission handler's
// cleanup removes after 48 hours.
func signupMailCapAllowed(ctx context.Context, db *mongo.Database, route string, alert signupMailCapAlert) (bool, error) {
	now := time.Now().UTC()
	for _, window := range signupMailCaps {
		bucket := now.Unix() / window.seconds
		filter := bson.M{"token": "go-rate:signup-cap-" + window.name + ":" + strconv.FormatInt(bucket, 10) + ":" + route}
		var record struct {
			Count int64 `bson:"count"`
		}
		err := db.Collection("demo_submission_tokens").FindOneAndUpdate(ctx, filter,
			bson.M{"$inc": bson.M{"count": 1}, "$setOnInsert": bson.M{"created_at": now, "status": "go_rate"}},
			options.FindOneAndUpdate().SetUpsert(true).SetReturnDocument(options.After)).Decode(&record)
		if err != nil {
			return false, err
		}
		if record.Count > window.limit {
			if record.Count == window.limit+1 && alert != nil {
				alert(ctx, route, window.name, window.limit, now)
			}
			return false, nil
		}
	}
	return true, nil
}

// signupMailCapAdminLog is the production alert: a log line and one admin_logs
// entry (shown in the admin log view). A failed write is only logged.
func signupMailCapAdminLog(db *mongo.Database) signupMailCapAlert {
	return func(ctx context.Context, route, window string, limit int64, now time.Time) {
		log.Printf("Signup mail cap reached: route=%s window=%s limit=%d", route, window, limit)
		_, err := db.Collection("admin_logs").InsertOne(ctx, bson.M{
			"event":     "signup_mail_cap_reached",
			"message":   "Site-wide signup limit reached; further signups on this form are refused until the window ends.",
			"details":   bson.M{"route": route, "window": window, "limit": limit},
			"timestamp": bson.NewDateTimeFromTime(now),
			"actor":     bson.M{"username": "system"},
			"source":    "go-api",
		})
		if err != nil {
			log.Printf("Signup mail cap alert could not be written (%s)", dbErrorKind(err))
		}
	}
}
