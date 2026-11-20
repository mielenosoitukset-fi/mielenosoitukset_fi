package main

import (
	"context"
	"testing"

	"go.mongodb.org/mongo-driver/v2/bson"
)

func TestPanicSwitchActiveSeesEitherSwitch(t *testing.T) {
	db := reminderTestDB(t)
	ctx := context.Background()
	panics := db.Collection("panic")

	if active, err := panicSwitchActive(ctx, db); err != nil || active {
		t.Fatalf("no switch documents: active=%v err=%v", active, err)
	}
	// The admin panel's global switch is off and stored first; a security
	// lockdown is on. The lockdown must still count.
	if _, err := panics.InsertOne(ctx, bson.M{"name": "global", "panic": false}); err != nil {
		t.Fatal(err)
	}
	if _, err := panics.InsertOne(ctx, bson.M{"_id": "security-lockdown", "name": "security", "panic": true}); err != nil {
		t.Fatal(err)
	}
	if active, err := panicSwitchActive(ctx, db); err != nil || !active {
		t.Fatalf("security lockdown on: active=%v err=%v", active, err)
	}
	if _, err := panics.UpdateOne(ctx, bson.M{"_id": "security-lockdown"}, bson.M{"$set": bson.M{"panic": false}}); err != nil {
		t.Fatal(err)
	}
	if active, err := panicSwitchActive(ctx, db); err != nil || active {
		t.Fatalf("both off: active=%v err=%v", active, err)
	}
	if _, err := panics.UpdateOne(ctx, bson.M{"name": "global"}, bson.M{"$set": bson.M{"panic": true}}); err != nil {
		t.Fatal(err)
	}
	if active, err := panicSwitchActive(ctx, db); err != nil || !active {
		t.Fatalf("global on: active=%v err=%v", active, err)
	}
	// Other documents in the collection never count.
	if _, err := panics.UpdateOne(ctx, bson.M{"name": "global"}, bson.M{"$set": bson.M{"panic": false}}); err != nil {
		t.Fatal(err)
	}
	if _, err := panics.InsertOne(ctx, bson.M{"name": "other", "panic": true}); err != nil {
		t.Fatal(err)
	}
	if active, err := panicSwitchActive(ctx, db); err != nil || active {
		t.Fatalf("unrelated document: active=%v err=%v", active, err)
	}
}
