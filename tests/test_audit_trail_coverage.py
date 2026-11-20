"""CI guard: no database client may be created without the audit listener.

The audit trail sits in the database driver (see utils/audit_trail.py and
go-api/audit_trail.go), so a write path can only skip it by opening a client
of its own. These checks fail when new code does that. Use
``mielenosoitukset_fi.utils.audit_trail.mongo_client`` (Python) or the client
built in ``go-api/main.go`` instead.
"""

import re
from pathlib import Path

from mielenosoitukset_fi.utils import audit_trail

ROOT = Path(__file__).resolve().parents[1]
PY_ALLOWED = {ROOT / "mielenosoitukset_fi" / "utils" / "audit_trail.py"}
PY_CLIENT = re.compile(r"(?<![\w.])(?:pymongo\.)?(?:Async)?MongoClient\s*\(|\bmotor\b|AsyncIOMotorClient")
GO_CONNECT = re.compile(r"\bmongo\.Connect\s*\(")


def _python_sources():
    for base in (ROOT / "mielenosoitukset_fi", ROOT / "scripts"):
        yield from base.rglob("*.py")
    yield from ROOT.glob("*.py")


def test_python_code_creates_clients_only_through_the_audit_factory():
    offenders = []
    for path in _python_sources():
        if path in PY_ALLOWED or "node_modules" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            code = line.split("#", 1)[0]
            if PY_CLIENT.search(code):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert not offenders, (
        "These lines open a MongoDB client without the audit trail; use "
        "mielenosoitukset_fi.utils.audit_trail.mongo_client():\n" + "\n".join(offenders)
    )


def test_go_api_connects_only_in_main_with_the_audit_monitor():
    offenders = []
    go_dir = ROOT / "go-api"
    for path in go_dir.rglob("*.go"):
        if path.name.endswith("_test.go"):
            continue
        text = path.read_text(encoding="utf-8")
        if GO_CONNECT.search(text) and path.name != "main.go":
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, "Only go-api/main.go may connect to MongoDB: " + ", ".join(offenders)
    main = (go_dir / "main.go").read_text(encoding="utf-8")
    assert "SetMonitor(audit.monitor())" in main, "go-api/main.go must attach the audit monitor"


def test_database_manager_uses_the_audit_factory():
    source = (ROOT / "mielenosoitukset_fi" / "database_manager.py").read_text(encoding="utf-8")
    assert "mongo_client(" in source


def test_every_skipped_collection_is_documented():
    doc = (ROOT / "docs" / "audit-trail.md").read_text(encoding="utf-8")
    missing = [name for name in audit_trail.SKIPPED_COLLECTIONS if f"`{name}`" not in doc]
    assert not missing, f"Document why these collections are not audited: {missing}"


def test_go_and_python_skip_the_same_collections():
    go = (ROOT / "go-api" / "audit_trail.go").read_text(encoding="utf-8")
    block = go.split("var auditSkippedCollections", 1)[1].split("}", 1)[0]
    go_names = set(re.findall(r'"([^"]+)"', block))
    assert go_names == set(audit_trail.SKIPPED_COLLECTIONS)
