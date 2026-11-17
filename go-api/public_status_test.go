package main

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestPublicGatePrecedesCachedResponsesAndPreservesHealth(t *testing.T) {
	lockdownOverride = func() bool { return true }
	defer func() { lockdownOverride = func() bool { return false } }()
	for _, failed := range []bool{false, true} {
		calls, served := 0, 0
		handler := publicAvailability(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			served++
			w.WriteHeader(200)
		}), func(context.Context) (bool, error) {
			calls++
			if failed {
				return false, errors.New("database unavailable")
			}
			return true, nil
		})
		for _, path := range []string{"/api/home", "/api/demonstrations", "/api/submit"} {
			w := httptest.NewRecorder()
			handler.ServeHTTP(w, httptest.NewRequest("GET", path, nil))
			if w.Code != 503 || w.Header().Get("Cache-Control") != "no-store" {
				t.Fatal(path, w.Code)
			}
		}
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, httptest.NewRequest("GET", "/health", nil))
		if served != 1 || calls != 1 || w.Code != 200 {
			t.Fatal(served, calls, w.Code)
		}
		w = httptest.NewRecorder()
		handler.ServeHTTP(w, httptest.NewRequest("GET", "/api/public-status", nil))
		if w.Code != 200 || w.Body.String() != "{\"available\":false}\n" {
			t.Fatal(w.Code, w.Body.String())
		}
	}
}

func gateHarness(t *testing.T) (status func() int, set func(locked bool, err error), advance func(time.Duration)) {
	now := time.Date(2026, 11, 17, 12, 0, 0, 0, time.UTC)
	clock = func() time.Time { return now }
	lockdownOverride = func() bool { return false }
	t.Cleanup(func() { clock = time.Now })
	var locked bool
	var err error
	handler := publicAvailability(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(200) }),
		func(context.Context) (bool, error) { return locked, err })
	status = func() int {
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, httptest.NewRequest("GET", "/api/home", nil))
		return w.Code
	}
	set = func(l bool, e error) { locked, err = l, e }
	advance = func(d time.Duration) { now = now.Add(d) }
	return
}

var errDown = errors.New("database unavailable")

func TestGateStaysOpenWhenReadFailsAfterOpen(t *testing.T) {
	status, set, advance := gateHarness(t)
	set(false, nil)
	if status() != 200 {
		t.Fatal("open read opens")
	}
	set(false, errDown)
	advance(2 * time.Second)
	if status() != 200 {
		t.Fatal("failed read keeps the open answer")
	}
	advance(availabilityGrace - 10*time.Second)
	if status() != 200 {
		t.Fatal("still open inside the window")
	}
	advance(time.Minute)
	if status() != 503 {
		t.Fatal("a failure outlasting the window closes")
	}
	set(false, nil)
	advance(2 * time.Second)
	if status() != 200 {
		t.Fatal("recovery reopens")
	}
}

func TestGateStaysLockedWhenReadFailsAfterLock(t *testing.T) {
	status, set, advance := gateHarness(t)
	set(false, nil)
	status()
	set(true, nil)
	advance(2 * time.Second)
	if status() != 503 {
		t.Fatal("lockdown applies at once")
	}
	set(false, errDown)
	for i := 0; i < 3; i++ {
		advance(availabilityGrace)
		if status() != 503 {
			t.Fatal("a failed read must not reopen a locked site")
		}
	}
	set(false, nil)
	advance(2 * time.Second)
	if status() != 200 {
		t.Fatal("an explicit open read reopens")
	}
}

func TestGateRestartDuringOutage(t *testing.T) {
	status, set, advance := gateHarness(t)
	set(false, errDown)
	if status() != 200 {
		t.Fatal("no answer yet and no override: stay open")
	}
	advance(time.Hour)
	if status() != 200 {
		t.Fatal("still open: nothing ever said locked")
	}
	lockdownOverride = func() bool { return true }
	advance(2 * time.Second)
	if status() != 503 {
		t.Fatal("emergency override closes")
	}
	set(false, nil)
	advance(2 * time.Second)
	if status() != 200 {
		t.Fatal("a successful open read wins over the override")
	}
}

func TestOverrideClosesAfterOpenAnswerWhenReadFails(t *testing.T) {
	status, set, advance := gateHarness(t)
	set(false, nil)
	if status() != 200 {
		t.Fatal("open")
	}
	set(false, errDown)
	advance(2 * time.Second)
	lockdownOverride = func() bool { return true }
	if status() != 503 {
		t.Fatal("override must close within the stale window")
	}
}
