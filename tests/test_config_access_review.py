"""ACCESS_REVIEW_* must be read from config.yaml, or the app never enforces the review."""
from pathlib import Path


def test_access_review_keys_loaded_from_config_yaml():
    src = Path(__file__).resolve().parents[1].joinpath("config.py").read_text(encoding="utf-8")
    assert 'cls.ACCESS_REVIEW_ENFORCE = config.get("ACCESS_REVIEW_ENFORCE", False)' in src
    assert 'cls.ACCESS_REVIEW_PROTECTED = config.get("ACCESS_REVIEW_PROTECTED", "")' in src
    assert 'cls.ACCESS_REVIEW_CONTACT_ENABLED = config.get("ACCESS_REVIEW_CONTACT_ENABLED", False)' in src
    assert 'cls.ACCESS_REVIEW_REPLY_TO = config.get("ACCESS_REVIEW_REPLY_TO"' in src
    assert 'cls.ACCESS_REVIEW_REPLY_BASE = config.get("ACCESS_REVIEW_REPLY_BASE"' in src


def test_escalation_ot_notices_key_loaded_from_config_yaml():
    src = Path(__file__).resolve().parents[1].joinpath("config.py").read_text(encoding="utf-8")
    assert 'cls.ESCALATION_OT_NOTICES_ENABLED = config.get("ESCALATION_OT_NOTICES_ENABLED", False)' in src
