package main

// Read-only port of the national-team part of Flask's utils/system_teams
// (user_team_access, clearance_record_approved and the god-appointment consent
// checks). It only answers "does this account hold national-team access", for
// the public profile badge. It never writes and never returns role data.
//
// Everything fails closed: a missing or invalid NATIONAL_ADMIN_ORG_ID, a missing
// organisation, an unreadable record or any unrecognised shape means "no
// national access".

import (
	"context"
	"math"
	"os"
	"reflect"

	"go.mongodb.org/mongo-driver/v2/bson"
)

// nationalSource is the read access the lookup needs.
type nationalSource interface {
	OrgExists(ctx context.Context, org bson.ObjectID) (bool, error)
	// Memberships returns governance-approved rows of the user in the organisation.
	Memberships(ctx context.Context, user, org bson.ObjectID) ([]bson.M, error)
	// Clearance returns the board_clearances record of the user, or nil.
	Clearance(ctx context.Context, userID string) (bson.M, error)
}

// nationalOrgID reads NATIONAL_ADMIN_ORG_ID; invalid or empty means no national team.
func nationalOrgID() (bson.ObjectID, bool) {
	raw := os.Getenv("NATIONAL_ADMIN_ORG_ID")
	if len(raw) != 24 {
		return bson.ObjectID{}, false
	}
	id, err := bson.ObjectIDFromHex(raw)
	return id, err == nil
}

// teamAccountActive mirrors the User model and active_account: inactive, banned,
// unconfirmed or suspended accounts hold no team access.
func teamAccountActive(doc bson.M) bool {
	if active, present := doc["active"]; present && active != true {
		return false
	}
	if banned, present := doc["banned"]; present && banned != false {
		return false
	}
	if profileTruthy(profileKey(doc["security_suspension"], "active")) {
		return false
	}
	return doc["confirmed"] == true
}

// profileAdminBadge mirrors User.global_admin without ever reading the role:
// governance-managed accounts show the badge only through the national team,
// others through the stored flag or the national team.
func profileAdminBadge(doc bson.M, national bool) bool {
	if managed, _ := doc["governance_managed"].(bool); managed {
		return national
	}
	flag, _ := doc["global_admin"].(bool)
	return flag || national
}

// userHasNationalAccess mirrors user_team_access(...) contains "national".
func userHasNationalAccess(ctx context.Context, src nationalSource, doc bson.M, user bson.ObjectID) (bool, error) {
	org, ok := nationalOrgID()
	if !ok || !teamAccountActive(doc) {
		return false, nil
	}
	rows, err := src.Memberships(ctx, user, org)
	if err != nil || len(rows) == 0 {
		return false, err
	}
	exists, err := src.OrgExists(ctx, org)
	if err != nil || !exists {
		return false, err
	}
	for _, row := range rows {
		if row["grant_source"] == "maintainer_bootstrap" {
			return true, nil
		}
	}
	record, err := src.Clearance(ctx, user.Hex())
	if err != nil {
		return false, err
	}
	return clearanceRecordApproved(record), nil
}

func profileTruthy(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case int32:
		return v != 0
	case int64:
		return v != 0
	case float64:
		return v != 0
	}
	return true
}

func profileValidID(value any) bool {
	text, ok := value.(string)
	return ok && profileID(text) != ""
}

func profileKeys(value any) map[string]bool {
	out := map[string]bool{}
	switch doc := value.(type) {
	case bson.M:
		for k := range doc {
			out[k] = true
		}
	case map[string]any:
		for k := range doc {
			out[k] = true
		}
	case bson.D:
		for _, item := range doc {
			out[item.Key] = true
		}
	}
	return out
}

func profileIsDoc(value any) bool {
	switch value.(type) {
	case bson.M, map[string]any, bson.D:
		return true
	}
	return false
}

func profileStringSet(value any) map[string]bool {
	out := map[string]bool{}
	if list, ok := value.(bson.A); ok {
		for _, item := range list {
			if text, ok := item.(string); ok {
				out[text] = true
			}
		}
	}
	return out
}

// clearanceRecordApproved mirrors clearance_record_approved for a record read
// from board_clearances, including the god-appointment consent checks.
func clearanceRecordApproved(record bson.M) bool {
	if record == nil {
		return false
	}
	approved := record["approved"] == true
	requests := record["team_requests"]
	emergency := profileKey(requests, "god_emergency")
	if approved && profileKey(emergency, "approved") == true &&
		profileKey(emergency, "method") == "usb_and_god" &&
		reflect.DeepEqual(profileKey(emergency, "id"), record["god_appointment_emergency_id"]) &&
		reflect.DeepEqual(profileKey(emergency, "candidate_id"), record["user_id"]) &&
		profileValidID(profileKey(emergency, "god_user_id")) &&
		profileTruthy(profileKey(emergency, "usb_key_id")) && profileTruthy(profileKey(emergency, "god_proof_id")) &&
		profileTruthy(profileKey(emergency, "usb_proof")) {
		return true
	}
	appointment, national := profileKey(requests, "god"), profileKey(requests, "national")
	roster := profileKey(appointment, "roster")
	source := profileKey(national, "source")
	if approved && profileKey(national, "approved") == true &&
		profileTruthy(profileKey(appointment, "id")) &&
		reflect.DeepEqual(profileKey(appointment, "id"), profileKey(national, "id")) &&
		(source == "unanimous_god_appointment" || source == "god_appointment_consent") {
		voters := godRequiredVoters(roster)
		if userID, _ := record["user_id"].(string); !voters[userID] && godConsentApproved(appointment, roster) {
			return true
		}
	}
	// Two approvers other than the candidate.
	approvals := record["approvals"]
	if !approved || !profileIsDoc(approvals) {
		return false
	}
	candidate, _ := record["user_id"].(string)
	count := 0
	for voter := range profileKeys(approvals) {
		if voter != candidate {
			count++
		}
	}
	return count >= 2
}

// godRequiredVoters mirrors god_appointments.required_voters.
func godRequiredVoters(roster any) map[string]bool {
	out := profileStringSet(profileKey(roster, "board"))
	for id := range profileStringSet(profileKey(roster, "gods")) {
		out[id] = true
	}
	return out
}

// godConsentApproved mirrors god_appointments.consent_approved for the recorded
// roster (the roster compared against itself, as the caller passes it).
func godConsentApproved(state, roster any) bool {
	if len(profileStringSet(profileKey(roster, "board"))) < 2 || profileKey(state, "approved") != true ||
		profileKey(state, "window_ends_at") != nil {
		return false
	}
	votes := profileKeys(profileKey(state, "votes"))
	method, phase := profileKey(state, "method"), profileKey(state, "phase")
	if method == "unanimous" && phase == "collecting" {
		chamber, _ := profileKey(state, "chamber").(string)
		nominating := profileStringSet(profileKey(roster, chamber))
		if len(nominating) == 0 || profileTruthy(profileKey(state, "vetoes")) && len(profileKeys(profileKey(state, "vetoes"))) > 0 {
			return false
		}
		for id := range nominating {
			if !votes[id] {
				return false
			}
		}
		return true
	}
	if method == "supermajority" && phase == "second_round" {
		required := godRequiredVoters(roster)
		count := 0
		for id := range votes {
			if required[id] {
				count++
			}
		}
		need := int(math.Ceil(float64(len(required)) * 0.75))
		if need < 2 {
			need = 2
		}
		return count >= need
	}
	return false
}
