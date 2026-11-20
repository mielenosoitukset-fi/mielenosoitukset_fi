package main

import (
	"context"
	"errors"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
)

type fakeDeletionStore struct {
	open    int64
	owners  []bson.ObjectID
	reasons []string
	err     error
}

func (f *fakeDeletionStore) OpenDeletionRequest(context.Context, bson.ObjectID) (int64, error) {
	return f.open, f.err
}

func (f *fakeDeletionStore) CreateDeletionRequest(_ context.Context, owner bson.ObjectID, reason string, _ time.Time) (int64, error) {
	if f.err != nil {
		return 0, f.err
	}
	f.owners, f.reasons = append(f.owners, owner), append(f.reasons, reason)
	f.open = 100123
	return 100123, nil
}

func deletionRig(t *testing.T) (accountWriteRig, *fakeDeletionStore) {
	g := newAccountWriteRig(t, true)
	store := &fakeDeletionStore{}
	g.writes.deletions = store
	return g, store
}

func TestAccountDeletionRequestOffPassesThrough(t *testing.T) {
	g := newAccountWriteRig(t, false)
	if w := g.postTo(accountDeletionPath, `{"confirm":true}`); w.Code != 299 {
		t.Fatal(w.Code)
	}
}

func TestAccountDeletionRequestGuards(t *testing.T) {
	g, store := deletionRig(t)
	for _, h := range [][]string{
		{"Sec-Fetch-Site", "cross-site"}, {"Origin", "https://evil.test"},
		{developerCSRFHeader, "-"}, {"Content-Type", "text/plain"},
	} {
		if w := g.postTo(accountDeletionPath, `{"confirm":true}`, h...); w.Code < 400 {
			t.Errorf("%v: got %d", h, w.Code)
		}
	}
	r := httptest.NewRequest("POST", accountDeletionPath, strings.NewReader(`{"confirm":true}`))
	w := httptest.NewRecorder()
	g.h.ServeHTTP(w, r)
	if w.Code != 401 && w.Code != 503 {
		t.Errorf("anonymous: got %d", w.Code)
	}
	if len(store.owners) != 0 {
		t.Fatal("rejected requests must not open a case")
	}
}

func TestAccountDeletionRequestNeedsConfirmAndValidReason(t *testing.T) {
	g, store := deletionRig(t)
	for _, body := range []string{`{}`, `{"confirm":false}`, `{"confirm":true,"reason":"` + strings.Repeat("a", 1001) + `"}`,
		`{"confirm":true,"reason":"a\u0007b"}`, `not json`} {
		if w := g.postTo(accountDeletionPath, body); w.Code != 400 {
			t.Errorf("%.40s: got %d", body, w.Code)
		}
	}
	if len(store.owners) != 0 {
		t.Fatal("invalid requests must not open a case")
	}
}

func TestAccountDeletionRequestOpensOneCaseForTheBridgeActor(t *testing.T) {
	g, store := deletionRig(t)
	w := g.postTo(accountDeletionPath, `{"confirm":true,"reason":"  En käytä enää  "}`)
	if w.Code != 201 || !strings.Contains(w.Body.String(), `"state":"created"`) || !strings.Contains(w.Body.String(), `"case":100123`) {
		t.Fatal(w.Code, w.Body.String())
	}
	if len(store.owners) != 1 || store.owners[0].Hex() != testActor || store.reasons[0] != "En käytä enää" {
		t.Fatal(store.owners, store.reasons)
	}
	if len(g.store.audits) != 1 || g.store.audits[0]["action"] != "account deletion requested" {
		t.Fatal(g.store.audits)
	}
	for k, v := range g.store.audits[0] {
		if s, ok := v.(string); ok && strings.Contains(s, "käytä") {
			t.Errorf("audit field %s carries the reason", k)
		}
	}
	again := g.postTo(accountDeletionPath, `{"confirm":true}`)
	if again.Code != 200 || !strings.Contains(again.Body.String(), `"state":"pending"`) || len(store.owners) != 1 {
		t.Fatal("second request must return the open case", again.Code, again.Body.String())
	}
}

func TestAccountDeletionRequestStoreFailure(t *testing.T) {
	g, store := deletionRig(t)
	store.err = errors.New("down")
	if w := g.postTo(accountDeletionPath, `{"confirm":true}`); w.Code != 503 {
		t.Fatal(w.Code)
	}
}

func TestCleanDeletionReason(t *testing.T) {
	if got, ok := cleanDeletionReason(" a‮b\r\nc "); !ok || got != "ab\nc" {
		t.Fatalf("%q %v", got, ok)
	}
}
