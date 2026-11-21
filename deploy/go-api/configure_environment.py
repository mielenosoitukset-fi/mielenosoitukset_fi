#!/usr/bin/env python3
"""Copy the authorized production DB settings without printing credential values."""
import json
import hashlib
import hmac
import os
from pathlib import Path
import tempfile
import yaml


def _system_team_id(config, key):
    """A 24-hex organization id from config.yaml, or None when absent; junk is an error."""
    value = config.get(key)
    if not value:
        return None
    value = str(value)
    if len(value) != 24 or any(c not in "0123456789abcdefABCDEF" for c in value):
        raise ValueError(f"Invalid {key}")
    return value


def configure(source, target):
    config = yaml.safe_load(Path(source).read_text())
    values = {"MONGODB_URI": config.get("MONGO_URI"), "MONGODB_DATABASE": config.get("MONGO_DBNAME"), "API_ADDR": "127.0.0.1:8091"}
    # Existing local Redis; isolated application keys, no global Redis changes.
    # An empty explicit value opts out and keeps the in-process caches only.
    redis_url = config.get("GO_PUBLIC_REDIS_URL", "redis://127.0.0.1:6379/0")
    if not isinstance(redis_url, str) or any(c in redis_url for c in "\n\r\0"):
        raise ValueError("Invalid GO_PUBLIC_REDIS_URL")
    if redis_url:
        values["PUBLIC_REDIS_URL"] = redis_url
    # Writes require an explicit rollout switch. Derive a separate key; Go
    # cannot use this value to validate Flask sessions or mint authentication.
    if config.get("GO_SUBMIT_ENABLED") is True:
        secret = config.get("SECRET_KEY")
        if not isinstance(secret, str) or len(secret) < 32:
            raise ValueError("A strong SECRET_KEY is required for Go submission intake")
        values["PUBLIC_SUBMIT_SECRET"] = hmac.new(
            secret.encode(), b"moso-go-submission-rate-v1", hashlib.sha256,
        ).hexdigest()
        values["PUBLIC_SUBMIT_ORIGIN"] = config.get("GO_SUBMIT_ORIGIN", "https://mielenosoitukset.fi")
        values["PUBLIC_SUBMIT_SESSION_SOCKET"] = "/var/www/mielenosoitukset_fi/mielenosoitus.sock"
        babel = config.get("BABEL") or {}
        values["PUBLIC_SUBMIT_LOCALES"] = ",".join(babel.get("SUPPORTED_LOCALES", ["fi", "en"]) or ["fi"])
        # Fundraising opening reminder signup (docs/fundraising-reminder.md): off
        # unless set, and only with public submissions on. The Go API also needs
        # the unique e-mail index and disables the routes without it.
        if config.get("FUNDRAISING_REMINDER") is True:
            values["FUNDRAISING_REMINDER_ENABLED"] = "true"
        # Pride banner carrier signup (docs/pride-banner-volunteers.md): same rules.
        if config.get("PRIDE_BANNER_SIGNUP") is True:
            values["PRIDE_BANNER_SIGNUP_ENABLED"] = "true"
        # Campaign opening interest signup (docs/campaign-opening-interest.md): same rules.
        if config.get("OPENING_INTEREST_SIGNUP") is True:
            values["OPENING_INTEREST_SIGNUP_ENABLED"] = "true"
        # Organization account requests (docs/organization-requests.md): the form
        # needs the submission origin and session socket above. On by default
        # since 21 November 2026 (maintainer: "turn everything on"), when the
        # Apache rules were already live; `ORG_REQUESTS_GO: false` opts out.
        if config.get("ORG_REQUESTS_GO", True) is True:
            values["ORG_REQUESTS_GO_ENABLED"] = "true"
            # Access recovery form (docs/organization-requests.md, O5): on with the
            # requests above since 21 November 2026 (maintainer: "approve everything");
            # `ORG_RECOVERY_GO: false` opts out.
            if config.get("ORG_RECOVERY_GO", True) is True:
                values["ORG_RECOVERY_GO_ENABLED"] = "true"
        # Suggestion form writes (docs/go-public-api.md): need public submissions, as
        # above. On by default since 20 November 2026 (maintainer's go);
        # `SUGGESTIONS_GO: false` in config.yaml opts out. Without this line a
        # hand-set environment flag was lost at the next deploy.
        if config.get("SUGGESTIONS_GO", True) is True:
            values["SUGGESTIONS_GO_ENABLED"] = "true"
        # Campaign volunteer confirmation by button and POST
        # (docs/volunteer-confirm-react.md): on by default since 21 November 2026
        # (maintainer's go); `VOLUNTEER_CONFIRM_GO: false` in config.yaml opts out.
        if config.get("VOLUNTEER_CONFIRM_GO", True) is True:
            values["VOLUNTEER_CONFIRM_GO_ENABLED"] = "true"
        # E-mail confirmation by button and POST (docs/email-confirm-react.md): on
        # by default since 21 November 2026; `EMAIL_CONFIRM_GO: false` opts out.
        # Go gets only the derived key Flask signs these tokens with
        # (utils/auth.py derive_confirmation_key), never SECRET_KEY, and it is
        # derived here on every deploy so a rewrite cannot drop it.
        if config.get("EMAIL_CONFIRM_GO", True) is True:
            values["EMAIL_CONFIRM_GO_ENABLED"] = "true"
            values["EMAIL_CONFIRM_JWT_KEY"] = hmac.new(
                secret.encode(), b"moso-email-confirmation-token-v1", hashlib.sha256,
            ).hexdigest()
    # Event analytics follow the Flask switch, which defaults to enabled when
    # the key is missing (app.py: config.get("SITE_ANALYTICS_ENABLED", True)),
    # so a redeploy keeps Go writes in step with Flask.
    if config.get("SITE_ANALYTICS_ENABLED", True):
        values["SITE_ANALYTICS_ENABLED"] = "true"
    # The read-only auth bridge (docs/auth-bridge.md) follows the same switch as
    # Flask's /api/session scopes, so one line in config.yaml turns both on. The
    # environment is rewritten on every deploy; a hand-edited flag would be lost.
    if config.get("AUTH_BRIDGE_SCOPES") is True:
        values["AUTH_BRIDGE_ENABLED"] = "true"
        values["AUTH_BRIDGE_ORIGIN"] = config.get("GO_SUBMIT_ORIGIN", "https://mielenosoitukset.fi")
        values["AUTH_BRIDGE_SESSION_SOCKET"] = "/var/www/mielenosoitukset_fi/mielenosoitus.sock"
        # Developer write controls (docs/developer-react-write-controls.md): a
        # separate switch, only with the bridge on. On by default since
        # 21 November 2026 (maintainer: "turn everything on", independent
        # review SHIP); `DEVELOPER_WRITES: false` in config.yaml opts out.
        if config.get("DEVELOPER_WRITES", True) is True:
            values["DEVELOPER_WRITES_ENABLED"] = "true"
        # Go POST /contact (docs/go-contact.md): off by default, and only with
        # the bridge on, because the one-time token lives in Flask's session.
        # Quotas and the body bound follow the Flask settings of the same name
        # so both runtimes enforce the same numbers.
        # On by default since 20 November 2026 (maintainer's go); an explicit
        # `CONTACT_GO_ENABLED: false` keeps Flask's handler.
        if config.get("CONTACT_GO_ENABLED", True) is True:
            values["CONTACT_GO_ENABLED"] = "true"
            for key in ("CONTACT_PER_IP_LIMIT", "CONTACT_GLOBAL_LIMIT", "CONTACT_MAX_REQUEST_BYTES"):
                if config.get(key) is not None:
                    values[key] = str(config[key])
        # Go POST /kampanja/api/volunteers (docs/go-campaign-volunteers.md): on by
        # default (an explicit false opts out), and only with the bridge on,
        # because a private campaign lets signed-in accounts preview the form.
        if config.get("CAMPAIGN_VOLUNTEERS_GO_ENABLED", True) is True:
            values["CAMPAIGN_VOLUNTEERS_GO_ENABLED"] = "true"
        # Account settings reads (docs/account-pages-react.md, step 2): the route
        # needs the signed-in account, so it exists only with the bridge on. On by
        # default since the React settings page was activated (20 November 2026,
        # maintainer's go); `ACCOUNT_SETTINGS_READS: false` in config.yaml opts out.
        if config.get("ACCOUNT_SETTINGS_READS", True) is True:
            values["ACCOUNT_SETTINGS_READS_ENABLED"] = "true"
            # Bio and general writes (steps 3a, 3b) and later account writes: their
            # own switch, on by default since 20 November 2026 (maintainer's go;
            # `ACCOUNT_SETTINGS_WRITES: false` opts out), and only on top of reads.
            if config.get("ACCOUNT_SETTINGS_WRITES", True) is True:
                values["ACCOUNT_SETTINGS_WRITES_ENABLED"] = "true"
        # Organization invitations by button and POST (docs/org-invite-react.md):
        # on by default since 21 November 2026 (maintainer's go), only with the
        # bridge on; `ORG_INVITE_GO: false` opts out. Go refuses to accept into a
        # system team, so it needs both team ids; without both the flag is not
        # written and release_routing.sh keeps the old routing.
        if config.get("ORG_INVITE_GO", True) is True:
            national = _system_team_id(config, "NATIONAL_ADMIN_ORG_ID")
            board = _system_team_id(config, "BOARD_ORG_ID")
            if national and board:
                values["ORG_INVITE_GO_ENABLED"] = "true"
                values["NATIONAL_ADMIN_ORG_ID"] = national
                values["BOARD_ORG_ID"] = board
    # Public profile reads (docs/account-pages-react.md): GET /api/profile/<username>.
    # Its own switch, off by default and independent of the bridge; the own and
    # viewer parts of the answer appear only when the bridge is on as well.
    if config.get("PROFILE_READS") is True:
        values["PROFILE_READS_ENABLED"] = "true"
        # The badge needs the national team organisation, as Flask reads it. Absent
        # means no national access (the badge then follows the stored flag only).
        national = _system_team_id(config, "NATIONAL_ADMIN_ORG_ID")
        if national:
            values["NATIONAL_ADMIN_ORG_ID"] = national
    # The fundraising details file (docs/go-fundraising-details.md) used to be
    # added to the environment by hand and was lost at the next deploy. Keep it
    # whenever the file exists; `FUNDRAISING_DETAILS_FILE` in config.yaml
    # overrides the default location. Without the file the host uses its
    # bundled copy and the reminder signup form stays hidden.
    details_file = config.get("FUNDRAISING_DETAILS_FILE") or "/etc/mielenosoitukset-fundraising/fundraising.json"
    if isinstance(details_file, str) and Path(details_file).is_file():
        values["FUNDRAISING_DETAILS_FILE"] = details_file
    for key, value in values.items():
        if not isinstance(value, str) or not value or any(c in value for c in "\n\r\0"):
            raise ValueError(f"Missing or invalid {key}; refusing to guess production settings")
    target = Path(target)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=target.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            os.fchmod(file.fileno(), 0o600)
            for key, value in values.items():
                file.write(f"{key}={json.dumps(value, ensure_ascii=False)}\n")
            file.flush()
            os.fsync(file.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("target")
    args = parser.parse_args()
    configure(args.source, args.target)
    print("API environment installed with mode0600; credential values were not printed")
