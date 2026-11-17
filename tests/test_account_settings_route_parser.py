"""The React settings page's response parser and helpers, run with Node's built-in TypeScript support."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = """
import { readAccountSettings, classifyAccountResponse, tabFromHash, nextTabIndex, generalPayload, filterCities, postFlaskJson, postBioToGo }
  from './frontend/src/app/account-settings-route.ts';
const id = 'abcdefabcdefabcdefabcdef';
const good = { general: { display_name: 'Maija', city: null, language: 'fi', dark_mode: null }, profile: { bio: null, profile_picture: 'javascript:x' },
  mfa: { enabled: true, devices: [{ id, name: 'Puhelin', created_at: '2026-01-02T03:04:05Z' }, { id: 'bad', name: 'x' }] },
  passkeys: [{ id, name: 'Avain', transports: ['usb', 5] }], login_history: [{ time: 't', ip: '1.2.3.4', ua: 'Firefox', ok: false, reason: '' }],
  cities: ['Helsinki', 5, ''] };
const parsed = readAccountSettings(good);
const answer = (status, body) => async () => ({ ok: status < 300, status, json: async () => body });
const goCalls = [];
const goAnswer = (status, body) => async (url, init) => { goCalls.push([url, init.method, init.headers['X-CSRF-Token'], init.body]); return { ok: status < 300, status, json: async () => body }; };
const token = 'a'.repeat(30) + '.' + 'b'.repeat(10);
const withToken = readAccountSettings({ ...good, writes: true, csrf: token });
const out = {
  csrf: [parsed?.csrf, withToken?.csrf, readAccountSettings({ ...good, writes: true, csrf: 'short' })?.csrf, readAccountSettings({ ...good, csrf: token })?.csrf],
  goOk: await postBioToGo(token, 'Hei', 'fb', goAnswer(200, { status: 'success', bio: 'Hei' })),
  goCalls,
  go400: await postBioToGo(token, 'x', 'fb', goAnswer(400, { error: 'Esittely on liian pitkä' })),
  go401: (await postBioToGo(token, 'x', 'fb', goAnswer(401, { error: 'unauthorized' }))).message,
  go403: (await postBioToGo(token, 'x', 'fb', goAnswer(403, { error: 'invalid csrf token' }))).message,
  go403plain: (await postBioToGo(token, 'x', 'fb', goAnswer(403, { error: 'forbidden' }))).message,
  go429: (await postBioToGo(token, 'x', 'fb', goAnswer(429, { error: 'too many requests' }))).message,
  go503: (await postBioToGo(token, 'x', 'fb', goAnswer(503, { error: 'service unavailable' }))).message,
  picture: parsed?.picture, devices: parsed?.mfa.devices.length, cities: parsed?.cities, transports: parsed?.passkeys[0].transports,
  badBody: readAccountSettings('x'), noMfa: readAccountSettings({ ...good, mfa: undefined }),
  badDark: readAccountSettings({ ...good, general: { ...good.general, dark_mode: 'yes' } }),
  badLogins: readAccountSettings({ ...good, login_history: 'x' }),
  kinds: [classifyAccountResponse(401), classifyAccountResponse(200), classifyAccountResponse(404), classifyAccountResponse(503)],
  hash: [tabFromHash('#tab-security'), tabFromHash('#nope'), tabFromHash('')],
  keys: [nextTabIndex('ArrowRight', 3, 4), nextTabIndex('ArrowRight', 0, 4), nextTabIndex('ArrowLeft', 0, 4), nextTabIndex('End', 0, 4), nextTabIndex('x', 0, 4)],
  payload: [generalPayload({ displayName: 'A', city: '', language: 'fi', darkMode: true }), generalPayload({ displayName: 'A', city: 'Oulu', language: 'fi', darkMode: false })],
  filter: filterCities(['Äänekoski', 'Oulu', 'Espoo'], 'ä'),
  flaskError: await postFlaskJson('/x', {}, 'fallback', answer(400, { status: 'error', message: 'Admin-nimitys on varattu palvelun sisäiseen käyttöön.' })),
  flaskHtml: await postFlaskJson('/x', {}, 'fallback', async () => ({ ok: true, status: 200, json: async () => { throw new Error('html'); } })),
  flaskOk: (await postFlaskJson('/x', {}, 'fallback', answer(200, { status: 'success', message: 'ok' }))).ok,
};
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is required")
def test_account_settings_parser_rejects_malformed_bodies():
    result = subprocess.run(["node", "--experimental-strip-types", "--input-type=module", "-e", SCRIPT],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["picture"] is None  # a javascript: picture is dropped
    assert out["devices"] == 1 and out["cities"] == ["Helsinki"] and out["transports"] == ["usb"]
    assert out["badBody"] is None and out["noMfa"] is None and out["badDark"] is None and out["badLogins"] is None
    assert out["kinds"] == ["signed-out", "ok", "unavailable", "unavailable"]
    assert out["hash"] == ["security", "general", "general"]
    assert out["keys"] == [0, 1, 3, 3, None]
    assert out["payload"][0] == {"display_name": "A", "language": "fi", "dark_mode": True}  # no city when none is chosen
    assert out["payload"][1]["city"] == "Oulu"
    assert out["filter"] == ["Äänekoski"]
    assert out["flaskError"] == {"ok": False, "message": "Admin-nimitys on varattu palvelun sisäiseen käyttöön."}
    assert out["flaskHtml"]["ok"] is False and out["flaskHtml"]["message"] == "fallback"
    assert out["flaskOk"] is True
    assert out["csrf"] == [None, "a" * 30 + "." + "b" * 10, None, None]  # the token needs writes: true and a sane shape
    assert out["goOk"]["ok"] is True
    assert out["goCalls"][0][:3] == ["/api/account/profile", "POST", "a" * 30 + "." + "b" * 10]
    assert json.loads(out["goCalls"][0][3]) == {"bio": "Hei"}
    assert out["go400"] == {"ok": False, "message": "Esittely on liian pitkä"}
    assert out["go401"] == out["go403"] == "Istunto vanhentui. Lataa sivu uudelleen."
    assert out["go403plain"] == out["go503"] == "fb"
    assert out["go429"] == "Liian monta pyyntöä. Yritä hetken kuluttua uudelleen."
