"""Print the test files for one CI shard, balanced by file size.

Usage: python3 scripts/ci_test_shard.py SHARD SHARDS (SHARD counts from 1).
Every file lands in exactly one shard, and the split is stable for a given tree.
"""
import sys
from pathlib import Path


def shard_files(root, shard, shards):
    files = sorted(root.glob("tests/**/test_*.py"))
    loads = [0] * shards
    buckets = [[] for _ in range(shards)]
    for path in sorted(files, key=lambda p: (-p.stat().st_size, str(p))):
        index = loads.index(min(loads))
        loads[index] += path.stat().st_size
        buckets[index].append(path.relative_to(root).as_posix())
    return sorted(buckets[shard - 1])


if __name__ == "__main__":
    shard, shards = int(sys.argv[1]), int(sys.argv[2])
    if not 1 <= shard <= shards:
        raise SystemExit("shard must be between 1 and SHARDS")
    print("\n".join(shard_files(Path(__file__).resolve().parents[1], shard, shards)))
