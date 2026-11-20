"""The React contact page's notices for the Go redirect flags, run with Node's built-in TypeScript support."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = """
import { contactNoticeFromSearch as read } from './frontend/src/app/contact-route.ts';
console.log(JSON.stringify(['sent', 'invalid', 'disabled', 'nope', 'constructor', '__proto__', 'toString', ''].map((f) => read('?contact=' + f))
  .concat([read(''), read('?suggestion=sent')])));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is required")
def test_contact_flags_map_to_notices_and_unknown_flags_show_nothing():
    result = subprocess.run(["node", "--experimental-strip-types", "--input-type=module", "-e", SCRIPT],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    sent, invalid, disabled, *rest = json.loads(result.stdout.strip().splitlines()[-1])
    assert sent["category"] == "success" and invalid["category"] == "error" and disabled["category"] == "warning"
    assert "!" not in sent["message"] + invalid["message"] + disabled["message"]
    assert rest == [None] * 7  # unknown, prototype names, empty, no query, another page's flag
