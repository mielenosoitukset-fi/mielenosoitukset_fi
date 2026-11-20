"""The React MFA helpers, run with Node's built-in TypeScript support."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = """
import { cleanDeviceName, cleanMfaCode, mfaRenameRequest, mfaRevokeRequest, mfaStartRequest, mfaVerifyRequest, readMfaResult, readMfaStart } from './frontend/src/app/account-mfa.ts';
const res = (status, body) => new Response(typeof body === 'string' ? body : JSON.stringify(body), { status });
const calls = [];
const scripted = (answers) => async (url, init) => { calls.push([url, init.method, init.credentials, init.headers['Content-Type'], init.body]); return answers.shift(); };
const png = 'data:image/png;base64,iVBORw0KGgo=';
const secret = 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP';
await mfaStartRequest(scripted([res(200, {})]));
await mfaVerifyRequest('123456', secret, 'Phone', scripted([res(200, {})]));
await mfaRenameRequest('abc', 'Key', scripted([res(200, {})]))();
await mfaRevokeRequest('abc', scripted([res(200, {})]))();
const out = {
  start: [readMfaStart({ status: 'pending', qr_code: png, secret }), readMfaStart({ status: 'pending', qr_code: 'data:image/svg+xml;base64,AAAA', secret }),
    readMfaStart({ status: 'pending', qr_code: 'javascript:alert(1)', secret }), readMfaStart({ status: 'pending', qr_code: png, secret: 'not base32!' }),
    readMfaStart({ status: 'error', qr_code: png, secret }), readMfaStart(null), readMfaStart({ status: 'pending', qr_code: png + '"onerror="x', secret })],
  code: [cleanMfaCode('123456'), cleanMfaCode(' 123 456 '), cleanMfaCode('12345'), cleanMfaCode('abcdef'), cleanMfaCode('1234567'), cleanMfaCode('')],
  name: [cleanDeviceName('  Desk key  '), cleanDeviceName('   '), cleanDeviceName('x'.repeat(80)).length, cleanDeviceName('😀'.repeat(70)).length / 2],
  result: [await readMfaResult(res(200, { status: 'success', message: 'Laite poistettu.' }), 'fb'), await readMfaResult(res(404, { status: 'error', message: 'Laitetta ei löytynyt.' }), 'fb'),
    await readMfaResult(res(500, 'html'), 'fb'), await readMfaResult(res(200, { status: 'error' }), 'fb'), await readMfaResult(null, 'fb')],
  calls,
};
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is required")
def test_mfa_helpers():
    result = subprocess.run(["node", "--experimental-strip-types", "--input-type=module", "-e", SCRIPT],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    out = json.loads(result.stdout.strip().splitlines()[-1])
    secret = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
    png = "data:image/png;base64,iVBORw0KGgo="
    assert out["start"] == [{"qr": png, "secret": secret}, None, None, None, None, None, None]
    assert out["code"] == ["123456", "123456", None, None, None, None]
    assert out["name"] == ["Desk key", None, 60, 60]
    assert out["result"][0] == {"ok": True, "message": "Laite poistettu.", "body": {"status": "success", "message": "Laite poistettu."}}
    assert out["result"][1]["ok"] is False and out["result"][1]["message"] == "Laitetta ei löytynyt."
    assert out["result"][2] == {"ok": False, "message": "fb", "body": {}}
    assert out["result"][3]["ok"] is False and out["result"][3]["message"] == "fb"
    assert out["result"][4] is None
    start, verify, rename, revoke = out["calls"]
    assert start == ["/users/auth/api/v2/mfa", "POST", "same-origin", "application/json", '{"step":"request_activation"}']
    assert json.loads(verify[4]) == {"step": "verify_code", "code": "123456", "secret": secret, "device_name": "Phone"}
    assert rename[0] == "/users/auth/api/v2/mfa_device_rename" and json.loads(rename[4]) == {"device_id": "abc", "name": "Key"}
    assert revoke[0] == "/users/auth/api/v2/mfa_device_revoke" and json.loads(revoke[4]) == {"device_id": "abc"}
