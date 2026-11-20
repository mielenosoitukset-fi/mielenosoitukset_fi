"""The React volunteer confirmation page's request helper, run with Node's built-in TypeScript support."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = """
import { confirmVolunteer, volunteerConfirmPath } from './frontend/src/app/volunteer-confirm.ts';
const token = 'abcDEF123_-abcDEF123_-';
const calls = [];
const reply = (status, body) => async (url, init) => { calls.push([url, init]); return { ok: status < 300, status, json: async () => body }; };
const out = {
  ok: await confirmVolunteer(token, reply(200, { success: true, status: 'success' })),
  invalid: await confirmVolunteer(token, reply(400, { success: false, status: 'invalid' })),
  expired: await confirmVolunteer(token, reply(400, { success: false, status: 'expired' })),
  rate: await confirmVolunteer(token, reply(429, { success: false, error_code: 'VOLUNTEER_RATE_LIMITED' })),
  unavailable: await confirmVolunteer(token, reply(503, { success: false, error_code: 'VOLUNTEER_UNAVAILABLE' })),
  forbidden: await confirmVolunteer(token, reply(403, { success: false })),
  // A 200 that is not the success verdict, or a 400 with an unknown status, never reads as success or a bad link.
  okWithoutStatus: await confirmVolunteer(token, reply(200, { success: true })),
  strange400: await confirmVolunteer(token, reply(400, { status: 'whatever' })),
  htmlBody: await confirmVolunteer(token, async () => ({ ok: true, status: 200, json: async () => { throw new Error('html'); } })),
  network: await confirmVolunteer(token, async () => { throw new Error('offline'); }),
  badToken: await confirmVolunteer('bad token!', reply(200, { status: 'success' })),
  emptyToken: await confirmVolunteer('', reply(200, { status: 'success' })),
  callCount: calls.length,
  first: [calls[0][0], calls[0][1].method, calls[0][1].credentials, calls[0][1].body, calls[0][1].referrerPolicy],
  path: volunteerConfirmPath,
};
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is required")
def test_volunteer_confirm_request_helper():
    result = subprocess.run(["node", "--experimental-strip-types", "--input-type=module", "-e", SCRIPT],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["ok"] == "success"
    assert (out["invalid"], out["expired"]) == ("invalid", "expired")
    assert out["rate"] == "rate-limited"
    assert out["unavailable"] == out["forbidden"] == out["okWithoutStatus"] == out["strange400"] == "error"
    assert out["htmlBody"] == out["network"] == "error"
    # A malformed token never leaves the browser.
    assert out["badToken"] == out["emptyToken"] == "invalid"
    assert out["callCount"] == 8  # the two malformed tokens made no request
    assert out["first"] == ["/api/campaign-volunteers/confirm", "POST", "omit", '{"token":"abcDEF123_-abcDEF123_-"}', "no-referrer"]
