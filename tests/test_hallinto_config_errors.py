"""Boot failures from a Hallinto config file name their cause without values.

On 21 November 2026 a directory mode change left the city grant file
unreadable for the service user. Every worker failed with a bare "configuration
is invalid", which hid the PermissionError until the host journal was read.
"""

import os

import pytest
from flask import Flask

from mielenosoitukset_fi.admin import hallinto_city_grant_api


def _app():
    return Flask("hallinto-config-errors")


def test_unreadable_city_grant_file_names_the_os_error(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text('{"proxy_secret_sha256": "synthetic-secret-value"}')
    path.chmod(0o600)
    monkeypatch.setenv("HALLINTO_CITY_GRANT_CONFIG_FILE", str(path))

    def denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(os, "open", denied)
    with pytest.raises(RuntimeError) as caught:
        hallinto_city_grant_api.configure(_app(), db=None)
    message = str(caught.value)
    assert message == "Hallinto city grant configuration is invalid (PermissionError)"
    assert str(tmp_path) not in message and "synthetic-secret-value" not in message


def test_unprotected_city_grant_file_names_the_validation_error(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text('{"proxy_secret_sha256": "synthetic-secret-value"}')
    path.chmod(0o644)
    monkeypatch.setenv("HALLINTO_CITY_GRANT_CONFIG_FILE", str(path))
    with pytest.raises(RuntimeError, match=r"^Hallinto city grant configuration is invalid \(ValueError\)$"):
        hallinto_city_grant_api.configure(_app(), db=None)


def test_incomplete_city_grant_settings_name_the_validation_error(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text('{"proxy_secret_sha256": "synthetic-secret-value"}')
    path.chmod(0o600)
    monkeypatch.setenv("HALLINTO_CITY_GRANT_CONFIG_FILE", str(path))
    with pytest.raises(RuntimeError) as caught:
        hallinto_city_grant_api.configure(_app(), db=None)
    assert str(caught.value) == "Hallinto city grant configuration is invalid (ValueError)"
    assert "synthetic-secret-value" not in str(caught.value)
