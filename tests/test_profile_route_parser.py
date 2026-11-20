"""The React profile page's response parser, run with Node's built-in TypeScript support."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = """
import { readProfile, classifyProfileResponse, profileActionMode } from './frontend/src/app/profile-route.ts';
const good = { username: 'maija', displayname: 'Maija', profile_picture: null, bio: null, admin_badge: false,
  posts_count: 0, followers: [{ username: 'a', displayname: 'A', profile_picture: 'javascript:x' }], following: [],
  followers_count: 1, following_count: 0, self: false, viewer: { following: true, friend_state: 'friends' } };
const out = {
  ok: readProfile(good, 'maija')?.followers[0].picture,
  mismatch: readProfile(good, 'other'),
  noSelf: readProfile({ ...good, self: undefined }),
  badState: readProfile({ ...good, viewer: { following: true, friend_state: 'x' } }),
  selfNeedsLists: readProfile({ ...good, self: true }),
  badBody: readProfile('x'),
  notFound: classifyProfileResponse(404, 'application/json; charset=utf-8'),
  flask404: classifyProfileResponse(404, 'text/html'),
  down: classifyProfileResponse(503, 'application/json'),
  anon: profileActionMode(readProfile(good), { authenticated: false }),
  unknownSignedIn: profileActionMode(readProfile({ ...good, viewer: null }), { authenticated: true }),
};
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is required")
def test_profile_parser_rejects_malformed_bodies():
    result = subprocess.run(["node", "--experimental-strip-types", "--input-type=module", "-e", SCRIPT],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["ok"] is None  # a javascript: picture is dropped
    assert out["mismatch"] is None and out["noSelf"] is None and out["badState"] is None
    assert out["selfNeedsLists"] is None and out["badBody"] is None
    assert (out["notFound"], out["flask404"], out["down"]) == ("not-found", "unavailable", "unavailable")
    assert out["anon"] == "viewer"  # viewer present in the body
    assert out["unknownSignedIn"] == "none"
