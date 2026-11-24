from pathlib import Path

from mielenosoitukset_fi.emailer.polish import html_to_text, polish_html

ROOT = Path(__file__).resolve().parents[1]


def test_shell_wraps_content_and_is_idempotent():
    html = ('<html><head><title>Otsikko</title><style>body{background:red}</style></head>'
            '<body style="color:red"><div class="container"><h2 style="color:blue">Hei</h2>'
            '<a class="btn" style="background:#000" href="https://mielenosoitukset.fi/x">Avaa</a></div></body></html>')
    out = polish_html(html)
    assert "background:red" not in out and 'style="color' not in out and 'class="container"' not in out
    assert '<a href="https://mielenosoitukset.fi/x" class="btn">Avaa</a>' in out
    assert "Mielenosoitukset.fi</a></div>" in out and "prefers-color-scheme: dark" in out
    assert "<title>Otsikko</title>" in out
    assert polish_html(out) == out


def test_inline_styled_link_becomes_button_and_callouts_unify():
    out = polish_html('<p>x</p><a href="https://a" style="background: #d9534f; color:#fff">Peru</a>'
                      '<div class="appeal">Perustelu</div>')
    assert '<a href="https://a" class="btn">Peru</a>' in out
    assert 'class="mfi-callout"' in out


def test_fragment_without_body_is_wrapped():
    out = polish_html("<p>hi</p>")
    assert "<p>hi</p>" in out and "viewport" in out


def test_every_template_survives_the_shell():
    from jinja2 import Environment, FileSystemLoader, Undefined
    env = Environment(loader=FileSystemLoader(ROOT / "mielenosoitukset_fi/templates/emails"), autoescape=True)
    names = [p.relative_to(env.loader.searchpath[0]).as_posix()
             for p in Path(env.loader.searchpath[0]).rglob("*.html")
             if p.name not in {"admin_email_base.html", "admin_email_macros.html", "macros_new.html"}]
    rendered = 0
    for name in names:
        try:
            html = env.get_template(name).render()
        except Exception:
            continue  # needs context we do not fake here
        if not html.strip():
            continue
        out = polish_html(html)
        assert "mfi-card" in out and "<style" in out and out.count("<style") == 1, name
        assert out.count("Ystävällisin terveisin") == 1, name
        assert "2023" not in out and "<p>Hei!" not in out, name
        rendered += 1
    assert rendered >= 20


def test_plain_text_is_not_html():
    text = html_to_text(
        '<html><head><style>a{}</style></head><body><h1>Otsikko</h1>'
        '<p>Moi <a href="https://mielenosoitukset.fi/x">linkki</a></p></body></html>'
    )
    assert "<" not in text
    assert "linkki (https://mielenosoitukset.fi/x)" in text
    assert "a{}" not in text


def test_signoff_unified_and_old_ones_removed():
    out = polish_html('<html><body><p>Hei Olivia!</p><p>Asia.</p>'
                      '<p>Ystävällisin terveisin<br>Tiimimme</p>'
                      '<p>&copy; 2023 Järjestäjätiimi.</p>'
                      '<p>Tämä on automaattinen viesti. Älä vastaa tähän sähköpostiin.</p></body></html>')
    assert out.count("Ystävällisin terveisin") == 1 and "Tiimimme" not in out
    assert "Mielenosoitukset.fi-tiimi</p>" in out and "<p>Hei Olivia,</p>" in out
    assert "2023" not in out and "Älä vastaa tähän sähköpostiin" not in out
