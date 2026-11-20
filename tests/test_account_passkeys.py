"""The React passkey helpers, run with Node's built-in TypeScript support."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = """
import { decodeCreationOptions, formatRegistrationCredential, passkeyDeleteRequest, passkeyRenameRequest, readPasskeyResult, registerPasskey } from './frontend/src/app/account-passkeys.ts';
const res = (status, body) => new Response(typeof body === 'string' ? body : JSON.stringify(body), { status });
const calls = [];
const scripted = (answers) => async (url, init) => { calls.push([url, init.method, init.credentials, init.body]); return answers.shift(); };
const opts = { status: 'success', options: { challenge: 'AQID', user: { id: 'BAUG', name: 'a' }, excludeCredentials: [{ id: 'AQID', type: 'public-key' }] } };
const fake = { id: 'cred', rawId: new Uint8Array([1, 2, 3]).buffer, type: 'public-key',
  response: { clientDataJSON: new Uint8Array([251, 255]).buffer, attestationObject: new Uint8Array([1]).buffer, getTransports: () => ['usb'] } };
const stepOk = (fetcher) => async (request) => request();
const dec = decodeCreationOptions(opts.options);
const sent = [];
const okRun = await registerPasskey('Phone', async (r) => r(), scripted([res(200, opts), res(200, { status: 'success' })]), async (o) => { sent.push(Array.from(new Uint8Array(o.publicKey.challenge))); return fake; });
const okCalls = calls.slice(); calls.length = 0;
const cancelled = await registerPasskey('x', async () => null, scripted([]), async () => fake);
const optionsBad = await registerPasskey('x', async (r) => r(), scripted([res(403, { status: 'error', message: 'Vaatii vahvistuksen.' })]), async () => fake);
const noCred = await registerPasskey('x', async (r) => r(), scripted([res(200, opts)]), async () => null);
const denied = await registerPasskey('x', async (r) => r(), scripted([res(200, opts)]), async () => { throw new Error('NotAllowedError'); });
const verifyBad = await registerPasskey('x', async (r) => r(), scripted([res(200, opts), res(400, { status: 'error', message: 'Vahvistus epäonnistui.' })]), async () => fake);
calls.length = 0;
await passkeyRenameRequest('abc', 'Key', scripted([res(200, {})]))();
await passkeyDeleteRequest('abc', scripted([res(200, {})]))();
const out = {
  dec: [Array.from(new Uint8Array(dec.challenge)), Array.from(new Uint8Array(dec.user.id)), dec.user.name, Array.from(new Uint8Array(dec.excludeCredentials[0].id))],
  fmt: formatRegistrationCredential(fake),
  okRun, sent, okCalls, cancelled, optionsBad, noCred, denied, verifyBad,
  result: [await readPasskeyResult(res(200, { status: 'success', message: 'Passkey poistettu.' }), 'fb'), await readPasskeyResult(res(404, { status: 'error', message: 'Passkeyta ei löytynyt.' }), 'fb'),
    await readPasskeyResult(res(500, 'html'), 'fb'), await readPasskeyResult(null, 'fb')],
  calls,
};
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is required")
def test_passkey_helpers():
    result = subprocess.run(["node", "--experimental-strip-types", "--input-type=module", "-e", SCRIPT],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["dec"] == [[1, 2, 3], [4, 5, 6], "a", [1, 2, 3]]
    assert out["fmt"] == {"id": "cred", "rawId": "AQID", "type": "public-key",
                          "response": {"clientDataJSON": "-_8", "attestationObject": "AQ", "transports": ["usb"]}}
    assert out["okRun"] == {"ok": True, "message": "Passkey lisätty onnistuneesti."}
    assert out["sent"] == [[1, 2, 3]]
    options, verify = out["okCalls"]
    assert options[0] == "/users/auth/api/v2/passkeys/register/options" and options[1] == "POST" and options[2] == "same-origin"
    assert verify[0] == "/users/auth/api/v2/passkeys/register/verify"
    assert json.loads(verify[3])["name"] == "Phone" and json.loads(verify[3])["credential"]["id"] == "cred"
    assert out["cancelled"] is None
    assert out["optionsBad"] == {"ok": False, "message": "Vaatii vahvistuksen."}
    assert out["noCred"]["ok"] is False and out["denied"]["ok"] is False
    assert out["verifyBad"] == {"ok": False, "message": "Vahvistus epäonnistui."}
    assert out["result"] == [{"ok": True, "message": "Passkey poistettu."}, {"ok": False, "message": "Passkeyta ei löytynyt."},
                             {"ok": False, "message": "fb"}, None]
    rename, delete = out["calls"]
    assert rename[0] == "/users/auth/api/v2/passkeys/rename" and json.loads(rename[3]) == {"id": "abc", "name": "Key"}
    assert delete[0] == "/users/auth/api/v2/passkeys/delete" and json.loads(delete[3]) == {"id": "abc"}
