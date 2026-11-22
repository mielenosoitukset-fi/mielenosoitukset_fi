package main

import (
	"context"
	"os"
	"testing"
	"time"

	"go.mongodb.org/mongo-driver/v2/mongo"
)

// The MongoDB role grants exact names. These integration tests run serially
// within the Go package, and the runner handles one job at a time.
func ciMongoDB(t *testing.T, client *mongo.Client, localName, purpose string) *mongo.Database {
	t.Helper()
	slot := os.Getenv("MOSO_CI_MONGO_SLOT")
	if slot == "" {
		return client.Database(localName)
	}
	switch slot {
	case "02", "03", "04":
	default:
		t.Fatal("invalid shared MongoDB slot")
	}
	db := client.Database("moso_go_" + slot + "_" + purpose)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := db.Drop(ctx); err != nil {
		t.Fatalf("reset shared MongoDB database: %v", err)
	}
	return db
}
