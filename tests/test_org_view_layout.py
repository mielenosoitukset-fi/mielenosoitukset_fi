"""Organisation page tables fit their column instead of running under the side panel (audit #12)."""

from pathlib import Path

CSS = Path("mielenosoitukset_fi/static/css/admin/workspace.css").read_text(encoding="utf-8")


def test_org_view_uses_table_main_layout(admin_client, seeded_data):
    page = admin_client.get(f"/admin/organization/view/{seeded_data['org_id']}").get_data(as_text=True)
    assert 'class="admin-detail-layout admin-detail-layout--table-main"' in page


def test_detail_tables_scroll_inside_their_column():
    assert ".admin-detail-layout > .admin-stack .admin-data-view__viewport {\n  overflow-x: auto;" in CSS
    assert ".admin-detail-layout.admin-detail-layout--table-main {\n    grid-template-columns: 1fr;" in CSS
    assert ".admin-toast:not(.show) { visibility: hidden;" in CSS
