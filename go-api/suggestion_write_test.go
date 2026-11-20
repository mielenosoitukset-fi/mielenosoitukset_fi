package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"regexp"
	"strings"
	"testing"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
)

type fakeSuggestionStore struct {
	orgName      any
	orgFound     bool
	demo         bson.D
	demoFound    bool
	orgDocs      []bson.D
	cases        []bson.D
	demoDocs     []bson.D
	mail         []bson.D
	nextCase     int64
	insertFailed bool
}

func (f *fakeSuggestionStore) FindOrganizationName(context.Context, bson.ObjectID) (any, bool, error) {
	return f.orgName, f.orgFound, nil
}
func (f *fakeSuggestionStore) FindDemonstration(context.Context, bson.ObjectID) (bson.D, bool, error) {
	return f.demo, f.demoFound, nil
}
func (f *fakeSuggestionStore) InsertOrgSuggestion(_ context.Context, d bson.D) error {
	f.orgDocs = append(f.orgDocs, d)
	return nil
}
func (f *fakeSuggestionStore) CreateCase(_ context.Context, build func(int64) bson.D) error {
	f.cases = append(f.cases, build(f.nextCase))
	return nil
}
func (f *fakeSuggestionStore) InsertDemoSuggestion(_ context.Context, d bson.D) error {
	if f.insertFailed {
		return context.DeadlineExceeded
	}
	f.demoDocs = append(f.demoDocs, d)
	return nil
}
func (f *fakeSuggestionStore) QueueMail(_ context.Context, d bson.D) error {
	f.mail = append(f.mail, d)
	return nil
}

// plain turns BSON values into the JSON shapes the fixture holds.
func plain(value any) any {
	switch typed := value.(type) {
	case bson.D:
		out := map[string]any{}
		for _, item := range typed {
			out[item.Key] = plain(item.Value)
		}
		return out
	case bson.A:
		out := []any{}
		for _, item := range typed {
			out = append(out, plain(item))
		}
		return out
	case bson.ObjectID:
		return map[string]any{"$oid": typed.Hex()}
	case int, int32, int64:
		return typed
	}
	return value
}

func jsonRoundTrip(t *testing.T, value any) any {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	var out any
	if err := json.Unmarshal(data, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

type scenarioFixture struct {
	DemoScenarios []struct {
		Name              string
		Demo              json.RawMessage
		Form              map[string]string
		Outcome           string
		Location          string
		LocationIsRequest bool           `json:"location_is_request_url"`
		SuggestedFields   map[string]any `json:"suggested_fields"`
		OriginalValues    map[string]any `json:"original_values"`
		ReporterComment   *string        `json:"reporter_comment"`
		ReporterEmail     *string        `json:"reporter_email"`
		StatusValue       string         `json:"status_value"`
		Keys              []string
		MailSubject       string `json:"mail_subject"`
		MailHTML          string `json:"mail_html"`
	} `json:"demo_scenarios"`
	OrgScenarios []struct {
		Name               string
		Org                map[string]any
		Form               [][2]string
		Location           string
		Fields             [][2]any
		MetaSubmitterEmail *string        `json:"meta_submitter_email"`
		SuggestionKeys     []string       `json:"suggestion_keys"`
		CaseKeys           []string       `json:"case_keys"`
		CaseType           string         `json:"case_type"`
		CaseMeta           map[string]any `json:"case_meta"`
		CaseSuggestionKeys []string       `json:"case_suggestion_keys"`
		MailSubject        string         `json:"mail_subject"`
		MailHTML           string         `json:"mail_html"`
	} `json:"org_scenarios"`
}

var whitespaceRun = regexp.MustCompile(`\s+`)

func normalizeMail(html string) string {
	html = whitespaceRun.ReplaceAllString(html, " ")
	return strings.ReplaceAll(strings.ReplaceAll(html, "> <", "><"), " <", "<")
}

const parityAgent = "Mozilla/5.0 (X11; Linux x86_64) <test> & \"quoted\" 'agent'"

func newTestWrites(store *fakeSuggestionStore) *suggestionWrites {
	return &suggestionWrites{store: store, origin: "https://mielenosoitukset.fi", instance: "test-instance",
		now: func() time.Time { return time.Date(2026, 11, 17, 10, 20, 30, 0, time.UTC) }}
}

func postForm(t *testing.T, writes *suggestionWrites, path string, values map[string][]string) *httptest.ResponseRecorder {
	t.Helper()
	request := httptest.NewRequest("POST", path, nil)
	request.Header.Set("User-Agent", parityAgent)
	request.RemoteAddr = "203.0.113.7:4000"
	request.PostForm = values
	recorder := httptest.NewRecorder()
	switch {
	case suggestionOrgPath.MatchString(path):
		writes.saveOrganization(context.Background(), recorder, request, suggestionOrgPath.FindStringSubmatch(path)[1], "203.0.113.7")
	default:
		writes.saveDemonstration(context.Background(), recorder, request, suggestionDemoPath.FindStringSubmatch(path)[1], "203.0.113.7")
	}
	return recorder
}

func TestParityDemoSuggestionScenarios(t *testing.T) {
	data, err := os.ReadFile("testdata/suggestion_parity.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture scenarioFixture
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	const demoID = "66fa58601102367d5d52887f"
	for _, scenario := range fixture.DemoScenarios {
		store := &fakeSuggestionStore{demoFound: true}
		if err := bson.UnmarshalExtJSON(scenario.Demo, false, &store.demo); err != nil {
			t.Fatal(err)
		}
		values := map[string][]string{}
		for key, value := range scenario.Form {
			values[key] = []string{value}
		}
		recorder := postForm(t, newTestWrites(store), "/suggest_change/"+demoID, values)
		if recorder.Code != 302 {
			t.Errorf("%s: status %d", scenario.Name, recorder.Code)
		}
		if scenario.Outcome == "rejected" {
			if len(store.demoDocs) != 0 || len(store.mail) != 0 || recorder.Header().Get("Location") != "https://mielenosoitukset.fi/suggest_change/"+demoID+"?suggestion=empty" {
				t.Errorf("%s: expected rejection, got %v %q", scenario.Name, store.demoDocs, recorder.Header().Get("Location"))
			}
			continue
		}
		if len(store.demoDocs) != 1 || len(store.mail) != 1 {
			t.Errorf("%s: stored %d, mails %d", scenario.Name, len(store.demoDocs), len(store.mail))
			continue
		}
		doc := store.demoDocs[0]
		if want := strings.ReplaceAll(scenario.Location, "{demo_id}", demoID) + "?suggestion=sent"; recorder.Header().Get("Location") != want {
			t.Errorf("%s: location %q want %q", scenario.Name, recorder.Header().Get("Location"), want)
		}
		get := func(key string) any { v, _ := documentValue(doc, key); return v }
		if got := jsonRoundTrip(t, plain(get("suggested_fields"))); !reflect.DeepEqual(got, jsonRoundTrip(t, scenario.SuggestedFields)) {
			t.Errorf("%s: suggested_fields\n got  %v\n want %v", scenario.Name, got, scenario.SuggestedFields)
		}
		if got := jsonRoundTrip(t, plain(get("original_values"))); !reflect.DeepEqual(got, jsonRoundTrip(t, scenario.OriginalValues)) {
			t.Errorf("%s: original_values\n got  %v\n want %v", scenario.Name, got, scenario.OriginalValues)
		}
		if !reflect.DeepEqual(get("reporter_comment"), ptrValue(scenario.ReporterComment)) || !reflect.DeepEqual(get("reporter_email"), ptrValue(scenario.ReporterEmail)) || get("status") != scenario.StatusValue || get("demo_id") != demoID {
			t.Errorf("%s: reporter/status/demo_id differ: %v", scenario.Name, doc)
		}
		keys := []string{}
		for _, item := range doc {
			keys = append(keys, item.Key)
		}
		sortStrings(keys)
		wantKeys := []string{}
		for _, key := range scenario.Keys {
			if key != "_id" { // assigned by the driver on insert
				wantKeys = append(wantKeys, key)
			}
		}
		if !reflect.DeepEqual(keys, wantKeys) {
			t.Errorf("%s: keys %v want %v", scenario.Name, keys, wantKeys)
		}
		mail := store.mail[0]
		subject, _ := documentValue(mail, "subject")
		html, _ := documentValue(mail, "html")
		body, _ := documentValue(mail, "body")
		if subject != scenario.MailSubject || body != html || normalizeMail(html.(string)) != normalizeMail(scenario.MailHTML) {
			t.Errorf("%s: mail differs\n got  %s\n want %s", scenario.Name, normalizeMail(html.(string)), normalizeMail(scenario.MailHTML))
		}
	}
}

func ptrValue(p *string) any {
	if p == nil {
		return nil
	}
	return *p
}

func TestParityOrganizationScenarios(t *testing.T) {
	data, _ := os.ReadFile("testdata/suggestion_parity.json")
	var fixture scenarioFixture
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	const orgID = "66fa58601102367d5d528880"
	for _, scenario := range fixture.OrgScenarios {
		store := &fakeSuggestionStore{orgFound: true, orgName: scenario.Org["name"], nextCase: 100_007}
		values := map[string][]string{}
		for _, pair := range scenario.Form {
			values[pair[0]] = append(values[pair[0]], pair[1])
		}
		recorder := postForm(t, newTestWrites(store), "/organization/"+orgID+"/save_suggestion", values)
		if recorder.Code != 302 || recorder.Header().Get("Location") != strings.ReplaceAll(scenario.Location, "{org}", orgID)+"?suggestion=saved" {
			t.Errorf("%s: %d %q", scenario.Name, recorder.Code, recorder.Header().Get("Location"))
		}
		if len(store.orgDocs) != 1 || len(store.cases) != 1 || len(store.mail) != 1 {
			t.Errorf("%s: writes %d/%d/%d", scenario.Name, len(store.orgDocs), len(store.cases), len(store.mail))
			continue
		}
		doc := store.orgDocs[0]
		keys := []string{}
		for _, item := range doc {
			keys = append(keys, item.Key)
		}
		sortStrings(keys)
		if !reflect.DeepEqual(keys, scenario.SuggestionKeys) {
			t.Errorf("%s: suggestion keys %v want %v", scenario.Name, keys, scenario.SuggestionKeys)
		}
		fields, _ := documentValue(doc, "fields")
		got := [][2]any{}
		for _, item := range fields.(bson.D) {
			got = append(got, [2]any{item.Key, plain(item.Value)})
		}
		if len(got) != len(scenario.Fields) {
			t.Errorf("%s: fields %v want %v", scenario.Name, got, scenario.Fields)
		} else {
			for index := range got {
				if got[index][0] != scenario.Fields[index][0] || !reflect.DeepEqual(jsonRoundTrip(t, got[index][1]), scenario.Fields[index][1]) {
					t.Errorf("%s: field %d %v want %v", scenario.Name, index, got[index], scenario.Fields[index])
				}
			}
		}
		meta, _ := documentValue(doc, "meta")
		if email, _ := documentValue(meta, "submitter_email"); !reflect.DeepEqual(email, ptrValue(scenario.MetaSubmitterEmail)) {
			t.Errorf("%s: submitter_email %v want %v", scenario.Name, email, scenario.MetaSubmitterEmail)
		}
		caseDoc := store.cases[0]
		caseKeys := []string{}
		for _, item := range caseDoc {
			caseKeys = append(caseKeys, item.Key)
		}
		sortStrings(caseKeys)
		suggestion, _ := documentValue(caseDoc, "suggestion")
		embeddedKeys := []string{}
		for _, item := range suggestion.(bson.D) {
			embeddedKeys = append(embeddedKeys, item.Key)
		}
		sortStrings(embeddedKeys)
		caseType, _ := documentValue(caseDoc, "type")
		caseMeta, _ := documentValue(caseDoc, "meta")
		running, _ := documentValue(caseDoc, "running_num")
		if !reflect.DeepEqual(caseKeys, scenario.CaseKeys) || !reflect.DeepEqual(embeddedKeys, scenario.CaseSuggestionKeys) || caseType != scenario.CaseType || !reflect.DeepEqual(jsonRoundTrip(t, plain(caseMeta)), jsonRoundTrip(t, scenario.CaseMeta)) || running != int64(100_007) {
			t.Errorf("%s: case differs: %v %v %v", scenario.Name, caseKeys, embeddedKeys, caseType)
		}
		mail := store.mail[0]
		subject, _ := documentValue(mail, "subject")
		html, _ := documentValue(mail, "html")
		wantHTML := strings.NewReplacer("ORG", orgID, "SUGGESTION", func() string { id, _ := documentValue(doc, "_id"); return id.(bson.ObjectID).Hex() }()).Replace(scenario.MailHTML)
		if subject != scenario.MailSubject || normalizeMail(html.(string)) != normalizeMail(wantHTML) {
			t.Errorf("%s: mail differs\n got  %s\n want %s", scenario.Name, normalizeMail(html.(string)), normalizeMail(wantHTML))
		}
	}
}

func TestSuggestionHandlerDisabledAndGuards(t *testing.T) {
	reached := false
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { reached = true })
	handler := suggestionHandler(next, nil, submissionConfig{Origin: "https://mielenosoitukset.fi", Secret: strings.Repeat("x", 32)}, false)
	handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("POST", "/suggest_change/66fa58601102367d5d52887f", nil))
	if !reached {
		t.Fatal("disabled routes must pass through")
	}
	reached = false
	handler = suggestionHandler(next, nil, submissionConfig{}, true)
	handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("POST", "/suggest_change/66fa58601102367d5d52887f", nil))
	if !reached {
		t.Fatal("unconfigured routes must pass through")
	}
}

func TestSuggestionMissingTargetsAndBadIDs(t *testing.T) {
	store := &fakeSuggestionStore{}
	writes := newTestWrites(store)
	if r := postForm(t, writes, "/suggest_change/66fa58601102367d5d52887f", map[string][]string{"title": {"x"}}); r.Code != 302 || r.Header().Get("Location") != "/" {
		t.Errorf("missing demo: %d %q", r.Code, r.Header().Get("Location"))
	}
	if r := postForm(t, writes, "/suggest_change/not-an-id", nil); r.Code != 302 || r.Header().Get("Location") != "/" {
		t.Errorf("bad demo id: %d", r.Code)
	}
	if r := postForm(t, writes, "/organization/66fa58601102367d5d528880/save_suggestion", nil); r.Code != 302 || r.Header().Get("Location") != "/" {
		t.Errorf("missing org: %d", r.Code)
	}
	if r := postForm(t, writes, "/organization/zzz/save_suggestion", nil); r.Code != 400 {
		t.Errorf("bad org id: %d", r.Code)
	}
	if len(store.orgDocs)+len(store.demoDocs)+len(store.mail) != 0 {
		t.Error("nothing may be written")
	}
}

func TestDemoSuggestionInsertFailureRedirectsBack(t *testing.T) {
	store := &fakeSuggestionStore{demoFound: true, insertFailed: true, demo: bson.D{{Key: "title", Value: "x"}}}
	r := postForm(t, newTestWrites(store), "/suggest_change/66fa58601102367d5d52887f", map[string][]string{"title": {"y"}})
	if r.Code != 302 || len(store.mail) != 0 {
		t.Errorf("got %d, %d mails", r.Code, len(store.mail))
	}
}

func TestOverLongDescriptionRedirectsBackWithNotice(t *testing.T) {
	store := &fakeSuggestionStore{demoFound: true, demo: bson.D{{Key: "title", Value: "x"}}}
	r := postForm(t, newTestWrites(store), "/suggest_change/66fa58601102367d5d52887f", map[string][]string{"description_markdown": {strings.Repeat("a", suggestionMaxMarkdown+1)}})
	if r.Code != 302 || r.Header().Get("Location") != "https://mielenosoitukset.fi/suggest_change/66fa58601102367d5d52887f?suggestion=too_long" || len(store.demoDocs) != 0 {
		t.Errorf("%d %q", r.Code, r.Header().Get("Location"))
	}
}

func TestInsertFailureRedirectsBackWithNotice(t *testing.T) {
	store := &fakeSuggestionStore{demoFound: true, insertFailed: true, demo: bson.D{{Key: "title", Value: "x"}}}
	r := postForm(t, newTestWrites(store), "/suggest_change/66fa58601102367d5d52887f", map[string][]string{"title": {"y"}})
	if !strings.HasSuffix(r.Header().Get("Location"), "?suggestion=failed") {
		t.Errorf("%q", r.Header().Get("Location"))
	}
}
