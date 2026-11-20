package main

import (
	"net/http/httptest"
	"testing"
)

func TestRateLimitAddressGroupsIPv6ByPrefix(t *testing.T) {
	for input, expected := range map[string]string{
		"203.0.113.9":                   "203.0.113.9",
		"::ffff:203.0.113.9":            "203.0.113.9",
		"2001:db8:1:2:aaaa:bbbb:cccc:1": "2001:db8:1:2::/64",
		"2001:DB8:1:2::ffff":            "2001:db8:1:2::/64",
		"2001:db8:1:3::1":               "2001:db8:1:3::/64",
		"fe80::1%eth0":                  "fe80::/64",
		"":                              "",
		"not-an-address":                "not-an-address",
	} {
		if got := rateLimitAddress(input); got != expected {
			t.Errorf("%q: got %q, want %q", input, got, expected)
		}
	}
}

func TestSubmissionClientSharesKeyWithinIPv6Prefix(t *testing.T) {
	key := func(remote, forwarded string) string {
		request := httptest.NewRequest("POST", "/api/submissions", nil)
		request.RemoteAddr = remote
		if forwarded != "" {
			request.Header.Set("X-Forwarded-For", forwarded)
		}
		return submissionClient(request, "secret")
	}
	same := key("[::1]:1", "2001:db8:1:2::1")
	if key("[::1]:1", "2001:db8:1:2:ffff:ffff:ffff:ffff") != same {
		t.Fatal("addresses in one /64 must share a rate limit")
	}
	if key("[::1]:1", "2001:db8:1:3::1") == same {
		t.Fatal("another /64 is another visitor")
	}
	if key("203.0.113.9:1", "") == key("203.0.113.10:1", "") {
		t.Fatal("IPv4 addresses stay separate")
	}
	// The forwarded address itself is unchanged for audit records and Flask.
	request := httptest.NewRequest("POST", "/api/submissions", nil)
	request.RemoteAddr = "[::1]:1"
	request.Header.Set("X-Forwarded-For", "2001:db8:1:2::1")
	if submissionClientAddress(request) != "2001:db8:1:2::1" {
		t.Fatal(submissionClientAddress(request))
	}
}
