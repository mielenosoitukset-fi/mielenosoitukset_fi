"""The emergency grant script refuses unless used deliberately, and leaves no root copies."""
import importlib.util
from argparse import Namespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("emergency_grant", ROOT / "scripts/emergency/grant_rights.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _args(**over):
    base = dict(email="a@example.test", level="admin", actor="Olivia", reason="No other admin can log in",
                confirm_email="A@example.test")
    base.update(over)
    return Namespace(**base)


def test_old_root_scripts_are_gone():
    assert not (ROOT / "grant_admin_rights.py").exists()
    assert not (ROOT / "grant_all_rights.py").exists()


def test_guard_accepts_complete_use():
    _load().check_guard(_args(), {"EMERGENCY_GRANT_ALLOWED": "1"})


@pytest.mark.parametrize("env,over", [
    ({}, {}),
    ({"EMERGENCY_GRANT_ALLOWED": "1"}, {"reason": "short"}),
    ({"EMERGENCY_GRANT_ALLOWED": "1"}, {"actor": ""}),
    ({"EMERGENCY_GRANT_ALLOWED": "1"}, {"confirm_email": "b@example.test"}),
    ({"EMERGENCY_GRANT_ALLOWED": "1"}, {"level": "superuser"}),
])
def test_guard_refuses(env, over):
    module = _load()
    with pytest.raises(module.GuardError):
        module.check_guard(_args(**over), env)


def test_levels_map_to_rights():
    module = _load()
    assert module.update_for("admin") == {"role": "admin"}
    assert module.update_for("global_admin") == {"role": "global_admin", "global_admin": True}
    assert "global_permissions" in module.update_for("all")
