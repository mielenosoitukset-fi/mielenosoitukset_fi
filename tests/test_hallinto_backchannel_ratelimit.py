"""Hallinto's server-side /hallinto-oidc/ calls from its configured egress IP skip the per-IP limit."""
from pathlib import Path


def test_backchannel_filter_present():
    app_src = Path(__file__).resolve().parents[1].joinpath("mielenosoitukset_fi", "app.py").read_text(encoding="utf-8")
    assert "_exempt_hallinto_backchannel" in app_src
    assert 'request.path.startswith("/hallinto-oidc/")' in app_src
    cfg = Path(__file__).resolve().parents[1].joinpath("config.py").read_text(encoding="utf-8")
    assert 'cls.HALLINTO_BACKCHANNEL_IPS = list(config.get("HALLINTO_BACKCHANNEL_IPS") or [])' in cfg
