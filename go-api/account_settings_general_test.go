package main

import (
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"

	"go.mongodb.org/mongo-driver/v2/bson"
)

func settingsRig(t *testing.T, settings map[string]any) accountWriteRig {
	t.Helper()
	g := newAccountWriteRig(t, true)
	g.store.state = &accountSettingsState{Email: "alice@example.test", Username: "alice", Displayname: "Alice Tester", Settings: settings}
	return g
}

func (g accountWriteRig) postSettings(body string, headers ...string) (int, map[string]any) {
	w := g.postTo(accountSettingsPath, body, headers...)
	var answer map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &answer)
	return w.Code, answer
}

var fullSettings = map[string]any{"city": "Helsinki", "display_name": "Alice", "language": "fi", "dark_mode": true}

func copySettings(extra map[string]any) map[string]any {
	out := map[string]any{}
	for k, v := range fullSettings {
		out[k] = v
	}
	for k, v := range extra {
		out[k] = v
	}
	return out
}

func TestSettingsWriteChangesOnlyChangedFieldsAndQueuesMail(t *testing.T) {
	g := settingsRig(t, copySettings(nil))
	code, answer := g.postSettings(`{"city":"Espoo","display_name":"  Uusi  ","language":"fi","dark_mode":true}`)
	if code != 200 || answer["message"] != "Asetukset päivitetty ja ilmoitus lähetetty." {
		t.Fatal(code, answer)
	}
	if len(g.store.saved) != 1 || len(g.store.saved[0]) != 2 || g.store.saved[0]["city"] != "Espoo" || g.store.saved[0]["display_name"] != "Uusi" {
		t.Fatalf("saved %v", g.store.saved)
	}
	if g.store.names[0] == nil || *g.store.names[0] != "Uusi" || len(g.store.inserted[0]) != 0 {
		t.Fatal("display name must be copied to the user; no defaults for an existing document")
	}
	fields := answer["changed_fields"].(map[string]any)
	if len(fields) != 2 || fields["city"].(map[string]any)["old"] != "Helsinki" {
		t.Fatal(fields)
	}
	if len(g.store.mails) != 1 {
		t.Fatal("one mail expected")
	}
	mail := map[string]any{}
	for _, item := range g.store.mails[0] {
		mail[item.Key] = item.Value
	}
	recipients, _ := mail["recipients"].(bson.A)
	if mail["subject"] != "Käyttäjäasetusten muutokset" || len(recipients) != 1 || recipients[0] != "alice@example.test" ||
		mail["status"] != "pending" || mail["attempts"] != 0 || mail["sender"] != nil || mail["body"] != mail["html"] ||
		mail["instance_id"] != g.writes.instance || !strings.Contains(mail["html"].(string), "<strong>city</strong>") {
		t.Fatalf("queued mail %v", mail)
	}
	if len(g.store.audits) != 1 || g.store.audits[0]["action"] != "settings updated" || g.store.audits[0]["source"] != "go-api" {
		t.Fatal(g.store.audits)
	}
	for k, v := range g.store.audits[0] {
		if s, ok := v.(string); ok && (strings.Contains(s, "Espoo") || strings.Contains(s, "Uusi")) {
			t.Errorf("audit field %s carries a value", k)
		}
	}
}

func TestSettingsWriteNothingChangedWritesNoMail(t *testing.T) {
	g := settingsRig(t, copySettings(nil))
	code, answer := g.postSettings(`{"city":"Helsinki","display_name":"Alice","language":"fi","dark_mode":true}`)
	if code != 200 || answer["message"] != "Ei muutoksia asetuksissa." || len(answer["changed_fields"].(map[string]any)) != 0 {
		t.Fatal(code, answer)
	}
	if code, _ := g.postSettings(`{}`); code != 200 {
		t.Fatal(code)
	}
	if len(g.store.saved)+len(g.store.mails)+len(g.store.audits) != 0 {
		t.Fatal("no write, mail or audit expected")
	}
}

func TestSettingsWriteMissingDocumentUsesDefaultsAndUpserts(t *testing.T) {
	g := settingsRig(t, nil)
	code, answer := g.postSettings(`{"display_name":"Uusi","language":"fi","dark_mode":false}`)
	if code != 200 {
		t.Fatal(code, answer)
	}
	if got := g.store.inserted[0]; got["city"] != nil || got["subscribe_newsletter"] != nil || len(got) != 2 {
		t.Fatalf("defaults %v", got)
	}
	old := answer["changed_fields"].(map[string]any)["display_name"].(map[string]any)["old"]
	if old != "Alice Tester" {
		t.Fatal("default display name is the account's", old)
	}
}

func TestSettingsWriteRefusesBadInputAndWritesNothing(t *testing.T) {
	bad := []string{`nope`, ``, `[]`, `null`, `{"bio":"x"}`, `{"subscribe_newsletter":true}`, `{"display_name":null}`, `{"display_name":5}`,
		`{"display_name":"   "}`, `{"display_name":"` + strings.Repeat("a", 41) + `"}`, `{"display_name":"a\u0000b"}`,
		`{"city":"Atlantis"}`, `{"city":5}`, `{"city":"` + strings.Repeat("a", 101) + `"}`, `{"language":"sv"}`, `{"language":null}`,
		`{"dark_mode":"true"}`, `{"dark_mode":1}`, `{"dark_mode":null}`}
	// A fresh rig per request keeps the rate limit out of the way.
	for _, body := range bad {
		if code, _ := settingsRig(t, copySettings(nil)).postSettings(body); code != 400 {
			t.Errorf("%.40q: got %d", body, code)
		}
	}
	for _, name := range []string{"Admin", " @Administrator ", "ADMIN", "@ admin"} {
		code, answer := settingsRig(t, copySettings(nil)).postSettings(`{"display_name":"` + name + `"}`)
		if code != 400 || answer["error"] != "Admin-nimitys on varattu palvelun sisäiseen käyttöön." {
			t.Errorf("%q: %d %v", name, code, answer)
		}
	}
	g := settingsRig(t, copySettings(nil))
	if code, _ := g.postSettings(`{"display_name":"` + strings.Repeat("ä", 40) + `"}`); code != 200 {
		t.Fatal("exactly 40 characters must pass")
	}
	if code, _ := g.postSettings(`{"city":""}`); code != 200 {
		t.Fatal("an empty city clears the choice")
	}
}

func TestSettingsWriteGuardsAndFailures(t *testing.T) {
	g := settingsRig(t, copySettings(nil))
	for _, h := range [][]string{{"Sec-Fetch-Site", "cross-site"}, {developerCSRFHeader, "-"}, {"Origin", "https://evil.test"}, {"Content-Type", "text/plain"}} {
		if code, _ := g.postSettings(`{"city":"Espoo"}`, h...); code != 403 && code != 415 {
			t.Errorf("%v: %d", h, code)
		}
	}
	if len(g.store.saved) != 0 {
		t.Fatal("rejected requests must not write")
	}
	g.store.err = errors.New("db down")
	if code, _ := g.postSettings(`{"city":"Espoo"}`); code != 503 {
		t.Fatal(code)
	}
	g.store.err, g.store.state = nil, nil
	if code, _ := g.postSettings(`{"city":"Espoo"}`); code != 404 {
		t.Fatal(code)
	}
}

func TestSettingsWriteMailProblemsDoNotFailTheWrite(t *testing.T) {
	g := settingsRig(t, copySettings(nil))
	g.store.mailError = errors.New("queue down")
	if code, _ := g.postSettings(`{"city":"Espoo"}`); code != 200 || len(g.store.saved) != 1 {
		t.Fatal("a failed mail insert must only be logged", code)
	}
	g = settingsRig(t, copySettings(nil))
	g.store.state.Email = ""
	if code, _ := g.postSettings(`{"city":"Espoo"}`); code != 200 || len(g.store.saved) != 1 || len(g.store.mails) != 0 {
		t.Fatal("an account without an email still saves, with no mail", code)
	}
}

func TestSettingsWriteIsRateLimitedAndOffWhenFlagOff(t *testing.T) {
	g := settingsRig(t, copySettings(nil))
	var last int
	for i := 0; i < developerWritesPerMin+1; i++ {
		last, _ = g.postSettings(`{"city":"Espoo"}`)
	}
	if last != 429 {
		t.Fatal(last)
	}
	off := newAccountWriteRig(t, false)
	if w := off.postTo(accountSettingsPath, `{"city":"Espoo"}`); w.Code != 405 {
		t.Fatal("POST without the write flag keeps the read route's 405", w.Code)
	}
}

// The fixture is Flask's own answer and mail for each case.
type settingsParityCase struct {
	Name          string
	Email         string
	Displayname   string
	Settings      map[string]any
	Body          map[string]any
	Status        int
	Message       string
	ChangedFields map[string]map[string]any `json:"changed_fields"`
	Mail          *struct{ Subject, HTML string }
	SettingsAfter map[string]any `json:"settings_after"`
	NameAfter     *string        `json:"displayname_after"`
}

func TestSettingsParityWithFlask(t *testing.T) {
	data, err := os.ReadFile("testdata/settings_parity.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct{ Cases []settingsParityCase }
	if err := json.Unmarshal(data, &fixture); err != nil || len(fixture.Cases) < 10 {
		t.Fatal(err, len(fixture.Cases))
	}
	for _, c := range fixture.Cases {
		g := newAccountWriteRig(t, true)
		var settings map[string]any
		if c.Settings != nil {
			settings = map[string]any{}
			for _, field := range settingsFieldOrder {
				settings[field] = c.Settings[field]
			}
		}
		g.store.state = &accountSettingsState{Email: c.Email, Username: "alice", Displayname: c.Displayname, Settings: settings}
		body, _ := json.Marshal(c.Body)
		code, answer := g.postSettings(string(body))
		if code != c.Status {
			t.Errorf("%s: status %d, Flask %d", c.Name, code, c.Status)
			continue
		}
		message := answer["message"]
		if c.Status != 200 {
			message = answer["error"]
		}
		if message != c.Message {
			t.Errorf("%s: message %v, Flask %q", c.Name, message, c.Message)
		}
		if c.Status != 200 {
			if len(g.store.saved) != 0 {
				t.Errorf("%s: refused request wrote", c.Name)
			}
			continue
		}
		gotJSON, _ := json.Marshal(answer["changed_fields"])
		wantJSON, _ := json.Marshal(c.ChangedFields)
		if string(gotJSON) != string(wantJSON) {
			t.Errorf("%s: changed_fields %s, Flask %s", c.Name, gotJSON, wantJSON)
		}
		if c.Mail == nil {
			if len(g.store.saved) != 0 || len(g.store.mails) != 0 {
				t.Errorf("%s: Flask changed nothing", c.Name)
			}
			continue
		}
		if len(g.store.mails) != 1 {
			t.Errorf("%s: %d mails", c.Name, len(g.store.mails))
			continue
		}
		mail := map[string]any{}
		for _, item := range g.store.mails[0] {
			mail[item.Key] = item.Value
		}
		if mail["subject"] != c.Mail.Subject || mail["html"] != c.Mail.HTML {
			t.Errorf("%s: mail differs from Flask's render:\n got  %q\n want %q", c.Name, mail["html"], c.Mail.HTML)
		}
		// What Flask ends up with: defaults for a new document plus the changes.
		after := map[string]any{}
		for k, v := range g.store.inserted[0] {
			after[k] = v
		}
		for k, v := range g.store.saved[0] {
			after[k] = v
		}
		if c.Settings != nil {
			for k, v := range c.Settings {
				if _, ok := after[k]; !ok {
					after[k] = v
				}
			}
		}
		for k, want := range c.SettingsAfter {
			if k != "subscribe_newsletter" || c.Settings != nil {
				if got, ok := after[k]; !ok && want != nil || ok && !jsonEqual(got, want) {
					t.Errorf("%s: stored %s = %v, Flask %v", c.Name, k, got, want)
				}
			}
		}
		if c.NameAfter != nil {
			if want, ok := c.SettingsAfter["display_name"]; ok && want == *c.NameAfter && (g.store.names[0] == nil || *g.store.names[0] != *c.NameAfter) {
				t.Errorf("%s: users.displayname not copied", c.Name)
			}
		}
	}
}

func jsonEqual(a, b any) bool {
	x, _ := json.Marshal(a)
	y, _ := json.Marshal(b)
	return string(x) == string(y)
}
