package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestOpeningInterestHandlerGuardsBeforeTouchingTheDatabase(t *testing.T) {
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(299) })
	config := submissionConfig{Origin: "https://mielenosoitukset.fi", Secret: strings.Repeat("s", 32)}
	on := &reminderState{}
	on.available.Store(true)
	run := func(state *reminderState, method, path string, headers map[string]string) *httptest.ResponseRecorder {
		request := httptest.NewRequest(method, path, strings.NewReader("{}"))
		for key, value := range headers {
			request.Header.Set(key, value)
		}
		recorder := httptest.NewRecorder()
		openingInterestHandler(next, nil, config, state).ServeHTTP(recorder, request)
		return recorder
	}
	same := map[string]string{"Origin": "https://mielenosoitukset.fi"}
	checks := []struct {
		name   string
		code   int
		result *httptest.ResponseRecorder
	}{
		{"other paths pass through", 299, run(on, "POST", bannerPath, nil)},
		{"flag off: signup does not exist", 404, run(&reminderState{}, "POST", openingPath, same)},
		{"flag off: unsubscribe does not exist", 404, run(nil, "POST", openingUnsubscribePath, nil)},
		{"signup is POST only", 405, run(on, "GET", openingPath, same)},
		{"unsubscribe is POST only (scanners)", 405, run(on, "GET", openingUnsubscribePath, nil)},
		{"foreign origin", 403, run(on, "POST", openingPath, map[string]string{"Origin": "https://evil.example"})},
		{"missing origin on confirm", 403, run(on, "POST", openingConfirmPath, nil)},
		{"cross-site", 403, run(on, "POST", openingPath, map[string]string{"Origin": "https://mielenosoitukset.fi", "Sec-Fetch-Site": "cross-site"})},
	}
	for _, check := range checks {
		if check.result.Code != check.code {
			t.Fatalf("%s: status %d, want %d", check.name, check.result.Code, check.code)
		}
		if check.code != 299 && check.result.Header().Get("Cache-Control") != "no-store" {
			t.Fatalf("%s: response may be cached", check.name)
		}
	}
}

func TestOpeningInterestAnswersAreAllowlisted(t *testing.T) {
	for _, value := range []string{"", "yes", "maybe", "no"} {
		if !openingWilling[value] {
			t.Fatalf("willing %q rejected", value)
		}
	}
	for _, value := range []string{"yes ", "YES", "1", "<script>"} {
		if openingWilling[value] {
			t.Fatalf("willing %q accepted", value)
		}
	}
	for _, value := range []string{"", "under10", "10to20", "over20"} {
		if !openingBands[value] {
			t.Fatalf("band %q rejected", value)
		}
	}
	if openingBands["50"] || openingBands["over 20"] {
		t.Fatal("unknown band accepted")
	}
}
