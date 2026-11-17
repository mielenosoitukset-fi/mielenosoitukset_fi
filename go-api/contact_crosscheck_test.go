package main

import (
	"context"
	"os"
	"os/exec"
	"strings"
	"testing"
	"time"
)

// Optional cross-check (CONTACT_TEST_MONGO and CONTACT_TEST_PYTHON): Python's
// limits library and Go's Hit share one counter document.
func TestContactCounterContinuesPythonLimitsCount(t *testing.T) {
	python := os.Getenv("CONTACT_TEST_PYTHON")
	if python == "" {
		t.Skip("CONTACT_TEST_PYTHON not set")
	}
	_, limits := contactTestMongo(t)
	store := mongoContactStore{db: limits, limiter: limits}
	script := `import sys
from limits.storage import storage_from_string
s = storage_from_string(sys.argv[1], database_name=sys.argv[2])
print(s.incr("LIMITER/1.2.3.4/contact/3/10/minute", 600), flush=True)
import os; os._exit(0)`
	run := func() string {
		ctx, stop := context.WithTimeout(context.Background(), 20*time.Second)
		defer stop()
		out, err := exec.CommandContext(ctx, python, "-I", "-c", script, os.Getenv("CONTACT_TEST_MONGO"), limits.Name()).CombinedOutput()
		if err != nil {
			t.Fatalf("%v: %s", err, out)
		}
		return strings.TrimSpace(string(out))
	}
	if got := run(); got != "1" {
		t.Fatalf("python first hit %s", got)
	}
	if got, err := store.Hit(context.Background(), "LIMITER/1.2.3.4/contact/3/10/minute", 10*time.Minute); err != nil || got != 2 {
		t.Fatalf("go continued at %d, %v", got, err)
	}
	if got := run(); got != "3" {
		t.Fatalf("python continued at %s", got)
	}
}
