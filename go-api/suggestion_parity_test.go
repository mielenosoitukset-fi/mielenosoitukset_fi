package main

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

// The fixture is written by tests/test_go_suggestion_parity.py from the real
// Flask code: each case below replays Flask's input and expects Flask's output.
type parityFixture struct {
	Markdown []struct {
		Input, HTML string
	} `json:"markdown"`
	HTMLToMarkdown []struct {
		Input, Markdown string
	} `json:"html_to_markdown"`
	Tags []struct {
		Input  string
		Output []string
	} `json:"tags"`
	Route []struct {
		Input  string
		Output []string
	} `json:"route"`
	Organizers []struct {
		Input  string
		Output []map[string]string
	} `json:"organizers"`
	Clock []struct {
		A, B string
		Same bool
	} `json:"clock"`
}

func loadParityFixture(t *testing.T) parityFixture {
	t.Helper()
	data, err := os.ReadFile("testdata/suggestion_parity.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture parityFixture
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture
}

func TestParityMarkdown(t *testing.T) {
	failures := 0
	for _, example := range loadParityFixture(t).Markdown {
		if got := markdownToHTML(example.Input); got != example.HTML {
			failures++
			t.Errorf("markdown %q:\n got  %q\n want %q", example.Input, got, example.HTML)
		}
	}
	t.Logf("%d markdown mismatches", failures)
}

func TestParityHTMLToMarkdown(t *testing.T) {
	for _, example := range loadParityFixture(t).HTMLToMarkdown {
		if got := descriptionMarkdown(example.Input); got != example.Markdown {
			t.Errorf("html %q: got %q want %q", example.Input, got, example.Markdown)
		}
	}
}

func TestParityTagsRouteOrganizersClock(t *testing.T) {
	fixture := loadParityFixture(t)
	for _, example := range fixture.Tags {
		text := analyticsTrim(example.Input)
		got := []string{}
		if lower := lowerASCII(text); lower != "none" && lower != "null" {
			got = normalizeTagText(text)
		}
		if !reflect.DeepEqual(got, example.Output) {
			t.Errorf("tags %q: got %#v want %#v", example.Input, got, example.Output)
		}
	}
	for _, example := range fixture.Route {
		if got := normalizeRoutePoints(example.Input); !reflect.DeepEqual(got, example.Output) {
			t.Errorf("route %q: got %#v want %#v", example.Input, got, example.Output)
		}
	}
	for _, example := range fixture.Organizers {
		got := []map[string]string{}
		for _, entry := range normalizeOrganizerText(example.Input) {
			got = append(got, map[string]string{"name": entry.Name, "email": entry.Email, "website": entry.Website})
		}
		if len(got) == 0 && len(example.Output) == 0 {
			continue
		}
		if !reflect.DeepEqual(got, example.Output) {
			t.Errorf("organizers %q: got %#v want %#v", example.Input, got, example.Output)
		}
	}
	for _, example := range fixture.Clock {
		if got := sameClock(example.A, example.B); got != example.Same {
			t.Errorf("clock %q %q: got %v want %v", example.A, example.B, got, example.Same)
		}
	}
}
