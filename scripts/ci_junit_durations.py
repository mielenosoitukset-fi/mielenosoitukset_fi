"""Merge pytest JUnit files into scripts/ci_test_durations.json.

Usage: python3 scripts/ci_junit_durations.py junit-1.xml junit-2.xml ...
Sums each test's seconds per file. Run after downloading the shard artifacts of
a green CI run so the next split follows real run times.
"""
import json
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


def main(paths):
    totals = defaultdict(float)
    for path in paths:
        for case in ET.parse(path).iter("testcase"):
            module = case.get("classname", "").split(".")
            file = next((i for i, part in enumerate(module) if part.startswith("test_")), None)
            if file is not None:
                totals["/".join(module[: file + 1]) + ".py"] += float(case.get("time", 0))
    out = Path(__file__).with_name("ci_test_durations.json")
    out.write_text(json.dumps({k: round(v, 1) for k, v in sorted(totals.items())}, indent=0) + "\n")
    print(f"{len(totals)} files, {sum(totals.values()):.0f} s")


if __name__ == "__main__":
    main(sys.argv[1:])
