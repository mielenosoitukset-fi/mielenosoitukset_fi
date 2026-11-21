"""Audit items 4 and 20: no dead panic buttons, /admin/ opens the dashboard."""


def test_admin_root_redirects_to_dashboard(admin_client):
    response = admin_client.get("/admin/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/dashboard")


def test_admin_root_needs_login(app):
    assert app.test_client().get("/admin/").status_code in (302, 401)


def test_dashboard_has_no_dead_panic_forms(admin_client):
    body = admin_client.get("/admin/dashboard").get_data(as_text=True)
    assert "/admin/dashboard/panic/activate" not in body
    assert "/admin/dashboard/panic/deactivate" not in body


def test_panic_link_only_when_security_page_opens(admin_client, monkeypatch):
    import mielenosoitukset_fi.utils.security_cases as cases

    monkeypatch.setattr(cases, "actor_access", lambda db, actor: (_ for _ in ()).throw(PermissionError("no")))
    assert "/admin/governance/security" not in admin_client.get("/admin/dashboard").get_data(as_text=True)

    monkeypatch.setattr(cases, "actor_access", lambda db, actor: {"ok": True})
    body = admin_client.get("/admin/dashboard").get_data(as_text=True)
    assert 'href="/admin/governance/security"' in body and "Avaa turvatilanteet" in body


def test_logs_link_appears_once_in_sidebar(admin_client):
    body = admin_client.get("/admin/dashboard").get_data(as_text=True)
    assert body.count("Lokit ja tapahtumat") == 1
