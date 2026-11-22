"""Print the test files for one CI shard, balanced by recorded run time.

Usage: python3 scripts/ci_test_shard.py SHARD SHARDS (SHARD counts from 1).
Every file lands in exactly one shard, and the split is stable for a given tree.

``scripts/ci_test_durations.json`` maps test files to seconds from a recent CI
run (see ``scripts/ci_junit_durations.py``). Files it lacks are estimated from
their size at the recorded seconds per byte. Without the file the split falls
back to file size alone. Shard 1 also builds the frontend, so it starts with a
head start in seconds that lowers the share of tests it receives.
"""
import json
import sys
from pathlib import Path

SHARD_ONE_SETUP_SECONDS = 35.0


def _weights(root, files):
    sizes = {p: p.stat().st_size for p in files}
    try:
        recorded = json.loads((root / "scripts" / "ci_test_durations.json").read_text())
    except (OSError, ValueError):
        return {p: float(size) for p, size in sizes.items()}, 0.0
    known = [(recorded[rel], size) for p, size in sizes.items()
             if (rel := p.relative_to(root).as_posix()) in recorded]
    total_size = sum(size for _, size in known) or 1
    per_byte = sum(sec for sec, _ in known) / total_size
    return {
        p: float(recorded.get(p.relative_to(root).as_posix(), sizes[p] * per_byte))
        for p in files
    }, SHARD_ONE_SETUP_SECONDS


def shard_files(root, shard, shards):
    files = sorted(root.glob("tests/**/test_*.py"))
    weights, shard_one_start = _weights(root, files)
    loads = [0.0] * shards
    loads[0] = shard_one_start
    buckets = [[] for _ in range(shards)]
    for path in sorted(files, key=lambda p: (-weights[p], str(p))):
        index = loads.index(min(loads))
        loads[index] += weights[path]
        buckets[index].append(path.relative_to(root).as_posix())
    return sorted(buckets[shard - 1])


if __name__ == "__main__":
    shard, shards = int(sys.argv[1]), int(sys.argv[2])
    if not 1 <= shard <= shards:
        raise SystemExit("shard must be between 1 and SHARDS")
    print("\n".join(shard_files(Path(__file__).resolve().parents[1], shard, shards)))
