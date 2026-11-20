"""The React invitation page's request helpers, run with Node's built-in TypeScript support."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = """
import { loadInvitation, acceptInvitation, loginUrlFor, invitationUrl } from './frontend/src/app/org-invite.ts';
const org = 'abcdefabcdefabcdefabcdef';
const calls = [];
const reply = (status, body) => async (url, init) => { calls.push([url, init]); return { ok: status < 300, status, json: async () => body }; };
const html = status => async () => ({ ok: status < 300, status, json: async () => { throw new Error('html'); } });
const out = {
  invited: await loadInvitation(org, reply(200, { state: 'invited', csrf: 'tok', organization: { name: 'Rauha' } })),
  invitedNoCsrf: await loadInvitation(org, reply(200, { state: 'invited', organization: { name: 'Rauha' } })),
  invitedNoName: await loadInvitation(org, reply(200, { state: 'invited', csrf: 'tok' })),
  notInvited: await loadInvitation(org, reply(200, { state: 'not-invited' })),
  noOrg: await loadInvitation(org, reply(200, { state: 'organization-not-found' })),
  protectedState: await loadInvitation(org, reply(200, { state: 'protected' })),
  signedOut: await loadInvitation(org, reply(401, { error: 'authentication required' })),
  down: await loadInvitation(org, reply(503, { error: 'service unavailable' })),
  forbidden: await loadInvitation(org, reply(403, { error: 'forbidden' })),
  unknownState: await loadInvitation(org, reply(200, { state: 'joined' })),
  htmlBody: await loadInvitation(org, html(200)),
  network: await loadInvitation(org, async () => { throw new Error('offline'); }),
  badId: await loadInvitation('nope', reply(200, { state: 'invited', csrf: 't', organization: { name: 'x' } })),
  readCalls: calls.length,
  readInit: [calls[0][0], calls[0][1].method, calls[0][1].credentials, calls[0][1].body ?? null],
};
calls.length = 0;
Object.assign(out, {
  joined: await acceptInvitation(org, 'tok', reply(200, { state: 'joined', success: true, organization: { name: 'Rauha' } })),
  joinedNoSuccess: await acceptInvitation(org, 'tok', reply(200, { state: 'joined' })),
  lost: await acceptInvitation(org, 'tok', reply(200, { state: 'not-invited', success: false })),
  protectedAnswer: await acceptInvitation(org, 'tok', reply(403, { state: 'protected', success: false })),
  staleToken: await acceptInvitation(org, 'tok', reply(403, { error: 'invalid csrf token' })),
  aSignedOut: await acceptInvitation(org, 'tok', reply(401, {})),
  rate: await acceptInvitation(org, 'tok', reply(429, {})),
  aDown: await acceptInvitation(org, 'tok', reply(503, {})),
  aNetwork: await acceptInvitation(org, 'tok', async () => { throw new Error('offline'); }),
  noToken: await acceptInvitation(org, '', reply(200, { state: 'joined', success: true })),
  badId2: await acceptInvitation('../x', 'tok', reply(200, { state: 'joined', success: true })),
  acceptCalls: calls.length,
  acceptInit: [calls[0][0], calls[0][1].method, calls[0][1].credentials, calls[0][1].headers['X-CSRF-Token'], calls[0][1].body],
  login: loginUrlFor('/users/auth/login', org),
  url: invitationUrl(org),
});
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is required")
def test_org_invite_request_helpers():
    result = subprocess.run(["node", "--experimental-strip-types", "--input-type=module", "-e", SCRIPT],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    out = json.loads(result.stdout.strip().splitlines()[-1])
    org = "abcdefabcdefabcdefabcdef"
    assert out["invited"] == {"kind": "invited", "name": "Rauha", "csrf": "tok"}
    assert out["invitedNoCsrf"] == out["invitedNoName"] == out["unknownState"] == out["htmlBody"] == out["network"] == {"kind": "unavailable"}
    assert out["down"] == out["forbidden"] == {"kind": "unavailable"}
    assert out["notInvited"] == {"kind": "not-invited"} and out["noOrg"] == {"kind": "organization-not-found"}
    assert out["protectedState"] == {"kind": "protected"} and out["signedOut"] == {"kind": "signed-out"}
    assert out["badId"] == {"kind": "organization-not-found"} and out["readCalls"] == 10
    assert out["readInit"] == [f"/api/organizations/{org}/invitation", "GET", "same-origin", None]
    assert out["joined"] == {"kind": "joined", "name": "Rauha"}
    # Only a full joined answer counts; a bare state never does.
    assert out["joinedNoSuccess"] == {"kind": "error"}
    assert out["lost"] == {"kind": "not-invited"} and out["protectedAnswer"] == {"kind": "protected"}
    assert out["staleToken"] == {"kind": "session"} and out["aSignedOut"] == {"kind": "signed-out"}
    assert out["rate"] == {"kind": "rate-limited"}
    assert out["aDown"] == out["aNetwork"] == {"kind": "error"}
    assert out["noToken"] == out["badId2"] == {"kind": "error"}
    assert out["acceptCalls"] == 8
    assert out["acceptInit"] == [f"/api/organizations/{org}/invitation/accept", "POST", "same-origin", "tok", "{}"]
    assert out["login"] == "/users/auth/login?next=%2Fusers%2Forgs%2Faccept_invite%3Forganization_id%3D" + org
