import gzip
from datetime import date, datetime, timedelta

import pytest

from mielenosoitukset_fi.utils.analytics_log_recovery import (
    COLLECTION, SNAPSHOT_ID, get_recovered_pages, recover_logs,
)


def line(path="/ker%C3%A4ys?email=private", method="GET", status=200,
         agent="Mozilla/5.0 Macintosh", stamp="14/Nov/2026:23:30:00 +0000"):
    return f'192.0.2.1 - - [{stamp}] "{method} {path} HTTP/1.1" {status} 123 "https://example.test/private" "{agent}"\n'


def test_recovery_filters_requests_and_retains_only_safe_daily_counts(tmp_path):
    log = tmp_path / "access.log.gz"
    with gzip.open(log, "wt") as stream:
        stream.write(line())
        stream.write(line("/privacy", stamp="15/Nov/2026:10:00:00 +0200"))
        stream.write(line("/kampanja/?token=private"))
        stream.write(line("/kampanja/confirm/private"))
        stream.write(line("/ker%C3%A4ys", status=302))
        stream.write(line(method="HEAD"))
        stream.write(line(agent="Googlebot"))
        stream.write(line(status=408).replace('"GET /ker%C3%A4ys?email=private HTTP/1.1"', '"-"'))
        stream.write(line(stamp="13/Nov/2026:10:00:00 +0200"))
    snapshot = recover_logs([log, log], date(2026, 11, 15), date(2026, 11, 15))
    assert snapshot["rows"] == [
        {"date": "2026-11-15", "path": "/kampanja", "count": 1},
        {"date": "2026-11-15", "path": "/keräys", "count": 1},
        {"date": "2026-11-15", "path": "/privacy", "count": 1},
    ]
    assert "private" not in str(snapshot)
    assert "192.0.2.1" not in str(snapshot)


def test_recovery_refuses_unparsed_or_empty_input(tmp_path):
    log = tmp_path / "access.log"
    log.write_text(line() + "unknown format\n")
    with pytest.raises(ValueError, match="malformed"):
        recover_logs([log], date(2026, 11, 15), date(2026, 11, 15))


def test_recovery_accepts_escaped_headers_and_origin_log_suffix(tmp_path):
    log = tmp_path / "access.log"
    record = line(agent=r'Mozilla/5.0 \"quoted\"').replace(
        '"https://example.test/private"', r'"https://example.test/\"quoted\""',
    )
    log.write_text(record.rstrip("\n") + ' origin="example.test"\n')
    snapshot = recover_logs([log], date(2026, 11, 15), date(2026, 11, 15))
    assert snapshot["rows"] == [{"date": "2026-11-15", "path": "/keräys", "count": 1}]


def test_import_is_preview_only_by_default_and_replacement_is_backed_up(tmp_path, db, monkeypatch):
    from bson import json_util
    from mielenosoitukset_fi.scripts import recover_analytics_pages as cli
    from mielenosoitukset_fi.utils.site_analytics import HELSINKI_TZ

    day = datetime.now(HELSINKI_TZ).date() - timedelta(days=1)
    log = tmp_path / "access.log"
    log.write_text(line(stamp=day.strftime("%d/%b/%Y") + ":12:00:00 +0200"))
    output = tmp_path / "output.json"
    args = ["recover", str(log), "--start", day.isoformat(), "--end", day.isoformat(), "--output", str(output)]
    db[COLLECTION].delete_many({})
    monkeypatch.setattr("sys.argv", args)
    cli.main()
    assert db[COLLECTION].count_documents({}) == 0
    backup = tmp_path / "before.json"
    monkeypatch.setattr("sys.argv", args + ["--apply", "--backup", str(backup)])
    cli.main()
    assert json_util.loads(backup.read_text()) is None
    previous = db[COLLECTION].find_one({"_id": SNAPSHOT_ID})
    log.write_text(log.read_text() * 2)
    backup2 = tmp_path / "before2.json"
    monkeypatch.setattr("sys.argv", args + ["--apply", "--backup", str(backup2)])
    cli.main()
    assert json_util.loads(backup2.read_text()) == previous
    assert db[COLLECTION].find_one({"_id": SNAPSHOT_ID})["rows"][0]["count"] == 2


def test_recovered_report_respects_range_and_does_not_change_pageviews(db, app):
    db[COLLECTION].delete_many({})
    db[COLLECTION].insert_one({
        "_id": SNAPSHOT_ID,
        "first_request": "2026-11-01T01:00:00+02:00",
        "last_request": "2026-11-15T23:00:00+02:00",
        "rows": [
            {"date": "2026-11-01", "path": "/keräys", "count": 8},
            {"date": "2026-11-15", "path": "/keräys", "count": 2},
        ],
    })
    before = list(db.site_analytics.find())
    report = get_recovered_pages(db, "2026-11-10", "2026-11-16")
    assert report["rows"] == [{"path": "/keräys", "title": "Keräys", "count": 2}]
    assert list(db.site_analytics.find()) == before


def test_admin_recovery_empty_error_and_permission_states(admin_client, client, app, db, monkeypatch):
    from inspect import unwrap

    db[COLLECTION].delete_many({})
    response = admin_client.get("/admin/analytics/")
    assert response.status_code == 200
    assert "Valitulta aikaväliltä ei ole palautettuja lokitietoja." in response.text
    assert client.get("/admin/analytics/").status_code in (302, 401)
    view = unwrap(app.view_functions["admin_site_analytics.site_overview"])
    monkeypatch.setitem(view.__globals__, "get_recovered_pages", lambda *args: (_ for _ in ()).throw(RuntimeError("offline")))
    response = admin_client.get("/admin/analytics/")
    assert response.status_code == 200
    assert "Lokitietoja ei voitu ladata." in response.text
