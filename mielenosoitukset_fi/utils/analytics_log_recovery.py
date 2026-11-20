"""Recover bounded, anonymous page-request counts from Apache combined logs.

These are origin requests, not pageviews. Never merge them into site_analytics.
"""
import gzip
import re
from collections import Counter
from datetime import datetime
from urllib.parse import unquote, urlsplit

from mielenosoitukset_fi.utils.site_analytics import (
    BOT, GENERIC_PAGE_TITLES, HELSINKI_TZ, classify_device,
)

COLLECTION = "site_analytics_log_recovery"
SNAPSHOT_ID = "apache-public-pages-v1"
PAGE_TITLES = {
    **GENERIC_PAGE_TITLES,
    "/kampanja": "Kampanja",
    "/pride-nakyvaksi": "Pride näkyväksi",
}
_COMBINED = re.compile(
    r'^\S+ \S+ \S+ \[([^]]+)\] "((?:[^"\\]|\\.)*)" '
    r'(\d{3}) \S+ "(?:[^"\\]|\\.)*" "((?:[^"\\]|\\.)*)"(?: .*)?$'
)


def recover_logs(paths, start, end):
    """Read an inclusive range of completed Helsinki days; retain no raw fields."""
    counts = Counter()
    parsed = malformed = 0
    first = last = None
    for path in sorted(set(paths)):
        opener = gzip.open if str(path).endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                match = _COMBINED.fullmatch(line.rstrip("\n"))
                if not match:
                    malformed += 1
                    continue
                stamp, request_line, status, agent = match.groups()
                try:
                    moment = datetime.strptime(stamp, "%d/%b/%Y:%H:%M:%S %z").astimezone(HELSINKI_TZ)
                except ValueError:
                    malformed += 1
                    continue
                if not start <= moment.date() <= end:
                    continue
                parsed += 1
                first = min(first, moment) if first else moment
                last = max(last, moment) if last else moment
                parts = request_line.split(" ", 2)
                if len(parts) != 3 or parts[0] != "GET":
                    # Apache also logs aborted connections as "-" and raw TLS
                    # probes as escaped bytes; neither is a page request.
                    continue
                method, target, protocol = parts
                if not protocol.startswith("HTTP/"):
                    continue
                try:
                    page = unquote(urlsplit(target).path).rstrip("/")
                except ValueError:
                    continue
                if (method == "GET" and 200 <= int(status) < 300
                        and page in PAGE_TITLES and classify_device(agent) != BOT):
                    counts[(moment.strftime("%Y-%m-%d"), page)] += 1
    if not parsed or malformed:
        raise ValueError(f"Incomplete log parse: {parsed} in-range records, {malformed} malformed records")
    return {
        "_id": SNAPSHOT_ID,
        "first_request": first.isoformat(),
        "last_request": last.isoformat(),
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "parsed_records": parsed,
        "rows": [{"date": day, "path": page, "count": count}
                 for (day, page), count in sorted(counts.items())],
    }


def get_recovered_pages(db, start, end):
    snapshot = db[COLLECTION].find_one({"_id": SNAPSHOT_ID})
    if not snapshot:
        return None
    counts = Counter()
    for row in snapshot["rows"]:
        if start <= row["date"] <= end and row["path"] in PAGE_TITLES:
            counts[row["path"]] += row["count"]
    return {
        "first_request": datetime.fromisoformat(snapshot["first_request"]).strftime("%d.%m.%Y klo %H.%M"),
        "last_request": datetime.fromisoformat(snapshot["last_request"]).strftime("%d.%m.%Y klo %H.%M"),
        "rows": [{"path": path, "title": PAGE_TITLES[path], "count": count}
                 for path, count in counts.most_common()],
    }
