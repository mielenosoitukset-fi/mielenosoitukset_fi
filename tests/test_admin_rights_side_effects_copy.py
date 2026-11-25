"""Rights changes name their hidden side effects before saving (20 Nov 2026)."""

from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / "mielenosoitukset_fi" / "templates" / "admin_V2"


def test_org_admin_role_confirm_names_admin_workspace_access():
    source = (TEMPLATES / "organizations" / "view.html").read_text(encoding="utf-8")
    assert "saavat organisaation ylläpito-oikeudet automaattisesti" in source
    assert "data-role=\"{{ invite.role or 'member' }}\"" in source
    assert "action === 'change-role'" in source


