package main

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"

	"go.mongodb.org/mongo-driver/v2/bson"
)

// The fixture is written by tests/test_go_public_payload_parity.py from the
// Python twin the Flask admin preview uses (mielenosoitukset_fi/utils/public_payload.py).
// Each case feeds the same stored document to publicPayload and expects the
// same JSON, so the admin preview shows exactly what the public page shows.
func TestPublicPayloadMatchesAdminPreview(t *testing.T) {
	data, err := os.ReadFile("testdata/public_payload_parity.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Cases []struct {
			Lang     string          `json:"lang"`
			Document json.RawMessage `json:"document"`
			Payload  any             `json:"payload"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	if len(fixture.Cases) == 0 {
		t.Fatal("fixture has no cases")
	}
	for index, row := range fixture.Cases {
		var doc bson.M
		if err := bson.UnmarshalExtJSON(row.Document, true, &doc); err != nil {
			t.Fatalf("case %d: %v", index, err)
		}
		encoded, err := json.Marshal(publicPayload(doc, row.Lang))
		if err != nil {
			t.Fatalf("case %d: %v", index, err)
		}
		var got any
		if err := json.Unmarshal(encoded, &got); err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(got, row.Payload) {
			want, _ := json.Marshal(row.Payload)
			t.Errorf("case %d differs\n go:     %s\n python: %s", index, encoded, want)
		}
	}
}
