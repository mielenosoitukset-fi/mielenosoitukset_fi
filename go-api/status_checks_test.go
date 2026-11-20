package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
)

// AWS's published SigV4 example for a GET Object request
// (Amazon S3 API reference, "Signature calculations: GET Object").
func TestSignS3RequestMatchesAWSExample(t *testing.T) {
	request := httptest.NewRequest("GET", "https://examplebucket.s3.amazonaws.com/test.txt", nil)
	request.Header = http.Header{}
	request.Header.Set("Range", "bytes=0-9")
	signS3Request(request, s3StatusConfig{AccessKey: "AKIAIOSFODNN7EXAMPLE",
		SecretKey: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", Region: "us-east-1"},
		time.Date(2013, 5, 24, 0, 0, 0, 0, time.UTC))
	expected := "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, " +
		"SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, " +
		"Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
	if got := request.Header.Get("Authorization"); got != expected {
		t.Fatal(got)
	}
}

func TestStatusChecksAreOffByDefault(t *testing.T) {
	for _, name := range []string{"STATUS_CACHE_REDIS_URL", "STATUS_S3_ENDPOINT", "STATUS_S3_ACCESS_KEY", "STATUS_S3_SECRET_KEY", "STATUS_S3_REGION"} {
		t.Setenv(name, "")
	}
	checks, closeChecks := statusChecksFromEnv()
	defer closeChecks()
	if len(checks) != 0 {
		t.Fatal(checks)
	}
	// A partial or unsafe storage setting disables only that check.
	t.Setenv("STATUS_S3_ENDPOINT", "https://s3.example.test")
	if checks, _ := statusChecksFromEnv(); len(checks) != 0 {
		t.Fatal("partial storage config must stay off")
	}
	t.Setenv("STATUS_CACHE_REDIS_URL", "not a url")
	t.Setenv("STATUS_S3_ACCESS_KEY", "key")
	t.Setenv("STATUS_S3_SECRET_KEY", "secret")
	checks, closeChecks = statusChecksFromEnv()
	defer closeChecks()
	if len(checks) != 1 || checks[0].name != statusStorageName {
		t.Fatal(checks)
	}
}

func TestStorageStatusConfigValidation(t *testing.T) {
	valid := s3StatusConfig{"https://s3.example.test", "key", "secret", "us-east-1"}
	for _, endpoint := range []string{"http://s3.example.test", "https://user:pass@s3.example.test", "https://s3.example.test/?x=1", "s3.example.test", "ftp://127.0.0.1"} {
		config := valid
		config.Endpoint = endpoint
		if _, err := storageStatusCheck(config, nil); err == nil {
			t.Fatal(endpoint)
		}
	}
	for _, endpoint := range []string{"https://s3.example.test", "https://s3.example.test/base/", "http://127.0.0.1:9000", "http://localhost:9000"} {
		config := valid
		config.Endpoint = endpoint
		if _, err := storageStatusCheck(config, nil); err != nil {
			t.Fatal(endpoint, err)
		}
	}
}

func TestStorageStatusCheckListsBuckets(t *testing.T) {
	var status atomic.Int32
	status.Store(200)
	var seen atomic.Value
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seen.Store(r.Method + " " + r.URL.RequestURI() + " " + r.Header.Get("Authorization") + " " + r.Header.Get("X-Amz-Content-Sha256"))
		switch status.Load() {
		case 302:
			http.Redirect(w, r, "/elsewhere", http.StatusFound)
		case 0:
			<-r.Context().Done()
		default:
			w.WriteHeader(int(status.Load()))
			_, _ = w.Write([]byte("<ListAllMyBucketsResult/>"))
		}
	}))
	defer server.Close()
	check, err := storageStatusCheck(s3StatusConfig{server.URL, "AKIDEXAMPLE", "secret", "eu-north-1"}, server.Client())
	if err != nil {
		t.Fatal(err)
	}
	probe := func() error {
		ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
		defer cancel()
		return check.probe(ctx)
	}
	if err := probe(); err != nil {
		t.Fatal(err)
	}
	request := seen.Load().(string)
	if !strings.HasPrefix(request, "GET / AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/") ||
		!strings.Contains(request, "/eu-north-1/s3/aws4_request, SignedHeaders=host;x-amz-content-sha256;x-amz-date, Signature=") ||
		!strings.HasSuffix(request, emptyPayloadSHA256) {
		t.Fatal(request)
	}
	for _, code := range []int32{403, 500, 302, 0} {
		status.Store(code)
		if err := probe(); err == nil {
			t.Fatal(code)
		}
	}
}

func TestCacheStatusCheckWritesShortLivedProbeKey(t *testing.T) {
	server := miniredis.RunT(t)
	server.Set("flask_cache__status_ping", "kept")
	check, closeClient, err := cacheStatusCheck("redis://" + server.Addr() + "/3")
	if err != nil {
		t.Fatal(err)
	}
	defer closeClient()
	if err := check.probe(context.Background()); err != nil {
		t.Fatal(err)
	}
	server.Select(3)
	if !server.Exists(statusCacheKey) || server.TTL(statusCacheKey) != 5*time.Second {
		t.Fatal(server.Keys(), server.TTL(statusCacheKey))
	}
	server.Select(0)
	if value, _ := server.Get("flask_cache__status_ping"); value != "kept" {
		t.Fatal("Flask cache entries must stay untouched")
	}
	server.Close()
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err := check.probe(ctx); err == nil {
		t.Fatal("closed Redis must fail")
	}
	if _, _, err := cacheStatusCheck("http://not-redis"); err == nil {
		t.Fatal("invalid URL must be refused")
	}
}

func TestServiceStatusReportsExtraChecksInParallel(t *testing.T) {
	slow := func(ctx context.Context) error { <-ctx.Done(); return errors.New("private-host secret") }
	handler := serviceStatusHandler(func(context.Context) error { return nil },
		statusCheck{statusCacheName, "Yhteys kunnossa", slow},
		statusCheck{statusStorageName, "Saavutettavissa", slow})
	started := time.Now()
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest("GET", "/api/status", nil))
	if time.Since(started) > 1500*time.Millisecond {
		t.Fatal("probes must run together", time.Since(started))
	}
	var result serviceStatus
	if err := json.Unmarshal(recorder.Body.Bytes(), &result); err != nil || strings.Contains(recorder.Body.String(), "private-host") {
		t.Fatal(err, recorder.Body.String())
	}
	expected := []statusService{{"Tietokanta", "ok", "Yhteys kunnossa"}, {statusCacheName, "err", "Ei vastaa"}, {statusStorageName, "err", "Ei vastaa"}}
	if result.OverallOK || len(result.Services) != 3 {
		t.Fatal(result)
	}
	for index := range expected {
		if result.Services[index] != expected[index] {
			t.Fatal(result.Services)
		}
	}

	healthy := serviceStatusHandler(func(context.Context) error { return nil },
		statusCheck{statusCacheName, "Yhteys kunnossa", func(context.Context) error { return nil }},
		statusCheck{statusStorageName, "Saavutettavissa", func(context.Context) error { return nil }})
	recorder = httptest.NewRecorder()
	healthy.ServeHTTP(recorder, httptest.NewRequest("GET", "/api/status", nil))
	if err := json.Unmarshal(recorder.Body.Bytes(), &result); err != nil || !result.OverallOK || result.Services[2].Message != "Saavutettavissa" {
		t.Fatal(err, result)
	}
}
