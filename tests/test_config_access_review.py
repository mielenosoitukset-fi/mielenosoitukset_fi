"""ACCESS_REVIEW_* must be read from config.yaml, or the app never enforces the review."""
from pathlib import Path


def test_access_review_keys_loaded_from_config_yaml():
    src = Path(__file__).resolve().parents[1].joinpath("config.py").read_text(encoding="utf-8")
    assert 'cls.ACCESS_REVIEW_ENFORCE = config.get("ACCESS_REVIEW_ENFORCE", False)' in src
    assert 'cls.ACCESS_REVIEW_PROTECTED = config.get("ACCESS_REVIEW_PROTECTED", "")' in src
    assert 'cls.ACCESS_REVIEW_CONTACT_ENABLED = config.get("ACCESS_REVIEW_CONTACT_ENABLED", False)' in src
