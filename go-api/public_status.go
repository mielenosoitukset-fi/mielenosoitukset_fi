package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
)

// Gate policy when the lockdown status read fails (timeout, connection error):
//   - A read that says "locked" applies at once and is sticky: a failed read
//     never reopens a site that was locked.
//   - After a read that said "not locked", a failed read keeps that answer for
//     availabilityGrace (default 15 minutes, PUBLIC_GATE_STALE_SECONDS). After
//     that the gate fails closed.
//   - A process that never had a successful answer (restart during a database
//     outage) stays open, unless the local emergency override says closed.
//
// The lockdown lives in the same database, so an outage cannot be used to
// bypass it: a locked answer is sticky and a new lockdown cannot be written
// while the database is down. See docs/decisions.md.
var availabilityGrace = gateStaleWindow()

func gateStaleWindow() time.Duration {
	if n, err := strconv.Atoi(os.Getenv("PUBLIC_GATE_STALE_SECONDS")); err == nil && n > 0 {
		return time.Duration(n) * time.Second
	}
	return 15 * time.Minute
}

// lockdownOverride reports the local emergency override: environment
// PUBLIC_LOCKDOWN_OVERRIDE=closed, or the file named by
// PUBLIC_LOCKDOWN_OVERRIDE_FILE (default /etc/mielenosoitukset/lockdown-override)
// existing. It only matters while no successful read has happened yet.
var lockdownOverride = func() bool {
	if os.Getenv("PUBLIC_LOCKDOWN_OVERRIDE") == "closed" {
		return true
	}
	path := os.Getenv("PUBLIC_LOCKDOWN_OVERRIDE_FILE")
	if path == "" {
		path = "/etc/mielenosoitukset/lockdown-override"
	}
	_, err := os.Stat(path)
	return err == nil
}

// gateReadFailures counts failed status reads since start (logged, not exported).
var gateReadFailures atomic.Int64

// clock is replaceable in tests.
var clock = time.Now

// The gate is outside response caches, including Redis. One coalesced Mongo
// read per second per process bounds activation latency without a read per hit.
func publicAvailability(next http.Handler, read func(context.Context) (bool, error)) http.Handler {
	var mu sync.Mutex
	var checked, lastOpen, lastLog time.Time
	var locked, answered, everLocked bool
	var readErr error
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/health" || r.URL.Path == "/api/status" {
			next.ServeHTTP(w, r)
			return
		}
		mu.Lock()
		if clock().Sub(checked) >= time.Second {
			ctx, cancel := context.WithTimeout(r.Context(), time.Second)
			locked, readErr = read(ctx)
			cancel()
			checked = clock()
			switch {
			case readErr != nil:
				n := gateReadFailures.Add(1)
				if checked.Sub(lastLog) >= 30*time.Second {
					lastLog = checked
					log.Printf("public gate: lockdown status read failed (%d since start, answered=%t, locked=%t): %v", n, answered, everLocked, readErr)
				}
			case locked:
				answered, everLocked, lastOpen = true, true, time.Time{}
			default:
				answered, everLocked, lastOpen = true, false, checked
			}
		}
		unavailable := locked
		if readErr != nil {
			switch {
			case everLocked:
				unavailable = true
			case !answered:
				unavailable = lockdownOverride()
			default:
				unavailable = clock().Sub(lastOpen) > availabilityGrace
			}
		}
		mu.Unlock()
		if r.URL.Path == "/api/public-status" {
			w.Header().Set("Cache-Control", "no-store")
			writeJSON(w, http.StatusOK, map[string]bool{"available": !unavailable})
			return
		}
		if unavailable {
			w.Header().Set("Cache-Control", "no-store")
			w.Header().Set("Retry-After", "60")
			writeError(w, http.StatusServiceUnavailable, "service temporarily unavailable")
			return
		}
		next.ServeHTTP(w, r)
	})
}

func readPublicLockdown(db *mongo.Database) func(context.Context) (bool, error) {
	return func(ctx context.Context) (bool, error) {
		count, err := db.Collection("panic").CountDocuments(ctx, bson.M{
			"name": bson.M{"$in": bson.A{"global", "security"}}, "panic": true,
		})
		return count > 0, err
	}
}
