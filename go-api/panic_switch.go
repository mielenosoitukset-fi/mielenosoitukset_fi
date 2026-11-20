package main

import (
	"context"
	"errors"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

// panicSwitchActive reports whether the global or the security panic switch is
// on. The panic collection holds one document per switch ("global" from the
// admin panel, "security" from a lockdown case), so the query asks for an active
// one: reading whichever document comes first could miss the switch that is on.
func panicSwitchActive(ctx context.Context, db *mongo.Database) (bool, error) {
	err := db.Collection("panic").FindOne(ctx,
		bson.M{"name": bson.M{"$in": bson.A{"global", "security"}}, "panic": true},
		options.FindOne().SetProjection(bson.M{"_id": 1})).Err()
	if errors.Is(err, mongo.ErrNoDocuments) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	return true, nil
}
