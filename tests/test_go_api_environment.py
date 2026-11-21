"""The opt-in Go writer must accept the same content languages as Flask."""
import importlib.util
import json
from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize("babel, expected", [
    ({}, ["fi", "en"]),
    ({"SUPPORTED_LOCALES": ["fi", "en", "sv"]}, ["fi", "en", "sv"]),
    ({"SUPPORTED_LOCALES": ["fi", "sv"]}, ["fi", "sv"]),
    ({"SUPPORTED_LOCALES": []}, ["fi"]),
])
def test_submission_locales_follow_nested_flask_configuration(tmp_path, babel, expected):
    path = Path(__file__).resolve().parents[1] / "deploy/go-api/configure_environment.py"
    spec = importlib.util.spec_from_file_location("go_environment", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source, target = tmp_path / "config.yaml", tmp_path / "environment"
    source.write_text(yaml.safe_dump({
        "MONGO_URI": "mongodb://127.0.0.1/isolated", "MONGO_DBNAME": "isolated",
        "SECRET_KEY": "test-secret-not-for-production-" * 2, "GO_SUBMIT_ENABLED": True, "BABEL": babel,
    }))
    module.configure(source, target)
    values = dict(line.split("=", 1) for line in target.read_text().splitlines())
    assert json.loads(values["PUBLIC_SUBMIT_LOCALES"]).split(",") == expected


@pytest.mark.parametrize("flag, expected", [(True, "true"), (None, "true"), (False, None)])
def test_event_analytics_switch_follows_the_flask_default_and_yaml_flag(tmp_path, flag, expected):
    path = Path(__file__).resolve().parents[1] / "deploy/go-api/configure_environment.py"
    spec = importlib.util.spec_from_file_location("go_environment", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source, target = tmp_path / "config.yaml", tmp_path / "environment"
    config = {"MONGO_URI": "mongodb://127.0.0.1/isolated", "MONGO_DBNAME": "isolated"}
    if flag is not None:
        config["SITE_ANALYTICS_ENABLED"] = flag
    source.write_text(yaml.safe_dump(config))
    module.configure(source, target)
    values = dict(line.split("=", 1) for line in target.read_text().splitlines())
    assert (json.loads(values["SITE_ANALYTICS_ENABLED"]) if "SITE_ANALYTICS_ENABLED" in values else None) == expected


def test_auth_bridge_flag_is_written_from_config_and_off_by_default(tmp_path):
    path = Path(__file__).resolve().parents[1] / "deploy/go-api/configure_environment.py"
    spec = importlib.util.spec_from_file_location("go_environment", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for flag, expected in ((None, False), (False, False), (True, True)):
        source, target = tmp_path / f"c{flag}.yaml", tmp_path / f"e{flag}"
        config = {"MONGO_URI": "mongodb://127.0.0.1/isolated", "MONGO_DBNAME": "isolated"}
        if flag is not None:
            config["AUTH_BRIDGE_SCOPES"] = flag
        source.write_text(yaml.safe_dump(config))
        module.configure(source, target)
        values = dict(line.split("=", 1) for line in target.read_text().splitlines())
        assert ("AUTH_BRIDGE_ENABLED" in values) is expected
        if expected:
            assert json.loads(values["AUTH_BRIDGE_ENABLED"]) == "true"
            assert json.loads(values["AUTH_BRIDGE_SESSION_SOCKET"]).endswith("mielenosoitus.sock")
            assert json.loads(values["AUTH_BRIDGE_ORIGIN"]).startswith("https://")


def test_developer_writes_flag_needs_the_bridge_and_is_off_by_default(tmp_path):
    path = Path(__file__).resolve().parents[1] / "deploy/go-api/configure_environment.py"
    spec = importlib.util.spec_from_file_location("go_environment", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = (
        ({}, False),
        ({"AUTH_BRIDGE_SCOPES": True}, False),
        ({"DEVELOPER_WRITES": True}, False),  # no bridge, no writes
        ({"AUTH_BRIDGE_SCOPES": True, "DEVELOPER_WRITES": False}, False),
        ({"AUTH_BRIDGE_SCOPES": True, "DEVELOPER_WRITES": True}, True),
    )
    for index, (extra, expected) in enumerate(cases):
        source, target = tmp_path / f"c{index}.yaml", tmp_path / f"e{index}"
        source.write_text(yaml.safe_dump({"MONGO_URI": "mongodb://127.0.0.1/isolated", "MONGO_DBNAME": "isolated", **extra}))
        module.configure(source, target)
        values = dict(line.split("=", 1) for line in target.read_text().splitlines())
        assert ("DEVELOPER_WRITES_ENABLED" in values) is expected


def test_account_settings_reads_flag_needs_the_bridge_and_is_off_by_default(tmp_path):
    path = Path(__file__).resolve().parents[1] / "deploy/go-api/configure_environment.py"
    spec = importlib.util.spec_from_file_location("go_environment", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = (
        ({}, False),
        ({"AUTH_BRIDGE_SCOPES": True}, True),  # on by default with the bridge
        ({"ACCOUNT_SETTINGS_READS": True}, False),  # no bridge, no route
        ({"AUTH_BRIDGE_SCOPES": True, "ACCOUNT_SETTINGS_READS": False}, False),
        ({"AUTH_BRIDGE_SCOPES": True, "ACCOUNT_SETTINGS_READS": True}, True),
    )
    for index, (extra, expected) in enumerate(cases):
        source, target = tmp_path / f"a{index}.yaml", tmp_path / f"ae{index}"
        source.write_text(yaml.safe_dump({"MONGO_URI": "mongodb://127.0.0.1/isolated", "MONGO_DBNAME": "isolated", **extra}))
        module.configure(source, target)
        values = dict(line.split("=", 1) for line in target.read_text().splitlines())
        assert ("ACCOUNT_SETTINGS_READS_ENABLED" in values) is expected


def test_account_settings_writes_need_the_bridge_and_the_reads(tmp_path):
    path = Path(__file__).resolve().parents[1] / "deploy/go-api/configure_environment.py"
    spec = importlib.util.spec_from_file_location("go_environment", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bridge = {"AUTH_BRIDGE_SCOPES": True}
    cases = (
        ({}, False),  # no bridge
        ({"ACCOUNT_SETTINGS_WRITES": True}, False),
        ({**bridge}, True),  # on by default with the bridge and the (default) reads
        ({**bridge, "ACCOUNT_SETTINGS_WRITES": True}, True),
        ({**bridge, "ACCOUNT_SETTINGS_READS": False, "ACCOUNT_SETTINGS_WRITES": True}, False),  # no reads
        ({"ACCOUNT_SETTINGS_READS": True, "ACCOUNT_SETTINGS_WRITES": True}, False),  # no bridge
        ({**bridge, "ACCOUNT_SETTINGS_READS": True}, True),
        ({**bridge, "ACCOUNT_SETTINGS_READS": True, "ACCOUNT_SETTINGS_WRITES": False}, False),
        ({**bridge, "ACCOUNT_SETTINGS_READS": True, "ACCOUNT_SETTINGS_WRITES": True}, True),
    )
    for index, (extra, expected) in enumerate(cases):
        source, target = tmp_path / f"w{index}.yaml", tmp_path / f"we{index}"
        source.write_text(yaml.safe_dump({"MONGO_URI": "mongodb://127.0.0.1/isolated", "MONGO_DBNAME": "isolated", **extra}))
        module.configure(source, target)
        values = dict(line.split("=", 1) for line in target.read_text().splitlines())
        assert ("ACCOUNT_SETTINGS_WRITES_ENABLED" in values) is expected


def test_profile_reads_flag_is_independent_of_the_bridge_and_off_by_default(tmp_path):
    path = Path(__file__).resolve().parents[1] / "deploy/go-api/configure_environment.py"
    spec = importlib.util.spec_from_file_location("go_environment", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = (
        ({}, False),
        ({"PROFILE_READS": False}, False),
        ({"PROFILE_READS": True}, True),
        ({"AUTH_BRIDGE_SCOPES": True, "PROFILE_READS": True}, True),
        ({"AUTH_BRIDGE_SCOPES": True}, False),
    )
    for index, (extra, expected) in enumerate(cases):
        source, target = tmp_path / f"p{index}.yaml", tmp_path / f"pe{index}"
        source.write_text(yaml.safe_dump({"MONGO_URI": "mongodb://127.0.0.1/isolated", "MONGO_DBNAME": "isolated", **extra}))
        module.configure(source, target)
        values = dict(line.split("=", 1) for line in target.read_text().splitlines())
        assert ("PROFILE_READS_ENABLED" in values) is expected


def test_national_admin_org_id_is_passed_only_with_profile_reads_and_validated(tmp_path):
    path = Path(__file__).resolve().parents[1] / "deploy/go-api/configure_environment.py"
    spec = importlib.util.spec_from_file_location("go_environment", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    org = "0123456789abcdef01234567"
    base = {"MONGO_URI": "mongodb://127.0.0.1/isolated", "MONGO_DBNAME": "isolated"}
    for index, (extra, expected) in enumerate((
        ({"PROFILE_READS": True, "NATIONAL_ADMIN_ORG_ID": org}, org),
        ({"NATIONAL_ADMIN_ORG_ID": org}, None),
        ({"PROFILE_READS": True}, None),
    )):
        source, target = tmp_path / f"n{index}.yaml", tmp_path / f"ne{index}"
        source.write_text(yaml.safe_dump({**base, **extra}))
        module.configure(source, target)
        values = dict(line.split("=", 1) for line in target.read_text().splitlines())
        assert (json.loads(values["NATIONAL_ADMIN_ORG_ID"]) if "NATIONAL_ADMIN_ORG_ID" in values else None) == expected
    source = tmp_path / "bad.yaml"
    source.write_text(yaml.safe_dump({**base, "PROFILE_READS": True, "NATIONAL_ADMIN_ORG_ID": "nothex"}))
    with pytest.raises(ValueError):
        module.configure(source, tmp_path / "bad-env")


def test_fundraising_reminder_flag_needs_public_submissions_and_is_off_by_default(tmp_path):
    path = Path(__file__).resolve().parents[1] / "deploy/go-api/configure_environment.py"
    spec = importlib.util.spec_from_file_location("go_environment", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    secret = "test-secret-not-for-production-" * 2
    cases = (
        ({}, False),
        ({"GO_SUBMIT_ENABLED": True}, False),
        ({"FUNDRAISING_REMINDER": True}, False),  # no submissions, no reminder
        ({"GO_SUBMIT_ENABLED": True, "FUNDRAISING_REMINDER": False}, False),
        ({"GO_SUBMIT_ENABLED": True, "FUNDRAISING_REMINDER": True}, True),
    )
    for index, (extra, expected) in enumerate(cases):
        source, target = tmp_path / f"c{index}.yaml", tmp_path / f"e{index}"
        source.write_text(yaml.safe_dump({"MONGO_URI": "mongodb://127.0.0.1/isolated", "MONGO_DBNAME": "isolated",
                                          "SECRET_KEY": secret, **extra}))
        module.configure(source, target)
        values = dict(line.split("=", 1) for line in target.read_text().splitlines())
        assert ("FUNDRAISING_REMINDER_ENABLED" in values) is expected


def test_pride_banner_signup_flag_needs_public_submissions_and_is_off_by_default(tmp_path):
    path = Path(__file__).resolve().parents[1] / "deploy/go-api/configure_environment.py"
    spec = importlib.util.spec_from_file_location("go_environment_banner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    secret = "test-secret-not-for-production-" * 2
    cases = (
        ({}, False),
        ({"PRIDE_BANNER_SIGNUP": True}, False),  # no submissions, no signup
        ({"GO_SUBMIT_ENABLED": True}, False),
        ({"GO_SUBMIT_ENABLED": True, "PRIDE_BANNER_SIGNUP": False}, False),
        ({"GO_SUBMIT_ENABLED": True, "PRIDE_BANNER_SIGNUP": True}, True),
    )
    for index, (extra, expected) in enumerate(cases):
        source, target = tmp_path / f"c{index}.yaml", tmp_path / f"e{index}"
        source.write_text(yaml.safe_dump({"MONGO_URI": "mongodb://127.0.0.1/isolated", "MONGO_DBNAME": "isolated",
                                          "SECRET_KEY": secret, **extra}))
        module.configure(source, target)
        values = dict(line.split("=", 1) for line in target.read_text().splitlines())
        assert ("PRIDE_BANNER_SIGNUP_ENABLED" in values) is expected


def test_fundraising_details_file_is_kept_only_when_it_exists(tmp_path):
    path = Path(__file__).resolve().parents[1] / "deploy/go-api/configure_environment.py"
    spec = importlib.util.spec_from_file_location("go_environment", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    details = tmp_path / "fundraising.json"
    for present, expected in ((False, False), (True, True)):
        if present:
            details.write_text("{}")
        source, target = tmp_path / f"c{present}.yaml", tmp_path / f"e{present}"
        source.write_text(yaml.safe_dump({"MONGO_URI": "mongodb://127.0.0.1/isolated", "MONGO_DBNAME": "isolated",
                                          "FUNDRAISING_DETAILS_FILE": str(details)}))
        module.configure(source, target)
        values = dict(line.split("=", 1) for line in target.read_text().splitlines())
        assert ("FUNDRAISING_DETAILS_FILE" in values) is expected


def test_contact_go_flag_needs_the_bridge_and_is_on_by_default(tmp_path):
    path = Path(__file__).resolve().parents[1] / "deploy/go-api/configure_environment.py"
    spec = importlib.util.spec_from_file_location("go_environment", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = (
        ({}, False),
        ({"AUTH_BRIDGE_SCOPES": True}, True),  # on by default with the bridge
        ({"CONTACT_GO_ENABLED": True}, False),  # no bridge, no contact route
        ({"AUTH_BRIDGE_SCOPES": True, "CONTACT_GO_ENABLED": False}, False),
        ({"AUTH_BRIDGE_SCOPES": True, "CONTACT_GO_ENABLED": True}, True),
    )
    for index, (extra, expected) in enumerate(cases):
        source, target = tmp_path / f"c{index}.yaml", tmp_path / f"e{index}"
        config = {"MONGO_URI": "mongodb://127.0.0.1/isolated", "MONGO_DBNAME": "isolated", **extra}
        if expected:
            config.update({"CONTACT_PER_IP_LIMIT": "2 per 10 minutes", "CONTACT_MAX_REQUEST_BYTES": 8192})
        source.write_text(yaml.safe_dump(config))
        module.configure(source, target)
        values = dict(line.split("=", 1) for line in target.read_text().splitlines())
        assert ("CONTACT_GO_ENABLED" in values) is expected
        if expected:
            assert json.loads(values["CONTACT_GO_ENABLED"]) == "true"
            assert json.loads(values["CONTACT_PER_IP_LIMIT"]) == "2 per 10 minutes"
            assert json.loads(values["CONTACT_MAX_REQUEST_BYTES"]) == "8192"
            assert "CONTACT_GLOBAL_LIMIT" not in values


def test_campaign_volunteers_go_flag_needs_the_bridge_and_is_on_by_default(tmp_path):
    path = Path(__file__).resolve().parents[1] / "deploy/go-api/configure_environment.py"
    spec = importlib.util.spec_from_file_location("go_environment", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = (
        ({}, False),
        ({"AUTH_BRIDGE_SCOPES": True}, True),  # on by default with the bridge
        ({"CAMPAIGN_VOLUNTEERS_GO_ENABLED": True}, False),  # no bridge, no route
        ({"AUTH_BRIDGE_SCOPES": True, "CAMPAIGN_VOLUNTEERS_GO_ENABLED": False}, False),
        ({"AUTH_BRIDGE_SCOPES": True, "CAMPAIGN_VOLUNTEERS_GO_ENABLED": True}, True),
    )
    for index, (extra, expected) in enumerate(cases):
        source, target = tmp_path / f"v{index}.yaml", tmp_path / f"w{index}"
        source.write_text(yaml.safe_dump({"MONGO_URI": "mongodb://127.0.0.1/isolated", "MONGO_DBNAME": "isolated", **extra}))
        module.configure(source, target)
        values = dict(line.split("=", 1) for line in target.read_text().splitlines())
        assert ("CAMPAIGN_VOLUNTEERS_GO_ENABLED" in values) is expected


def test_suggestion_writes_follow_public_submissions_and_default_on(tmp_path):
    path = Path(__file__).resolve().parents[1] / "deploy/go-api/configure_environment.py"
    spec = importlib.util.spec_from_file_location("go_environment_suggestions", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    secret = "test-secret-not-for-production-" * 2
    cases = (
        ({}, False),
        ({"SUGGESTIONS_GO": True}, False),  # no public submissions, no route
        ({"GO_SUBMIT_ENABLED": True}, True),
        ({"GO_SUBMIT_ENABLED": True, "SUGGESTIONS_GO": False}, False),
    )
    for index, (extra, expected) in enumerate(cases):
        source, target = tmp_path / f"s{index}.yaml", tmp_path / f"se{index}"
        source.write_text(yaml.safe_dump({"MONGO_URI": "mongodb://127.0.0.1/isolated", "MONGO_DBNAME": "isolated",
                                          "SECRET_KEY": secret, **extra}))
        module.configure(source, target)
        values = dict(line.split("=", 1) for line in target.read_text().splitlines())
        assert ("SUGGESTIONS_GO_ENABLED" in values) is expected


def test_suggestion_routing_is_guarded_by_the_release_check():
    root = Path(__file__).resolve().parents[1]
    routing = (root / "deploy/apache/react-ssr-routing.conf").read_text(encoding="utf-8")
    release = (root / "deploy/release_routing.sh").read_text(encoding="utf-8")
    assert 'RewriteRule "^/organization/[a-fA-F0-9]{24}/save_suggestion$" "http://127.0.0.1:8091%{REQUEST_URI}" [P,L]' in routing
    assert 'RewriteRule "^/suggest_change/[a-fA-F0-9]{24}$" "http://127.0.0.1:8091%{REQUEST_URI}" [P,L]' in routing
    assert "Sec-Fetch-Site: cross-site" in release and "suggest_change/000000000000000000000000" in release


def test_token_confirm_flags_follow_public_submissions_and_default_on(tmp_path):
    from mielenosoitukset_fi.utils.auth import derive_confirmation_key

    path = Path(__file__).resolve().parents[1] / "deploy/go-api/configure_environment.py"
    spec = importlib.util.spec_from_file_location("go_environment_token_confirm", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    secret = "test-secret-not-for-production-" * 2
    cases = (
        ({}, False, False),
        ({"VOLUNTEER_CONFIRM_GO": True, "EMAIL_CONFIRM_GO": True}, False, False),  # no public submissions
        ({"GO_SUBMIT_ENABLED": True}, True, True),
        ({"GO_SUBMIT_ENABLED": True, "VOLUNTEER_CONFIRM_GO": False}, False, True),
        ({"GO_SUBMIT_ENABLED": True, "EMAIL_CONFIRM_GO": False}, True, False),
    )
    for index, (extra, volunteer, email) in enumerate(cases):
        source, target = tmp_path / f"t{index}.yaml", tmp_path / f"te{index}"
        source.write_text(yaml.safe_dump({"MONGO_URI": "mongodb://127.0.0.1/isolated", "MONGO_DBNAME": "isolated",
                                          "SECRET_KEY": secret, **extra}))
        module.configure(source, target)
        text = target.read_text()
        values = dict(line.split("=", 1) for line in text.splitlines())
        assert ("VOLUNTEER_CONFIRM_GO_ENABLED" in values) is volunteer
        assert ("EMAIL_CONFIRM_GO_ENABLED" in values) is email
        assert ("EMAIL_CONFIRM_JWT_KEY" in values) is email
        # Go gets the derived key Flask signs with, never SECRET_KEY itself.
        assert secret not in text
        if email:
            assert json.loads(values["EMAIL_CONFIRM_JWT_KEY"]) == derive_confirmation_key(secret)


def test_org_invite_flag_needs_the_bridge_and_both_team_ids(tmp_path):
    path = Path(__file__).resolve().parents[1] / "deploy/go-api/configure_environment.py"
    spec = importlib.util.spec_from_file_location("go_environment_org_invite", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    national, board = "a" * 24, "b" * 24
    cases = (
        ({"NATIONAL_ADMIN_ORG_ID": national, "BOARD_ORG_ID": board}, False),  # no bridge
        ({"AUTH_BRIDGE_SCOPES": True}, False),  # no team ids
        ({"AUTH_BRIDGE_SCOPES": True, "NATIONAL_ADMIN_ORG_ID": national}, False),  # one id only
        ({"AUTH_BRIDGE_SCOPES": True, "NATIONAL_ADMIN_ORG_ID": national, "BOARD_ORG_ID": board}, True),
        ({"AUTH_BRIDGE_SCOPES": True, "NATIONAL_ADMIN_ORG_ID": national, "BOARD_ORG_ID": board,
          "ORG_INVITE_GO": False}, False),
    )
    for index, (extra, expected) in enumerate(cases):
        source, target = tmp_path / f"o{index}.yaml", tmp_path / f"oe{index}"
        source.write_text(yaml.safe_dump({"MONGO_URI": "mongodb://127.0.0.1/isolated", "MONGO_DBNAME": "isolated", **extra}))
        module.configure(source, target)
        values = dict(line.split("=", 1) for line in target.read_text().splitlines())
        assert ("ORG_INVITE_GO_ENABLED" in values) is expected
        assert ("BOARD_ORG_ID" in values) is expected
        if expected:
            assert json.loads(values["NATIONAL_ADMIN_ORG_ID"]) == national
            assert json.loads(values["BOARD_ORG_ID"]) == board
    source = tmp_path / "bad.yaml"
    source.write_text(yaml.safe_dump({"MONGO_URI": "mongodb://127.0.0.1/isolated", "MONGO_DBNAME": "isolated",
                                      "AUTH_BRIDGE_SCOPES": True, "NATIONAL_ADMIN_ORG_ID": national, "BOARD_ORG_ID": "nothex"}))
    with pytest.raises(ValueError):
        module.configure(source, tmp_path / "bad-env")


def test_org_invite_routing_is_guarded_by_the_release_check():
    root = Path(__file__).resolve().parents[1]
    routing = (root / "deploy/apache/react-ssr-routing.conf").read_text(encoding="utf-8")
    release = (root / "deploy/release_routing.sh").read_text(encoding="utf-8")
    lines = [line for line in routing.splitlines() if not line.lstrip().startswith("#")]
    api = 'RewriteRule "^/api/organizations/[0-9a-f]{24}/invitation(/accept)?$" "http://127.0.0.1:8091$0" [P,L]'
    page = 'RewriteRule "^/users/orgs/accept_invite$" "http://127.0.0.1:8090$0" [P,L]'
    assert lines[lines.index(page) - 1] == 'RewriteCond %{REQUEST_METHOD} "^(GET|HEAD)$"'
    assert api in lines
    assert "000000000000000000000000/invitation/accept || true) == 405" in release
    assert release.index("org_invite_ready") < release.index('install -d -m 0700 "$backup_root"')


def test_org_requests_flag_follows_public_submissions_and_defaults_on(tmp_path):
    path = Path(__file__).resolve().parents[1] / "deploy/go-api/configure_environment.py"
    spec = importlib.util.spec_from_file_location("go_environment_org_requests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    secret = "test-secret-not-for-production-" * 2
    cases = (
        ({}, False),
        ({"ORG_REQUESTS_GO": True}, False),  # no public submissions: no origin or session socket
        ({"GO_SUBMIT_ENABLED": True}, True),
        ({"GO_SUBMIT_ENABLED": True, "ORG_REQUESTS_GO": False}, False),
    )
    for index, (extra, expected) in enumerate(cases):
        source, target = tmp_path / f"r{index}.yaml", tmp_path / f"re{index}"
        source.write_text(yaml.safe_dump({"MONGO_URI": "mongodb://127.0.0.1/isolated", "MONGO_DBNAME": "isolated",
                                          "SECRET_KEY": secret, **extra}))
        module.configure(source, target)
        values = dict(line.split("=", 1) for line in target.read_text().splitlines())
        assert ("ORG_REQUESTS_GO_ENABLED" in values) is expected


def test_org_requests_routing_is_guarded_by_the_release_check():
    root = Path(__file__).resolve().parents[1]
    routing = (root / "deploy/apache/react-ssr-routing.conf").read_text(encoding="utf-8")
    release = (root / "deploy/release_routing.sh").read_text(encoding="utf-8")
    lines = [line for line in routing.splitlines() if not line.lstrip().startswith("#")]
    assert 'RewriteRule "^/organisaatiot/hae$" "http://127.0.0.1:8091/api/organization-requests" [P,L]' in lines
    assert "http://127.0.0.1:8091/api/organization-requests || true) == 405" in release
    assert release.index("org_requests_ready") < release.index('install -d -m 0700 "$backup_root"')


def test_unconfirmed_org_requests_expire_through_a_ttl_index():
    from types import SimpleNamespace
    from mielenosoitukset_fi.scripts.provision_public_indexes import ensure_organization_request_indexes

    calls = []
    db = SimpleNamespace(organization_requests=SimpleNamespace(
        create_index=lambda *args, **kwargs: calls.append((args, kwargs))))
    ensure_organization_request_indexes(db)
    assert calls == [(("expires_at",), {"expireAfterSeconds": 0, "name": "organization_requests_unconfirmed_ttl"})]
