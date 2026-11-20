"""The React settings page follows Go's identity-change refusal only to the fixed Flask path."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = """
import { isIdentityChangeRefusal, accountIdentityChangePath } from './frontend/src/app/account-settings-route.ts';
const reply = (status, body, fail = false) => ({ status, json: async () => { if (fail) throw new Error('not json'); return body; } });
const flagged = { error: 'identity change required', redirect: accountIdentityChangePath };
console.log(JSON.stringify({
  path: accountIdentityChangePath,
  flagged: await isIdentityChangeRefusal(reply(403, flagged)),
  wrongStatus: await isIdentityChangeRefusal(reply(200, flagged)),
  otherRedirect: await isIdentityChangeRefusal(reply(403, { ...flagged, redirect: 'https://evil.example/' })),
  otherError: await isIdentityChangeRefusal(reply(403, { error: 'forbidden', redirect: accountIdentityChangePath })),
  notJson: await isIdentityChangeRefusal(reply(403, null, true)),
  nullBody: await isIdentityChangeRefusal(reply(403, null)),
}));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is required")
def test_only_the_exact_refusal_triggers_the_redirect():
    result = subprocess.run(["node", "--experimental-strip-types", "--input-type=module", "-e", SCRIPT],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["path"] == "/users/auth/forced_identity_change/"
    assert out["flagged"] is True
    assert not any(out[key] for key in ("wrongStatus", "otherRedirect", "otherError", "notJson", "nullBody"))
