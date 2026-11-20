package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/event"
)

// The same vectors run in tests/test_audit_trail.py, so both services redact alike.
func TestAuditRedactionVectors(t *testing.T) {
	data, err := os.ReadFile("testdata/audit_redaction.json")
	if err != nil {
		t.Fatal(err)
	}
	var vectors []struct {
		Name, Mode, Collection string
		Input, Expected        any
	}
	if err := json.Unmarshal(data, &vectors); err != nil {
		t.Fatal(err)
	}
	for _, v := range vectors {
		mode := auditRedactMode{filter: v.Mode == "filter", secretCollection: v.Mode == "change" && auditSecretCollections[v.Collection]}
		got := auditRedact(v.Input, mode, "", 0)
		gotJSON, _ := json.Marshal(got)
		var normalized any
		_ = json.Unmarshal(gotJSON, &normalized)
		if !reflect.DeepEqual(normalized, v.Expected) {
			t.Errorf("%s:\n got  %s\n want %v", v.Name, gotJSON, v.Expected)
		}
	}
}

func rawCommand(t *testing.T, doc bson.D) bson.Raw {
	t.Helper()
	raw, err := bson.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func TestAuditMonitorRecordsRequestWrites(t *testing.T) {
	trail := newAuditTrail()
	var written []auditWrite
	done := make(chan struct{}, 4)
	trail.write = func(_ context.Context, w auditWrite) error {
		written = append(written, w)
		done <- struct{}{}
		return nil
	}
	trail.run(nil)
	monitor := trail.monitor()

	id := bson.NewObjectID()
	var ctx context.Context
	handler := auditRequestHandler(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) { ctx = r.Context() }))
	handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("POST", "/kampanja/confirm/AbCdEfGhIjKlMnOpQrStUv", nil))
	ctx = context.WithValue(ctx, principalKey{}, Principal{Authenticated: true, ActorID: "u1", Name: "Secret Name"})

	cmd := bson.D{{Key: "update", Value: "volunteers"}, {Key: "updates", Value: bson.A{
		bson.D{{Key: "q", Value: bson.D{{Key: "_id", Value: id}}}, {Key: "u", Value: bson.D{{Key: "$set", Value: bson.D{{Key: "confirmed", Value: true}, {Key: "token_hash", Value: "abc"}}}}}},
	}}}
	monitor.Started(ctx, &event.CommandStartedEvent{Command: rawCommand(t, cmd), DatabaseName: "db", CommandName: "update", RequestID: 7, ConnectionID: "c1"})
	reply := rawCommand(t, bson.D{{Key: "ok", Value: 1.0}, {Key: "n", Value: int32(1)}, {Key: "nModified", Value: int32(1)}})
	monitor.Succeeded(ctx, &event.CommandSucceededEvent{CommandFinishedEvent: event.CommandFinishedEvent{CommandName: "update", DatabaseName: "db", RequestID: 7, ConnectionID: "c1", Duration: time.Millisecond}, Reply: reply})

	// A claim that matched nothing is not an edit.
	monitor.Started(ctx, &event.CommandStartedEvent{Command: rawCommand(t, cmd), DatabaseName: "db", CommandName: "update", RequestID: 8, ConnectionID: "c1"})
	noop := rawCommand(t, bson.D{{Key: "ok", Value: 1.0}, {Key: "n", Value: int32(0)}, {Key: "nModified", Value: int32(0)}})
	monitor.Succeeded(ctx, &event.CommandSucceededEvent{CommandFinishedEvent: event.CommandFinishedEvent{CommandName: "update", RequestID: 8, ConnectionID: "c1"}, Reply: noop})

	// Reads are not recorded.
	find := bson.D{{Key: "find", Value: "volunteers"}, {Key: "filter", Value: bson.D{}}}
	monitor.Started(ctx, &event.CommandStartedEvent{Command: rawCommand(t, find), DatabaseName: "db", CommandName: "find", RequestID: 9, ConnectionID: "c1"})

	trail.close(time.Second)
	if len(written) != 1 {
		t.Fatalf("want 1 event, got %d", len(written))
	}
	doc := written[0].doc
	if doc["collection"] != "volunteers" || doc["op"] != "update" {
		t.Fatalf("unexpected event %v", doc)
	}
	if actor := doc["actor"].(bson.M); actor["kind"] != "user" || actor["user_id"] != "u1" || len(actor) != 2 {
		t.Fatalf("actor must be the user id only: %v", actor)
	}
	via := doc["via"].(bson.M)
	if via["kind"] != "http" || via["method"] != "POST" || via["rule"] != "/kampanja/confirm/:token" {
		t.Fatalf("via must name the route without the link token: %v", via)
	}
	change, _ := json.Marshal(doc["change"])
	if string(change) == "" || !json.Valid(change) || strings.Contains(string(change), `"abc"`) {
		t.Fatalf("secret leaked into change: %s", change)
	}
	if ids := doc["doc_ids"].(bson.A); len(ids) != 1 || ids[0] != id {
		t.Fatalf("doc ids: %v", ids)
	}
}

func TestAuditJobContextAndShutdown(t *testing.T) {
	ctx := withAuditJob(context.Background(), "submission-recovery")
	fields := auditContext(ctx)
	if fields["actor"].(bson.M)["name"] != "submission-recovery" || fields["via"].(bson.M)["kind"] != "job" {
		t.Fatalf("job context: %v", fields)
	}
	trail := newAuditTrail()
	trail.write = func(context.Context, auditWrite) error { return nil }
	trail.run(nil)
	trail.close(time.Second)
	trail.enqueue(auditWrite{db: "db", doc: bson.M{"op": "update"}}) // must not panic after close
}

func TestAuditDescribeSchemaChanges(t *testing.T) {
	drop := auditDescribe("drop", bson.D{{Key: "drop", Value: "cases"}})
	if drop["op"] != "ddl:drop" || drop["collection"] != "cases" {
		t.Fatalf("drop: %v", drop)
	}
	if auditDescribe("insert", bson.D{{Key: "insert", Value: "admin_logs"}}) != nil {
		t.Fatal("log inserts are records already")
	}
	if auditDescribe("update", bson.D{{Key: "update", Value: "site_analytics"}}) != nil {
		t.Fatal("telemetry counters are skipped")
	}
	out := auditDescribe("aggregate", bson.D{{Key: "aggregate", Value: "demonstrations"}, {Key: "pipeline", Value: bson.A{bson.D{{Key: "$out", Value: "copy"}}}}})
	if out["op"] != "aggregate_out" || out["collection"] != "copy" {
		t.Fatalf("aggregate $out: %v", out)
	}
}
