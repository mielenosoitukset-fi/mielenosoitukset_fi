package main

// Self-service account deletion request (docs/account-erasure.md, decision 20 Nov 2026).
//
// POST /api/account/deletion-request exists only with the other account writes
// (ACCOUNT_SETTINGS_WRITES_ENABLED=true) and uses the same guards: bridge sign-in,
// same-origin fetch metadata, Origin, JSON, account-bound CSRF token, rate limit.
// It opens one admin case of type account_deletion_request for the signed-in
// account; the case notice job tells the admins. Nothing is deleted here: an admin
// deletes the account through the existing admin delete, which applies the erasure
// rules. A second request while one is open returns the open case.

import (
	"context"
	"net/http"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

const (
	accountDeletionPath      = "/api/account/deletion-request"
	accountDeletionCaseType  = "account_deletion_request"
	accountDeletionReasonMax = 1000
)

// accountDeletionStore is kept apart from accountWriteStore so the other writes'
// fakes need not change.
type accountDeletionStore interface {
	// OpenDeletionRequest returns the running number of an open request, or 0.
	OpenDeletionRequest(ctx context.Context, owner bson.ObjectID) (int64, error)
	// CreateDeletionRequest opens the case and returns its running number.
	CreateDeletionRequest(ctx context.Context, owner bson.ObjectID, reason string, now time.Time) (int64, error)
}

func (a *accountWrites) serveDeletionRequest(w http.ResponseWriter, r *http.Request, resolver *sessionResolver) {
	a.serve(w, r, resolver, a.requestDeletion)
}

// cleanDeletionReason trims the optional reason; ok is false when it is refused.
func cleanDeletionReason(raw string) (string, bool) {
	text := strings.TrimSpace(strings.ReplaceAll(stripBidi(raw), "\r\n", "\n"))
	if utf8.RuneCountInString(text) > accountDeletionReasonMax {
		return "", false
	}
	for _, c := range text {
		if unicode.IsControl(c) && c != '\n' && c != '\t' {
			return "", false
		}
	}
	return text, true
}

func (a *accountWrites) requestDeletion(ctx context.Context, w http.ResponseWriter, r *http.Request, owner bson.ObjectID) error {
	if a.deletions == nil {
		writeError(w, http.StatusNotFound, "not found")
		return nil
	}
	var body struct {
		Confirm bool    `json:"confirm"`
		Reason  *string `json:"reason"`
	}
	if !decodeDeveloperBody(w, r, &body) {
		return nil
	}
	if !body.Confirm {
		writeError(w, http.StatusBadRequest, "Vahvista poistopyyntö.")
		return nil
	}
	reason := ""
	if body.Reason != nil {
		var ok bool
		if reason, ok = cleanDeletionReason(*body.Reason); !ok {
			writeError(w, http.StatusBadRequest, "Perustelu on liian pitkä tai sisältää kiellettyjä merkkejä.")
			return nil
		}
	}
	open, err := a.deletions.OpenDeletionRequest(ctx, owner)
	if err != nil {
		return err
	}
	if open != 0 {
		writeJSON(w, http.StatusOK, map[string]any{"status": "success", "state": "pending", "case": open})
		return nil
	}
	running, err := a.deletions.CreateDeletionRequest(ctx, owner, reason, a.guard.now().UTC())
	if err != nil {
		return err
	}
	// The audit row never carries the reason.
	_ = a.store.Audit(ctx, bson.M{"user_id": owner, "action": "account deletion requested", "ip_address": submissionClientAddress(r),
		"timestamp": bson.NewDateTimeFromTime(a.guard.now().UTC()), "source": "go-api"})
	writeJSON(w, http.StatusCreated, map[string]any{"status": "success", "state": "created", "case": running})
	return nil
}

type mongoAccountDeletionStore struct{ db *mongo.Database }

func (s mongoAccountDeletionStore) OpenDeletionRequest(ctx context.Context, owner bson.ObjectID) (int64, error) {
	var found struct {
		Running any `bson:"running_num"`
	}
	err := s.db.Collection("cases").FindOne(ctx, bson.M{
		"type": accountDeletionCaseType, "submitter_id": owner, "meta.closed": bson.M{"$ne": true},
	}, options.FindOne().SetProjection(bson.M{"running_num": 1})).Decode(&found)
	if err == mongo.ErrNoDocuments {
		return 0, nil
	}
	if err != nil {
		return 0, err
	}
	switch typed := found.Running.(type) {
	case int32:
		return int64(typed), nil
	case int64:
		return typed, nil
	case float64:
		return int64(typed), nil
	}
	return -1, nil // open, number unknown
}

func (s mongoAccountDeletionStore) CreateDeletionRequest(ctx context.Context, owner bson.ObjectID, reason string, now time.Time) (int64, error) {
	var running int64
	meta := bson.D{{Key: "urgency", Value: "normal"}}
	if reason != "" {
		meta = append(meta, bson.E{Key: "reason", Value: reason})
	}
	err := mongoSuggestionStore{s.db}.CreateCase(ctx, func(next int64) bson.D {
		running = next
		return bson.D{
			{Key: "_id", Value: bson.NewObjectID()}, {Key: "type", Value: accountDeletionCaseType},
			{Key: "demo_id", Value: nil}, {Key: "organization_id", Value: nil}, {Key: "submitter_id", Value: owner},
			{Key: "submitter", Value: nil}, {Key: "error_report", Value: bson.D{}}, {Key: "suggestion", Value: bson.D{}},
			{Key: "meta", Value: meta}, {Key: "action_logs", Value: bson.A{}},
			{Key: "case_history", Value: bson.A{}}, {Key: "running_num", Value: next},
			{Key: "created_at", Value: bson.NewDateTimeFromTime(now)}, {Key: "updated_at", Value: bson.NewDateTimeFromTime(now)},
		}
	})
	return running, err
}
