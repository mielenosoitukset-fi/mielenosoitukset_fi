"""Keep the CI host workaround scoped without downgrading/skipping tests."""

from pathlib import Path
import shlex

import yaml


def test_mongo_compatible_runner_keeps_database_and_required_suite():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/tests.yml").read_text())
    compose = yaml.safe_load((root / "compose.test.yml").read_text())
    reusable = yaml.safe_load((root / ".github/workflows/python-shards.yml").read_text())
    job = reusable["jobs"]["shard"]
    call = workflow["jobs"]["python-tests-shard"]
    assert call["uses"].startswith("mielenosoitukset-fi/mielenosoitukset_fi/.github/workflows/python-shards.yml@")
    assert len(call["uses"].rsplit("@", 1)[1]) == 40
    # Fork PRs always run on GitHub-hosted ubuntu-22.04; pushes and same-repo
    # PRs default to the self-hosted AX41 runners (MOSO_CI_RUNS_ON overrides).
    runs_on = job["runs-on"]
    assert "github.event.pull_request.head.repo.full_name == github.repository" in runs_on
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
    assert job["strategy"]["matrix"]["shard"] == [1, 2, 3]
    assert not job["strategy"]["fail-fast"]
    gate = workflow["jobs"]["python-tests"]
    assert gate["needs"] == "python-tests-shard" and gate["if"] == "always()"
    assert gate["runs-on"] == "ubuntu-22.04"
    assert runs_on.endswith("|| 'ubuntu-22.04' }}")
    assert "merge_group" not in runs_on

    concurrency = workflow["concurrency"]
    assert "github.event.pull_request.number" in concurrency["group"]
    assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


def test_shards_cover_every_test_file_once():
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("ci_test_shard", root / "scripts/ci_test_shard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    shards = [module.shard_files(root, n, 3) for n in range(1, 4)]
    every = sorted(p.relative_to(root).as_posix() for p in root.glob("tests/**/test_*.py"))
    assert sorted(f for shard in shards for f in shard) == every
    assert all(shards)


def test_disposable_ci_mongo_database_names_are_unique_per_job_and_worker(monkeypatch):
    from tests import conftest

    monkeypatch.delenv("MOSO_CI_MONGO_SLOT", raising=False)
    for env, expected in (
        ({"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2", "SHARD": "4", "PYTEST_XDIST_WORKER": "gw3"},
         "mielenosoitukset_test_123_2_4_gw3_"),
    ):
        for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "SHARD", "PYTEST_XDIST_WORKER"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(conftest, "XDIST_WORKER", env["PYTEST_XDIST_WORKER"])
        names = {conftest._test_db_name() for _ in range(5)}
        assert len(names) == 5
        assert all(name.startswith(expected) and len(name) <= 63 for name in names)


def test_shared_ci_mongo_database_names_match_runner_grants(monkeypatch):
    from tests import conftest

    for slot in ("02", "03", "04"):
        monkeypatch.setenv("MOSO_CI_MONGO_SLOT", slot)
        for worker in (None, "gw0", "gw1"):
            monkeypatch.setattr(conftest, "XDIST_WORKER", worker)
            assert conftest._test_db_name() == f"mielenosoitukset_test_{slot}_{worker or 'controller'}"


def test_shards_follow_recorded_durations_and_cover_every_file(tmp_path):
    from scripts.ci_test_shard import shard_files

    (tmp_path / "tests").mkdir()
    (tmp_path / "scripts").mkdir()
    for name, size in {"a": 10, "b": 10, "c": 10, "d": 10}.items():
        (tmp_path / "tests" / f"test_{name}.py").write_text("x" * size)
    (tmp_path / "scripts" / "ci_test_durations.json").write_text(
        '{"tests/test_a.py": 100, "tests/test_b.py": 50, "tests/test_c.py": 50, "tests/test_d.py": 10}'
    )
    shards = [shard_files(tmp_path, n, 2) for n in (1, 2)]
    assert sorted(shards[0] + shards[1]) == [f"tests/test_{n}.py" for n in "abcd"]
    assert "tests/test_a.py" in shards[1]  # shard 1 also builds the frontend


def test_browser_tests_are_outside_the_required_check_but_run_on_main_and_nightly():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/browser-tests.yml").read_text())
    triggers = workflow.get("on") or workflow[True]
    assert "pull_request" not in triggers and "merge_group" not in triggers
    assert triggers["push"]["branches"] == ["main"] and triggers["schedule"]
    assert workflow["jobs"]["browser"]["with"]["browser"] is True
    reusable = yaml.safe_load((root / ".github/workflows/python-shards.yml").read_text())
    assert reusable["jobs"]["shard"]["env"]["MOSO_BROWSER_TESTS"]
