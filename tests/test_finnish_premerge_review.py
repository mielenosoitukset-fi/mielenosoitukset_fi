import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("finnish_premerge_review", ROOT / "scripts/finnish_premerge_review.py")
review = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = review
spec.loader.exec_module(review)

DIFF = """diff --git a/frontend/src/app/PrideScreen.tsx b/frontend/src/app/PrideScreen.tsx
--- a/frontend/src/app/PrideScreen.tsx
+++ b/frontend/src/app/PrideScreen.tsx
@@ -16,2 +16,2 @@ export const prideCopy = {
-  lead: 'Ilmoita oma tapahtumasi mukaan.',
-  empty: 'Meille ei vielä ole ilmoitettu mielenosoituksia.',
+  lead: 'Ilmoita myös oma tapahtumasi.',
+  empty: 'Meille ei ole vielä ilmoitettu mielenosoituksia.',
@@ -30,0 +31,1 @@
+  count: '{count} osallistujaa',
"""


def test_diff_pairs_replaced_lines_and_tracks_new_line_numbers():
    added = review.parse_diff(DIFF)["frontend/src/app/PrideScreen.tsx"]
    assert [line for line, _text, _removed in added] == [16, 17, 31]
    assert added[0][2] == ["  lead: 'Ilmoita oma tapahtumasi mukaan.',"]
    assert added[2][2] == []


def test_admin_and_email_sources_are_separated_from_user_ui():
    inventory = {"mielenosoitukset_fi/templates/manual_base.html"}
    assert review.classify("frontend/src/app/PrideScreen.tsx", inventory) == "ui"
    assert review.classify("frontend/src/app/CityAdminApplyScreen.tsx", inventory) == "ui"
    assert review.classify("go-api/city_admin_application.go", inventory) == "ui"
    assert review.classify("mielenosoitukset_fi/templates/admin_V2/user/edit.html", inventory) == "admin"
    assert review.classify("mielenosoitukset_fi/admin/admin_bp.py", inventory) == "admin"
    assert review.classify("frontend/src/app/GodScreen.tsx", inventory) == "admin"
    assert review.classify("mielenosoitukset_fi/templates/manual_base.html", inventory) == "admin"
    assert review.classify("mielenosoitukset_fi/templates/emails/auth/reset.html", inventory) == "email"
    assert review.classify("go-api/suggestion_email.go", inventory) == "email"
    assert review.classify("tests/test_listing.py", inventory) == "out of scope"
    assert review.classify("mielenosoitukset_fi/translations/sv/LC_MESSAGES/messages.po", inventory) == "out of scope"


def test_finnish_detection_ignores_code():
    assert review.looks_finnish("  empty: 'Kartalla ei ole vielä mielenosoituksia.',")
    assert review.looks_finnish("error: 'Yritä uudelleen ja tarkista tiedot'")
    assert not review.looks_finnish("import { useState } from 'react';")
    assert not review.looks_finnish("  const total = items.length;")


def test_count_placeholders_get_singular_and_plural_examples():
    names, examples = review.placeholder_examples("'{count} osallistujaa ${city}ssa'")
    assert names == ["count", "city"]
    assert examples == ["'1 osallistujaa Helsinkissa'", "'2 osallistujaa Helsinkissa'", "'5 osallistujaa Helsinkissa'"]


def change():
    return review.Change(path="frontend/src/app/PrideScreen.tsx", line=17, text="empty: 'Ei tapahtumia.'")


def test_unavailable_model_is_never_reported_as_passed():
    outcome, text = review.report([change()], {"admin": [], "email": []}, "UNAVAILABLE", "exit 1: model not found", [], False, "a" * 40)
    assert outcome == "NOT REVIEWED"
    assert "has not passed" in text
    assert "NO FINDINGS" not in text


def test_findings_are_listed_as_unresolved():
    finding = {"id": "F1", "current": "Ei tapahtumia.", "replacement": "Ei mielenosoituksia.", "kind": "proposal", "reason": "Site term"}
    outcome, text = review.report([change()], {"admin": [], "email": []}, "AVAILABLE", "codex exec", [finding], False, "a" * 40)
    assert outcome == "UNRESOLVED FINDINGS"
    assert "`frontend/src/app/PrideScreen.tsx:17`" in text
    assert "| unresolved |" in text


def test_model_answer_must_be_the_requested_json(tmp_path):
    good = tmp_path / "good.sh"
    good.write_text('cat >/dev/null; echo \'{"findings": []}\'\n')
    assert review.ask_model("prompt", ["sh", str(good)], 10)[0] == "AVAILABLE"
    prose = tmp_path / "prose.sh"
    prose.write_text("cat >/dev/null; echo 'Looks fine to me'\n")
    assert review.ask_model("prompt", ["sh", str(prose)], 10)[0] == "UNAVAILABLE"
    missing = tmp_path / "missing.sh"
    missing.write_text("cat >/dev/null; echo 'model gpt-6.1-sol not found' >&2; exit 1\n")
    availability, detail, _ = review.ask_model("prompt", ["sh", str(missing)], 10)
    assert availability == "UNAVAILABLE" and "not found" in detail
    assert review.ask_model("prompt", None, 10)[0] == "UNAVAILABLE"
