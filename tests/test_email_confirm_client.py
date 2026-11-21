"""The React e-mail confirmation page's request helper, run with Node's built-in TypeScript support."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = """
import { confirmEmail, emailConfirmPath, emailConfirmRoutePattern } from './frontend/src/app/email-confirm.ts';
const token = 'abcDEF123_-.abcDEF123_-.abcDEF123_-';
const calls = [];
const reply = (status, body) => async (url, init) => { calls.push([url, init]); return { ok: status < 300, status, json: async () => body }; };
const out = {
  ok: await confirmEmail(token, reply(200, { success: true, status: 'confirmed' })),
  invalid: await confirmEmail(token, reply(400, { success: false, status: 'invalid' })),
  notFound: await confirmEmail(token, reply(404, { success: false, status: 'user-not-found' })),
  rate: await confirmEmail(token, reply(429, { success: false })),
  unavailable: await confirmEmail(token, reply(503, { success: false, error_code: 'EMAIL_CONFIRM_UNAVAILABLE' })),
  // A route that is not Go's (the unrouted 404 page) is not a verdict about the account.
  plain404: await confirmEmail(token, reply(404, { error: 'not found' })),
  okWithoutStatus: await confirmEmail(token, reply(200, { success: true })),
  wrongPair: await confirmEmail(token, reply(200, { status: 'invalid' })),
  htmlBody: await confirmEmail(token, async () => ({ ok: true, status: 200, json: async () => { throw new Error('html'); } })),
  network: await confirmEmail(token, async () => { throw new Error('offline'); }),
  noDots: await confirmEmail('abcdefgh', reply(200, { status: 'confirmed' })),
  spaces: await confirmEmail('a b.c.d', reply(200, { status: 'confirmed' })),
  callCount: calls.length,
  first: [calls[0][0], calls[0][1].method, calls[0][1].credentials, calls[0][1].body],
  route: ['aaaaa.bbbbb.ccccc', 'a b', 'ab', '/x'].map(value => emailConfirmRoutePattern.test(value)),
  path: emailConfirmPath,
};
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is required")
def test_email_confirm_request_helper():
    result = subprocess.run(["node", "--experimental-strip-types", "--input-type=module", "-e", SCRIPT],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["ok"] == "confirmed" and out["invalid"] == "invalid" and out["notFound"] == "user-not-found"
    assert out["rate"] == "rate-limited"
    assert out["unavailable"] == out["plain404"] == out["okWithoutStatus"] == out["wrongPair"] == "error"
    assert out["htmlBody"] == out["network"] == "error"
    # A malformed token never leaves the browser.
    assert out["noDots"] == out["spaces"] == "invalid"
    assert out["callCount"] == 8
    assert out["first"][:3] == ["/api/account/confirm-email", "POST", "omit"]
    assert json.loads(out["first"][3]) == {"token": "abcDEF123_-.abcDEF123_-.abcDEF123_-"}
    assert out["route"] == [True, False, False, False]
