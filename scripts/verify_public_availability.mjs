// Checks the React host's lockdown gate when the status read fails.
import assert from 'node:assert/strict';
import { availabilityReader, AVAILABILITY_GRACE_MS } from '../frontend/public-availability.mjs';

function harness(override = () => false) {
  let t = 1_000_000;
  let reply = { available: true };
  const fetcher = async () => {
    if (reply === 'fail') throw new Error('timeout');
    return { ok: true, json: async () => reply };
  };
  const read = availabilityReader('http://api', fetcher, () => t, override, () => {});
  return { read, set: value => { reply = value; }, advance: ms => { t += ms; } };
}

{ // read fails after open: stays open inside the window, closes after it
  const g = harness();
  assert.equal(await g.read(), true);
  g.set('fail'); g.advance(2000);
  assert.equal(await g.read(), true);
  g.advance(AVAILABILITY_GRACE_MS - 5000);
  assert.equal(await g.read(), true);
  g.advance(10_000);
  assert.equal(await g.read(), false);
  g.set({ available: true }); g.advance(2000);
  assert.equal(await g.read(), true);
}
{ // read fails after locked: stays locked; explicit open reopens
  const g = harness();
  await g.read();
  g.set({ available: false }); g.advance(2000);
  assert.equal(await g.read(), false, 'a lockdown applies at once');
  g.set('fail');
  for (let i = 0; i < 3; i++) {
    g.advance(AVAILABILITY_GRACE_MS);
    assert.equal(await g.read(), false, 'a failed read never reopens');
  }
  g.set({ available: true }); g.advance(2000);
  assert.equal(await g.read(), true);
}
{ // restart during outage: open unless the override says closed
  let closed = false;
  const g = harness(() => closed);
  g.set('fail');
  assert.equal(await g.read(), true);
  g.advance(AVAILABILITY_GRACE_MS * 4);
  assert.equal(await g.read(), true);
  closed = true; g.advance(2000);
  assert.equal(await g.read(), false);
  g.set({ available: true }); g.advance(2000);
  assert.equal(await g.read(), true);
}
console.log('public availability gate: ok');
