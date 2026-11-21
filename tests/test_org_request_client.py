"""The organization request pages' helpers, run with Node's built-in TypeScript support."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = """
import { splitLinks, orgRequestBody, statusToken, readStatus, orgRecoveryBody } from './frontend/src/app/org-request.ts';
const reply = (status, body) => async (url, init) => { calls.push([url, init]); return { ok: status < 300, status, json: async () => body }; };
const calls = [];
const form = entries => { const f = new FormData(); for (const [k, v] of entries) f.append(k, v); return f; };
const request = { id: '1', organization: 'Rauha', represents: true, onboarding: false, status: 'received', created_at: 'x', updated_at: 'x' };
const out = {
  links: splitLinks('https://a.fi\\n b.fi, c.fi d.fi e.fi f.fi'),
  represent: orgRequestBody(form([['represents', 'yes'], ['access', 'invite'], ['invite_email', 'pj@a.fi'], ['links', 'a.fi'], ['consent', 'on'], ['applicant_role', 'pj']]), 5),
  suggest: orgRequestBody(form([['represents', 'no'], ['access', 'invite'], ['invite_email', 'pj@a.fi'], ['applicant_role', 'pj'], ['onboarding', 'on']]), 0),
  ownAccess: orgRequestBody(form([['represents', 'yes'], ['access', 'own_account'], ['invite_email', 'x@a.fi']]), 0),
  lost: orgRecoveryBody(form([['need', 'lost_access'], ['person_email', 'x@a.fi'], ['person_role', 'admin'], ['links', 'a.fi'], ['consent', 'on']]), 7),
  member: orgRecoveryBody(form([['need', 'add_member'], ['person_email', 'x@a.fi'], ['person_role', 'admin']]), 0),
  token: statusToken('#abcdefghijklmnopqrstuv'),
  noHash: statusToken(''),
  shortToken: statusToken('#abc'),
  badToken: statusToken('#abcdefghijklmnop/../x'),
  ready: await readStatus('tok', reply(200, { success: true, request })),
  missing: await readStatus('tok', reply(404, { success: false, message: 'x' })),
  limited: await readStatus('tok', reply(429, { success: false, message: 'Liian monta yritystä.' })),
  html: await readStatus('tok', async () => ({ ok: true, status: 200, json: async () => { throw new Error('html'); } })),
  network: await readStatus('tok', async () => { throw new Error('offline'); }),
  init: [calls[0][0], calls[0][1].method, calls[0][1].credentials, calls[0][1].body],
};
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is required")
def test_org_request_helpers():
    result = subprocess.run(["node", "--experimental-strip-types", "--input-type=module", "-e", SCRIPT],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["links"] == ["https://a.fi", "b.fi", "c.fi", "d.fi", "e.fi"]
    assert out["represent"]["represents"] is True and out["represent"]["access"] == "invite"
    assert out["represent"]["invite_email"] == "pj@a.fi" and out["represent"]["consent"] is True
    assert out["represent"]["started_at"] == 5 and out["represent"]["homepage"] == ""
    # A suggestion sends no access choice, invite address or role.
    assert out["suggest"]["represents"] is False and out["suggest"]["access"] == ""
    assert out["suggest"]["invite_email"] == "" and out["suggest"]["applicant_role"] == ""
    assert out["suggest"]["onboarding"] is True and out["suggest"]["consent"] is False
    assert out["ownAccess"]["invite_email"] == ""
    assert out["token"] == "abcdefghijklmnopqrstuv"
    assert out["noHash"] == out["shortToken"] == out["badToken"] == ""
    assert out["ready"]["kind"] == "ready" and out["ready"]["request"]["organization"] == "Rauha"
    assert out["missing"] == {"kind": "missing"}
    assert out["limited"] == {"kind": "error", "message": "Liian monta yritystä."}
    assert out["html"]["kind"] == out["network"]["kind"] == "error"
    assert out["init"] == ["/organisaatiot/hakemus", "POST", "same-origin", '{"token":"tok"}']
    # Lost access names no one else and an owner change no role; only an added member has both.
    assert out["lost"]["need"] == "lost_access" and out["lost"]["person_email"] == "" and out["lost"]["person_role"] == ""
    assert out["lost"]["links"] == ["a.fi"] and out["lost"]["consent"] is True and out["lost"]["started_at"] == 7
    assert out["member"]["person_email"] == "x@a.fi" and out["member"]["person_role"] == "admin"
