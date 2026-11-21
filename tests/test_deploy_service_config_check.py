"""The pre-reload check that the Flask service user can read its config files."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "deploy/check_service_config_files.py"


def _tools(tmp_path, environment, user="www-data", denied=()):
    """Fake systemctl and runuser: runuser refuses reads of the ``denied`` paths."""
    systemctl = tmp_path / "systemctl"
    systemctl.write_text(
        "#!/usr/bin/env bash\n"
        f"printf 'User=%s\\n' {user!r}\n"
        f"printf 'Environment=%s\\n' {environment!r}\n")
    runuser = tmp_path / "runuser"
    denied_cases = "".join(f"  {path!r}) exit 1 ;;\n" for path in denied)
    runuser.write_text(
        "#!/usr/bin/env bash\n"
        "[[ $1 == -u && $3 == -- ]] || exit 99\n"
        "printf '%s\\n' \"$2\" >> \"$(dirname \"$0\")/runuser.log\"\n"
        "shift 3\n"
        f"case ${{3}} in\n{denied_cases}esac\n"
        "exec \"$@\"\n")
    for tool in (systemctl, runuser):
        tool.chmod(0o755)
    return ["--systemctl", str(systemctl), "--runuser", str(runuser)]


def _run(tmp_path, args):
    return subprocess.run([sys.executable, str(CHECK), "mielenosoitukset_fi", *args],
                          capture_output=True, text=True, timeout=30)


def test_readable_config_files_pass_as_the_service_user(tmp_path):
    grant = tmp_path / "grant.json"
    grant.write_text("{}")
    args = _tools(tmp_path, f"PATH=/usr/bin HALLINTO_CITY_GRANT_CONFIG_FILE={grant} OTHER=1")
    result = _run(tmp_path, args)
    assert result.returncode == 0, result.stderr
    assert "readable by www-data: 1" in result.stdout
    assert (tmp_path / "runuser.log").read_text().split() == ["www-data"]


def test_unreadable_config_file_fails_and_names_variable_without_contents(tmp_path):
    grant = tmp_path / "grant.json"
    grant.write_text('{"secret": "synthetic-secret-value"}')
    issuer = tmp_path / "issuer.json"
    issuer.write_text("{}")
    args = _tools(tmp_path, f"HALLINTO_CITY_GRANT_CONFIG_FILE={grant} HALLINTO_ISSUER_CONFIG_FILE={issuer}",
                  denied=(str(grant),))
    result = _run(tmp_path, args)
    assert result.returncode == 1
    assert f"HALLINTO_CITY_GRANT_CONFIG_FILE={grant}" in result.stderr
    assert "HALLINTO_ISSUER_CONFIG_FILE" not in result.stderr
    assert "synthetic-secret-value" not in result.stderr + result.stdout


def test_missing_config_file_fails(tmp_path):
    args = _tools(tmp_path, f"HALLINTO_CITY_GRANT_CONFIG_FILE={tmp_path / 'missing.json'}")
    result = _run(tmp_path, args)
    assert result.returncode == 1
    assert "HALLINTO_CITY_GRANT_CONFIG_FILE" in result.stderr


def test_quoted_environment_values_are_parsed(tmp_path):
    grant = tmp_path / "grant dir" / "config.json"
    grant.parent.mkdir()
    grant.write_text("{}")
    args = _tools(tmp_path, f"\"HALLINTO_CITY_GRANT_CONFIG_FILE={grant}\" MESSAGE=\"a b\"")
    result = _run(tmp_path, args)
    assert result.returncode == 0, result.stderr


def test_unit_without_config_files_passes(tmp_path):
    args = _tools(tmp_path, "PATH=/usr/bin")
    result = _run(tmp_path, args)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "runuser.log").exists()


def test_unreadable_unit_fails_closed(tmp_path):
    result = _run(tmp_path, ["--systemctl", str(tmp_path / "missing-systemctl")])
    assert result.returncode == 1
    assert "cannot read the mielenosoitukset_fi unit" in result.stderr
