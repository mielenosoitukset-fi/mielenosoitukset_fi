"""The React "all notifications" page: parser, link safety and the Flask list limit."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = """
import { readNotices, noticeHref, classifyNoticesResponse } from './frontend/src/app/notifications-route.ts';
const origin = 'https://mielenosoitukset.fi';
const good = [{ id: 'a', type: 'org_invite', link: '/orgs/accept_invite?x=1', message: 'Kutsu', time: '2026-11-20T10:00:00Z', read: false },
  { id: 'b', type: 'x', link: '#', message: 'M', time: 't', read: 'yes' }];
console.log(JSON.stringify({
  parsed: readNotices(good),
  bad: [readNotices('x'), readNotices([{ message: 1, link: '', time: '' }]), readNotices([null])],
  capped: readNotices(Array.from({ length: 60 }, () => good[0])).length,
  hrefs: [noticeHref('/demonstration/1#x', origin), noticeHref('https://evil.example/x', origin), noticeHref('javascript:alert(1)', origin),
    noticeHref('//evil.example/x', origin), noticeHref('#', origin), noticeHref('', origin), noticeHref('https://mielenosoitukset.fi/a?b=1', origin)],
  kinds: [classifyNoticesResponse({ status: 0, type: 'opaqueredirect', ok: false }), classifyNoticesResponse({ status: 401, ok: false }),
    classifyNoticesResponse({ status: 200, ok: true }), classifyNoticesResponse({ status: 500, ok: false })],
}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_notifications_route_helpers():
    result = subprocess.run(["node", "--experimental-strip-types", "--input-type=module", "-e", SCRIPT],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["parsed"][0]["read"] is False and out["parsed"][1]["read"] is False
    assert out["bad"] == [None, None, None]
    assert out["capped"] == 50
    assert out["hrefs"] == ["/demonstration/1#x", None, None, None, None, None, "/a?b=1"]
    assert out["kinds"] == ["signed-out", "signed-out", "ok", "unavailable"]


def test_routing_claims_only_the_notifications_page():
    routing = (ROOT / "deploy/apache/react-ssr-routing.conf").read_text(encoding="utf-8")
    assert 'RewriteRule "^/api/notifications/all$" "http://127.0.0.1:8090$0" [P,L]' in routing
    assert "^/api/notifications/$" not in routing and "notifications/mark-read" not in routing


def test_notification_list_limit_is_capped(user_client):
    assert user_client.get("/api/notifications/?limit=500").status_code == 200
    assert user_client.get("/api/notifications/?limit=abc").status_code == 200
