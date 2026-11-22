"""Keep the CI host workaround scoped without downgrading/skipping tests."""

from pathlib import Path
import shlex

import yaml


def test_mongo_compatible_runner_keeps_database_and_required_suite():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/tests.yml").read_text())
    compose = yaml.safe_load((root / "compose.test.yml").read_text())
    job = workflow["jobs"]["python-tests-shard"]
    # Fork PRs always run on GitHub-hosted ubuntu-22.04; pushes and same-repo
    # PRs default to the self-hosted AX41 runners (MOSO_CI_RUNS_ON overrides).
    runs_on = job["runs-on"]
    assert "github.event.pull_request.head.repo.full_name != github.repository) && 'ubuntu-22.04'" in runs_on
    assert "fromJSON(vars.MOSO_CI_RUNS_ON || '[\"self-hosted\",\"self-hosted-moso\"]')" in runs_on
    assert compose["services"]["mongo"]["image"] == "mongo:8"
    assert not job.get("continue-on-error", False)
    assert job["timeout-minutes"] == 30
    setup = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/setup-python@"))
    assert setup["with"]["python-version"] == "3.12"
    steps = {step["name"]: step for step in job["steps"]}
    assert "mongo redis mailpit localstack" in steps["Start disposable test services"]["run"]
    assert "--with-deps chromium" in steps["Install Chromium for Playwright"]["run"]
    for name in ("Wait for test services", "Run test suite"):
        assert not steps[name].get("continue-on-error", False)
        assert "if" not in steps[name]
    command = shlex.split(steps["Run test suite"]["run"].splitlines()[-1])
    assert command[:13] == [
        "python", "-u", "-m", "pytest", "-o", "addopts=", "-v", "--tb=short",
        "--durations=25", "--timeout=180", "--timeout-method=thread",
        "-o", "faulthandler_timeout=120",
    ]
    assert job["strategy"]["matrix"]["shard"] == [1, 2, 3, 4]
    assert not job["strategy"]["fail-fast"]
    gate = workflow["jobs"]["python-tests"]
    assert gate["needs"] == "python-tests-shard" and gate["if"] == "always()"
    assert gate["runs-on"] == job["runs-on"]

    concurrency = workflow["concurrency"]
    assert "github.event.pull_request.number" in concurrency["group"]
    assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


def test_shards_cover_every_test_file_once():
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("ci_test_shard", root / "scripts/ci_test_shard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    shards = [module.shard_files(root, n, 4) for n in range(1, 5)]
    every = sorted(p.relative_to(root).as_posix() for p in root.glob("tests/**/test_*.py"))
    assert sorted(f for shard in shards for f in shard) == every
    assert all(shards)
