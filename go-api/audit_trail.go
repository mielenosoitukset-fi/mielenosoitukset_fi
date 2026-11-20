package main

// Driver-level audit trail for the Go API, the twin of
// mielenosoitukset_fi/utils/audit_trail.py. The MongoDB client built in main.go
// carries audit.monitor(), so every write any handler or background loop makes
// is recorded in audit_events with who (the auth-bridge principal, never an
// email, name or IP), when, what (filter, update operators or inserted
// document, secrets redacted) and through which path (method and route, or the
// background job). tests/test_audit_trail_coverage.py fails when code opens a
// client elsewhere.

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/event"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

const (
	auditCollection     = "audit_events"
	auditSchemaVersion  = 1
	auditDefaultDays    = 730
	auditRedacted       = "[redacted]"
	auditMaxString      = 300
	auditMaxList        = 50
	auditMaxDepth       = 8
	auditQueueSize      = 2048
	auditWriteTimeout   = 2 * time.Second
	auditMaxLoggedBytes = 8000
)

// Keep in sync with SKIPPED_COLLECTIONS in audit_trail.py (a test compares them).
var auditSkippedCollections = map[string]bool{
	"audit_events":            true,
	"analytics":               true,
	"d_analytics":             true,
	"prepped_analytics":       true,
	"site_analytics":          true,
	"site_analytics_visitors": true,
	"web_vitals_daily":        true,
	"api_usage":               true,
	"background_job_leader":   true,
}

var auditAppendOnlyLogs = map[string]bool{
	"admin_logs": true, "super_audit_logs": true, "demo_audit_logs": true, "login_logs": true,
	"board_audit_logs": true, "demo_edit_history": true, "background_job_runs": true, "token_usage_logs": true,
}

var auditSecretCollections = map[string]bool{
	"api_tokens": true, "magic_links": true, "mfas": true, "mfa_login_challenges": true,
	"user_passkeys": true, "passkey_challenges": true, "fresh_step_up_tokens": true,
	"used_password_change_tokens": true, "demo_cancellation_tokens": true, "demo_submission_tokens": true,
	"contact_form_tokens": true, "hallinto_sessions": true, "oauth_authorization_codes": true,
	"erasure_identity_seals": true,
}

var (
	auditSecretKey   = regexp.MustCompile(`(?i)(pass(word|wd)?|pwd|hash|secret|token|cookie|session|otp|totp|recovery|backup_code|api_?key|authori[sz]ation|credential|private|signature|nonce|challenge|seal|(^|_)codes?$|^code_|(^|_)ips?$|ip_address|remote_addr|user_agent|forwarded)`)
	auditSecretValue = regexp.MustCompile(`^(\$2[aby]\$|\$argon2|\$scrypt|pbkdf2:|scrypt:|sha256\$|gAAAA|eyJ)|^\d{1,3}(\.\d{1,3}){3}$|^[0-9a-fA-F]{0,4}(:[0-9a-fA-F]{0,4}){2,7}$`)
	auditIDKey       = regexp.MustCompile(`(^_?id$|_ids?$|^slug$|^status$|^type$|^kind$|^job_key$|^key$)`)
	auditTokenPath   = regexp.MustCompile(`^[A-Za-z0-9_\-.~]{16,}$`)
	auditObjectID    = regexp.MustCompile(`^[0-9a-f]{24}$`)
)

var auditWriteCommands = map[string]bool{"insert": true, "update": true, "delete": true, "findAndModify": true, "findandmodify": true}
var auditDDLCommands = map[string]bool{"create": true, "drop": true, "dropDatabase": true, "renameCollection": true, "createIndexes": true, "dropIndexes": true, "collMod": true}

type auditRedactMode struct {
	secretCollection bool
	filter           bool
}

func auditHarmless(value any) bool {
	switch value.(type) {
	case nil, bool, int, int32, int64, float32, float64, time.Time, bson.DateTime, bson.ObjectID, bson.Timestamp, bson.Decimal128:
		return true
	}
	return false
}

func auditScalar(value any) any {
	switch v := value.(type) {
	case string:
		if auditSecretValue.MatchString(v) {
			return auditRedacted
		}
		if len(v) > auditMaxString {
			return v[:auditMaxString] + fmt.Sprintf("…[%d chars]", len(v))
		}
		return v
	case bson.Binary:
		return fmt.Sprintf("[bytes %d]", len(v.Data))
	case []byte:
		return fmt.Sprintf("[bytes %d]", len(v))
	}
	text := fmt.Sprint(value)
	if len(text) > auditMaxString {
		text = text[:auditMaxString]
	}
	return text
}

// auditRedact copies value for the trail without secrets. key is the field the
// value belongs to; operators ($set, $in ...) pass it on to their operands.
func auditRedact(value any, mode auditRedactMode, key string, depth int) any {
	if depth > auditMaxDepth {
		return "[depth]"
	}
	last := key
	if i := strings.LastIndex(key, "."); i >= 0 {
		last = key[i+1:]
	}
	idKey := last != "" && auditIDKey.MatchString(last)
	secretKey := key != "" && auditSecretKey.MatchString(key) && !idKey
	redactField := func(k string, v any) (string, any) {
		var child string
		switch {
		case k == "$or" || k == "$and" || k == "$nor":
			child = ""
		case strings.HasPrefix(k, "$"):
			child = key
		case secretKey:
			if auditHarmless(v) {
				return k, v
			}
			return k, auditRedacted
		default:
			child = k
		}
		return k, auditRedact(v, mode, child, depth+1)
	}
	switch v := value.(type) {
	case bson.D:
		out := bson.D{}
		for _, e := range v {
			k, r := redactField(e.Key, e.Value)
			out = append(out, bson.E{Key: k, Value: r})
		}
		return out
	case bson.M:
		out := bson.M{}
		for k, item := range v {
			k, r := redactField(k, item)
			out[k] = r
		}
		return out
	case map[string]any:
		out := map[string]any{}
		for k, item := range v {
			k, r := redactField(k, item)
			out[k] = r
		}
		return out
	case bson.A:
		return auditRedactList([]any(v), mode, key, depth)
	case []any:
		return auditRedactList(v, mode, key, depth)
	}
	if auditHarmless(value) {
		return value
	}
	if secretKey || (mode.secretCollection && !idKey) {
		return auditRedacted
	}
	if _, isString := value.(string); isString && mode.filter && !idKey {
		return "[str]"
	}
	return auditScalar(value)
}

func auditRedactList(items []any, mode auditRedactMode, key string, depth int) []any {
	out := []any{}
	for i, item := range items {
		if i >= auditMaxList {
			out = append(out, fmt.Sprintf("…[%d items]", len(items)))
			break
		}
		out = append(out, auditRedact(item, mode, key, depth+1))
	}
	return out
}

func auditGet(doc bson.D, key string) any {
	for _, e := range doc {
		if e.Key == key {
			return e.Value
		}
	}
	return nil
}

func auditDocs(value any) []bson.D {
	var out []bson.D
	switch v := value.(type) {
	case bson.A:
		for _, item := range v {
			if d, ok := item.(bson.D); ok {
				out = append(out, d)
			}
		}
	}
	return out
}

func auditFilterIDs(filter any) []any {
	d, ok := filter.(bson.D)
	if !ok {
		return nil
	}
	id := auditGet(d, "_id")
	if id == nil {
		return nil
	}
	if inner, ok := id.(bson.D); ok {
		if list, ok := auditGet(inner, "$in").(bson.A); ok {
			if len(list) > auditMaxList {
				list = list[:auditMaxList]
			}
			return []any(list)
		}
		return nil
	}
	return []any{id}
}

func auditTruthy(value any) bool {
	switch v := value.(type) {
	case bool:
		return v
	case int32:
		return v != 0
	case int64:
		return v != 0
	case float64:
		return v != 0
	}
	return false
}

// auditDescribe returns the event body for a write command, nil for reads.
func auditDescribe(name string, cmd bson.D) bson.M {
	if name == "aggregate" {
		stages, _ := auditGet(cmd, "pipeline").(bson.A)
		for _, stage := range stages {
			d, ok := stage.(bson.D)
			if !ok {
				continue
			}
			target := auditGet(d, "$out")
			if target == nil {
				target = auditGet(d, "$merge")
			}
			if target == nil {
				continue
			}
			if inner, ok := target.(bson.D); ok {
				if into := auditGet(inner, "into"); into != nil {
					target = into
				} else {
					target = auditGet(inner, "coll")
				}
			}
			if inner, ok := target.(bson.D); ok {
				target = auditGet(inner, "coll")
			}
			return bson.M{"op": "aggregate_out", "collection": fmt.Sprint(target), "source": fmt.Sprint(auditGet(cmd, "aggregate"))}
		}
		return nil
	}
	if !auditWriteCommands[name] && !auditDDLCommands[name] {
		return nil
	}
	collection := fmt.Sprint(auditGet(cmd, name))
	switch name {
	case "renameCollection":
		if i := strings.Index(collection, "."); i >= 0 {
			collection = collection[i+1:]
		}
	case "dropDatabase":
		collection = "*"
	}
	if auditSkippedCollections[collection] {
		return nil
	}
	secret := auditRedactMode{secretCollection: auditSecretCollections[collection]}
	filter := auditRedactMode{filter: true}
	switch name {
	case "insert":
		if auditAppendOnlyLogs[collection] {
			return nil
		}
		docs := auditDocs(auditGet(cmd, "documents"))
		ids, change := bson.A{}, bson.A{}
		for i, d := range docs {
			if id := auditGet(d, "_id"); id != nil && len(ids) < auditMaxList {
				ids = append(ids, id)
			}
			if i < 20 {
				change = append(change, auditRedact(d, secret, "", 0))
			}
		}
		return bson.M{"op": "insert", "collection": collection, "doc_ids": ids, "change": change, "count": len(docs)}
	case "update", "delete":
		field := map[string]string{"update": "updates", "delete": "deletes"}[name]
		entries := auditDocs(auditGet(cmd, field))
		ids, filters, changes := bson.A{}, bson.A{}, bson.A{}
		multi, upsert := false, false
		for i, e := range entries {
			q := auditGet(e, "q")
			for _, id := range auditFilterIDs(q) {
				if len(ids) < auditMaxList {
					ids = append(ids, id)
				}
			}
			if i < 20 {
				filters = append(filters, auditRedact(q, filter, "", 0))
				if name == "update" {
					changes = append(changes, auditRedact(auditGet(e, "u"), secret, "", 0))
				}
			}
			if name == "update" {
				multi = multi || auditTruthy(auditGet(e, "multi"))
				upsert = upsert || auditTruthy(auditGet(e, "upsert"))
			} else if limit := auditGet(e, "limit"); limit != nil && !auditTruthy(limit) {
				multi = true
			}
		}
		body := bson.M{"op": name, "collection": collection, "doc_ids": ids, "filter": filters, "multi": multi, "count": len(entries)}
		if name == "update" {
			body["change"] = changes
			body["upsert"] = upsert
		}
		return body
	case "findAndModify", "findandmodify":
		q := auditGet(cmd, "query")
		op := "update"
		if auditTruthy(auditGet(cmd, "remove")) {
			op = "delete"
		}
		ids := bson.A{}
		for _, id := range auditFilterIDs(q) {
			ids = append(ids, id)
		}
		body := bson.M{"op": op, "collection": collection, "doc_ids": ids, "filter": bson.A{auditRedact(q, filter, "", 0)}, "upsert": auditTruthy(auditGet(cmd, "upsert")), "count": 1}
		if op == "update" {
			body["change"] = bson.A{auditRedact(auditGet(cmd, "update"), secret, "", 0)}
		}
		return body
	}
	body := bson.M{"op": "ddl:" + name, "collection": collection}
	detail := bson.D{}
	for _, e := range cmd {
		switch e.Key {
		case name, "lsid", "$db", "$clusterTime", "txnNumber":
			continue
		}
		detail = append(detail, e)
	}
	if len(detail) > 0 {
		body["change"] = bson.A{auditRedact(detail, auditRedactMode{}, "", 0)}
	}
	if name == "renameCollection" {
		body["to"] = fmt.Sprint(auditGet(cmd, "to"))
	}
	return body
}

func auditInt(value any) int64 {
	switch v := value.(type) {
	case int32:
		return int64(v)
	case int64:
		return v
	case float64:
		return int64(v)
	}
	return 0
}

func auditOutcome(name string, reply bson.D) bson.M {
	out := bson.M{"ok": auditGet(reply, "ok") == nil || auditTruthy(auditGet(reply, "ok"))}
	if name == "findAndModify" || name == "findandmodify" {
		last, _ := auditGet(reply, "lastErrorObject").(bson.D)
		out["n"] = auditInt(auditGet(last, "n"))
		if up := auditGet(last, "upserted"); up != nil {
			out["upserted"] = bson.A{up}
		}
		if value, ok := auditGet(reply, "value").(bson.D); ok {
			if id := auditGet(value, "_id"); id != nil {
				out["doc_id"] = id
			}
		}
		return out
	}
	if n := auditGet(reply, "n"); n != nil {
		out["n"] = auditInt(n)
	}
	if m := auditGet(reply, "nModified"); m != nil {
		out["modified"] = auditInt(m)
	}
	if ups, ok := auditGet(reply, "upserted").(bson.A); ok && len(ups) > 0 {
		ids := bson.A{}
		for _, u := range ups {
			if d, ok := u.(bson.D); ok {
				ids = append(ids, auditGet(d, "_id"))
			}
		}
		out["upserted"] = ids
	}
	if errs, ok := auditGet(reply, "writeErrors").(bson.A); ok && len(errs) > 0 {
		out["ok"] = false
		codes := bson.A{}
		for _, e := range errs {
			if d, ok := e.(bson.D); ok && len(codes) < 10 {
				codes = append(codes, auditInt(auditGet(d, "code")))
			}
		}
		out["errors"] = codes
	}
	return out
}

func auditChangedNothing(op string, outcome bson.M) bool {
	if ok, _ := outcome["ok"].(bool); !ok {
		return false
	}
	if _, failed := outcome["errors"]; failed {
		return false
	}
	if _, upserted := outcome["upserted"]; upserted {
		return false
	}
	if n, has := outcome["n"].(int64); has && n == 0 && (op == "update" || op == "delete") {
		return true
	}
	// Matched but changed nothing: MongoDB left the document as it was.
	m, has := outcome["modified"].(int64)
	return op == "update" && has && m == 0
}

// ---------------------------------------------------------------------------
// Who and through which path
// ---------------------------------------------------------------------------

type auditRequestKey struct{}
type auditJobKey struct{}

type auditRequestInfo struct {
	Method    string
	Path      string
	RequestID string
}

// auditPath keeps route shape but never a bearer link: long opaque segments
// (confirmation and cancellation tokens) become ":token".
func auditPath(path string) string {
	parts := strings.Split(path, "/")
	for i, part := range parts {
		if auditTokenPath.MatchString(part) && !auditObjectID.MatchString(part) {
			parts[i] = ":token"
		}
	}
	return strings.Join(parts, "/")
}

func auditRequestHandler(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		info := auditRequestInfo{Method: r.Method, Path: auditPath(r.URL.Path), RequestID: bson.NewObjectID().Hex()}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), auditRequestKey{}, info)))
	})
}

// withAuditJob names the background loop that writes with ctx.
func withAuditJob(ctx context.Context, name string) context.Context {
	return context.WithValue(ctx, auditJobKey{}, name)
}

var auditHost, _ = os.Hostname()

func auditContext(ctx context.Context) bson.M {
	actor := bson.M{"kind": "system"}
	via := bson.M{"kind": "process", "service": "go-api"}
	ctxFields := bson.M{}
	if ctx != nil {
		if info, ok := ctx.Value(auditRequestKey{}).(auditRequestInfo); ok {
			via = bson.M{"kind": "http", "service": "go-api", "method": info.Method, "rule": info.Path}
			ctxFields["request_id"] = info.RequestID
			actor = bson.M{"kind": "anonymous"}
		}
		if job, ok := ctx.Value(auditJobKey{}).(string); ok {
			via = bson.M{"kind": "job", "service": "go-api", "name": job}
			actor = bson.M{"kind": "job", "name": job}
		}
		if p, ok := PrincipalFromContext(ctx); ok && p.Authenticated && p.ActorID != "" {
			actor = bson.M{"kind": "user", "user_id": p.ActorID}
		}
	}
	via["host"] = auditHost
	via["pid"] = os.Getpid()
	ctxFields["actor"] = actor
	ctxFields["via"] = via
	return ctxFields
}

// ---------------------------------------------------------------------------
// The trail
// ---------------------------------------------------------------------------

type auditWrite struct {
	db  string
	doc bson.M
}

type auditTrail struct {
	pending  sync.Map
	queue    chan auditWrite
	client   *mongo.Client
	indexed  sync.Map
	done     chan struct{}
	disabled bool
	write    func(context.Context, auditWrite) error
	start    sync.Once
	mu       sync.RWMutex
	closed   bool
}

func newAuditTrail() *auditTrail {
	disabled := false
	switch strings.ToLower(strings.TrimSpace(os.Getenv("AUDIT_TRAIL_DISABLED"))) {
	case "1", "true", "yes":
		disabled = true
	}
	return &auditTrail{queue: make(chan auditWrite, auditQueueSize), done: make(chan struct{}), disabled: disabled}
}

var audit = newAuditTrail()

func auditPendingKey(connection string, requestID int64) string {
	return connection + "#" + strconv.FormatInt(requestID, 10)
}

func (a *auditTrail) monitor() *event.CommandMonitor {
	if a.disabled {
		log.Print("AUDIT_TRAIL_DISABLED is set: database writes are not audited")
		return nil
	}
	return &event.CommandMonitor{
		Started: func(ctx context.Context, e *event.CommandStartedEvent) {
			var cmd bson.D
			if e.Command == nil || bson.Unmarshal(e.Command, &cmd) != nil {
				return
			}
			body := auditDescribe(e.CommandName, cmd)
			if body == nil {
				return
			}
			body["v"] = auditSchemaVersion
			body["at"] = time.Now().UTC()
			body["db"] = e.DatabaseName
			body["service"] = "go-api"
			for k, v := range auditContext(ctx) {
				body[k] = v
			}
			a.pending.Store(auditPendingKey(e.ConnectionID, e.RequestID), body)
		},
		Succeeded: func(_ context.Context, e *event.CommandSucceededEvent) {
			var reply bson.D
			_ = bson.Unmarshal(e.Reply, &reply)
			a.finish(e.CommandFinishedEvent, auditOutcome(e.CommandName, reply))
		},
		Failed: func(_ context.Context, e *event.CommandFailedEvent) {
			outcome := bson.M{"ok": false}
			if se, ok := e.Failure.(mongo.ServerError); ok {
				if codes := se.ErrorCodes(); len(codes) > 0 {
					outcome["error_code"] = codes[0]
				}
			}
			a.finish(e.CommandFinishedEvent, outcome)
		},
	}
}

func (a *auditTrail) finish(e event.CommandFinishedEvent, outcome bson.M) {
	value, ok := a.pending.LoadAndDelete(auditPendingKey(e.ConnectionID, e.RequestID))
	if !ok {
		return
	}
	body := value.(bson.M)
	body["outcome"] = outcome
	body["duration_ms"] = float64(e.Duration.Microseconds()) / 1000
	if auditChangedNothing(fmt.Sprint(body["op"]), outcome) {
		return
	}
	a.enqueue(auditWrite{db: e.DatabaseName, doc: body})
}

func (a *auditTrail) enqueue(w auditWrite) {
	a.mu.RLock()
	defer a.mu.RUnlock()
	if a.closed {
		auditFallback(w, "after shutdown")
		return
	}
	select {
	case a.queue <- w:
	default:
		auditFallback(w, "queue full")
	}
}

func auditFallback(w auditWrite, reason string) {
	data, err := json.Marshal(w.doc)
	if err != nil {
		data = []byte(fmt.Sprint(w.doc))
	}
	if len(data) > auditMaxLoggedBytes {
		data = data[:auditMaxLoggedBytes]
	}
	log.Printf("AUDIT_FALLBACK (%s) %s", reason, data)
}

func auditRetentionDays() int {
	days, err := strconv.Atoi(os.Getenv("AUDIT_RETENTION_DAYS"))
	if err != nil || days <= 0 {
		return auditDefaultDays
	}
	if days < 30 {
		return 30
	}
	return days
}

func (a *auditTrail) ensureIndexes(ctx context.Context, db string) {
	if _, seen := a.indexed.LoadOrStore(db, true); seen {
		return
	}
	_, err := a.client.Database(db).Collection(auditCollection).Indexes().CreateMany(ctx, []mongo.IndexModel{
		{Keys: bson.D{{Key: "at", Value: 1}}, Options: options.Index().SetName("audit_retention").SetExpireAfterSeconds(int32(auditRetentionDays() * 86400))},
		{Keys: bson.D{{Key: "collection", Value: 1}, {Key: "doc_ids", Value: 1}, {Key: "at", Value: -1}}, Options: options.Index().SetName("audit_by_document")},
		{Keys: bson.D{{Key: "actor.user_id", Value: 1}, {Key: "at", Value: -1}}, Options: options.Index().SetName("audit_by_actor")},
	})
	if err != nil {
		log.Printf("Could not ensure audit_events indexes (%s)", dbErrorKind(err))
	}
}

// run starts the single writer. Writes go through the audited client, but the
// audit_events collection is skipped, so they do not record themselves.
func (a *auditTrail) run(client *mongo.Client) {
	if a.disabled {
		close(a.done)
		return
	}
	a.client = client
	if a.write == nil {
		a.write = func(ctx context.Context, w auditWrite) error {
			a.ensureIndexes(ctx, w.db)
			_, err := a.client.Database(w.db).Collection(auditCollection).InsertOne(ctx, w.doc)
			return err
		}
	}
	a.start.Do(func() {
		go func() {
			defer close(a.done)
			for w := range a.queue {
				ctx, cancel := context.WithTimeout(context.Background(), auditWriteTimeout)
				if err := a.write(ctx, w); err != nil {
					auditFallback(w, dbErrorKind(err))
				}
				cancel()
			}
		}()
	})
}

// close drains queued events before the client disconnects.
func (a *auditTrail) close(timeout time.Duration) {
	if a.disabled {
		return
	}
	a.mu.Lock()
	if a.closed {
		a.mu.Unlock()
		return
	}
	a.closed = true
	close(a.queue)
	a.mu.Unlock()
	select {
	case <-a.done:
	case <-time.After(timeout):
		log.Print("Audit trail did not drain before shutdown")
	}
}
