"""The React step-up helper and the password change helpers, run with Node's built-in TypeScript support."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = """
import { isStepUpRequired, needsStepUp, readStepUpMethods, passwordStepUpPayload, runWithStepUp, base64UrlToBuffer, bufferToBase64Url,
  decodePublicKeyOptions, stepUpWithPassword, stepUpWithPasskey } from './frontend/src/app/account-stepup.ts';
import { passwordFormError, passwordPayload, passwordRequest, readPasswordResult } from './frontend/src/app/account-settings-route.ts';
const res = (status, body) => new Response(typeof body === 'string' ? body : JSON.stringify(body), { status });
const calls = [];
const scripted = (answers) => async (url, init) => { calls.push([url, init.method, init.headers['Content-Type'], init.body]); return answers.shift(); };
const stepUp = { error: 'step_up_required', message: 'x' };
let asked = 0;
const sent = [];
const req = (answers) => () => { sent.push(1); return Promise.resolve(answers.shift()); };
const retried = await runWithStepUp(req([res(403, stepUp), res(200, { status: 'success' })]), async () => { asked++; return true; });
const sentRetried = sent.length; sent.length = 0;
const cancelled = await runWithStepUp(req([res(403, stepUp)]), async () => false);
const sentCancelled = sent.length; sent.length = 0;
const plain403 = await runWithStepUp(req([res(403, 'Forbidden')]), async () => { asked += 10; return true; });
const ok = await runWithStepUp(req([res(200, { status: 'success' })]), async () => { asked += 100; return true; });
const bytes = new Uint8Array([251, 255, 254, 0, 1]);
const opts = decodePublicKeyOptions({ challenge: 'AQID', allowCredentials: [{ id: 'AQID', type: 'public-key' }] });
const pw = { current: 'old-password-1', next: 'Abcdef123456!', confirm: 'Abcdef123456!' };
await passwordRequest(pw, scripted([res(200, {})]))();
const pwCall = calls.slice();
const out = {
  detect: [isStepUpRequired(403, stepUp), isStepUpRequired(403, { error: 'other' }), isStepUpRequired(401, stepUp), isStepUpRequired(403, null), isStepUpRequired(403, [stepUp])],
  needs: [await needsStepUp(res(403, stepUp)), await needsStepUp(res(403, 'html')), await needsStepUp(res(200, stepUp))],
  methods: [readStepUpMethods({ methods: [{ type: 'webauthn' }, { type: 'password_totp', requires_totp: true }] }, true),
    readStepUpMethods({ methods: [{ type: 'webauthn' }, { type: 'password_totp', requires_totp: false }] }, false), readStepUpMethods('x', true)],
  payload: [passwordStepUpPayload('p', ' '), passwordStepUpPayload('p', ' 123456 ')],
  flow: { retriedStatus: retried?.status, sentRetried, cancelled, sentCancelled, plain403: plain403?.status, ok: ok?.status, asked },
  b64: [bufferToBase64Url(bytes.buffer), Array.from(new Uint8Array(base64UrlToBuffer(bufferToBase64Url(bytes.buffer))))],
  opts: [Array.from(new Uint8Array(opts.challenge)), Array.from(new Uint8Array(opts.allowCredentials[0].id)), opts.allowCredentials[0].type],
  pwOk: await stepUpWithPassword('p', '', scripted([res(200, { status: 'success' })])),
  pwBad: await stepUpWithPassword('p', '', scripted([res(403, { status: 'error', message: 'Väärä salasana.' })])),
  pwHtml: await stepUpWithPassword('p', '', scripted([res(500, 'x')])),
  passkeyBad: await stepUpWithPasskey(scripted([res(200, { status: 'error', message: 'Ei avainta.' })]), async () => { throw new Error('no'); }),
  passkeyThrows: await stepUpWithPasskey(scripted([res(200, { status: 'success', options: { challenge: 'AQID' } })]), async () => { throw new Error('denied'); }),
  form: [passwordFormError({ ...pw, current: '' }), passwordFormError({ ...pw, confirm: 'x' }), passwordFormError({ current: 'a', next: 'short', confirm: 'short' }), passwordFormError(pw)],
  body: passwordPayload(pw),
  result: [await readPasswordResult(res(200, { status: 'success', message: 'Salasana vaihdettu.' }), 'fb'), await readPasswordResult(res(400, { status: 'error', message: 'Nykyinen salasana on väärä.' }), 'fb'),
    await readPasswordResult(res(500, 'html'), 'fb'), await readPasswordResult(null, 'fb')],
  calls: pwCall,
};
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is required")
def test_stepup_and_password_helpers():
    result = subprocess.run(["node", "--experimental-strip-types", "--input-type=module", "-e", SCRIPT],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["detect"] == [True, False, False, False, False]
    assert out["needs"] == [True, False, False]
    assert out["methods"][0] == {"passkey": True, "password": True, "requiresTotp": True}
    assert out["methods"][1] == {"passkey": False, "password": True, "requiresTotp": False}
    assert out["methods"][2] == {"passkey": False, "password": True, "requiresTotp": False}
    assert out["payload"] == [{"password": "p"}, {"password": "p", "totp_code": "123456"}]
    flow = out["flow"]
    assert flow["retriedStatus"] == 200 and flow["sentRetried"] == 2  # sent once, then once more after step-up
    assert flow["cancelled"] is None and flow["sentCancelled"] == 1  # cancel: no retry
    assert flow["plain403"] == 403 and flow["ok"] == 200 and flow["asked"] == 1  # only the step-up answer asks
    assert out["b64"][0] == "-__-AAE" and out["b64"][1] == [251, 255, 254, 0, 1]
    assert out["opts"] == [[1, 2, 3], [1, 2, 3], "public-key"]
    assert out["pwOk"] == {"ok": True}
    assert out["pwBad"] == {"ok": False, "message": "Väärä salasana."}
    assert out["pwHtml"]["ok"] is False and out["pwHtml"]["message"] == "Virheellinen salasana"
    assert out["passkeyBad"] == {"ok": False, "message": "Ei avainta."}
    assert out["passkeyThrows"] == {"ok": False, "message": "Passkey-tunnistautuminen epäonnistui. Yritä salasanalla."}
    assert out["form"] == ["Kaikki kentät ovat pakollisia.", "Salasanat eivät täsmää.", "Salasana ei täytä vaatimuksia.", None]
    assert out["body"] == {"current": "old-password-1", "new": "Abcdef123456!", "confirm": "Abcdef123456!"}
    assert out["result"][0] == {"ok": True, "message": "Salasana vaihdettu."}
    assert out["result"][1] == {"ok": False, "message": "Nykyinen salasana on väärä."}
    assert out["result"][2] == {"ok": False, "message": "fb"} and out["result"][3] is None
    url, method, ctype, body = out["calls"][0]
    assert (url, method, ctype) == ("/users/auth/api/v2/change_password", "POST", "application/json")
    assert json.loads(body) == out["body"]
