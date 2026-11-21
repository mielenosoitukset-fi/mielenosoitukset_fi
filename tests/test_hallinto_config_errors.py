"""A bad Hallinto city grant config file names its cause without values.

On 21 November 2026 a directory mode change left the city grant file
unreadable for the service user. Every worker failed with a bare "configuration
is invalid", which hid the PermissionError until the host journal was read.
The routes are now disabled instead of crashing the site; the recorded reason
must still say what went wrong.
"""

import logging
import os

from flask import Flask

from mielenosoitukset_fi.admin import hallinto_city_grant_api


def _app():
    return Flask("hallinto-config-errors")


def test_unreadable_city_grant_file_names_the_os_error(tmp_path, monkeypatch, caplog):
    path = tmp_path / "config.json"
    path.write_text('{"proxy_secret_sha256": "synthetic-secret-value"}')
    path.chmod(0o600)
    monkeypatch.setenv("HALLINTO_CITY_GRANT_CONFIG_FILE", str(path))

    def denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(os, "open", denied)
    app = _app()
    with caplog.at_level(logging.ERROR):
        hallinto_city_grant_api.configure(app, db=None)
    assert app.extensions["hallinto_city_grants_disabled"] == "PermissionError"
    message = " ".join(record.getMessage() for record in caplog.records)
    assert "(PermissionError)" in message
    assert str(tmp_path) not in message and "synthetic-secret-value" not in message


def test_unprotected_city_grant_file_names_the_validation_error(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text('{"proxy_secret_sha256": "synthetic-secret-value"}')
    path.chmod(0o644)
    monkeypatch.setenv("HALLINTO_CITY_GRANT_CONFIG_FILE", str(path))
    app = _app()
    hallinto_city_grant_api.configure(app, db=None)
    assert app.extensions["hallinto_city_grants_disabled"] == "ValueError"


def test_incomplete_city_grant_settings_name_the_validation_error(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text('{"proxy_secret_sha256": "synthetic-secret-value"}')
    path.chmod(0o600)
    monkeypatch.setenv("HALLINTO_CITY_GRANT_CONFIG_FILE", str(path))
    app = _app()
    hallinto_city_grant_api.configure(app, db=None)
    assert app.extensions["hallinto_city_grants_disabled"] == "ValueError"
