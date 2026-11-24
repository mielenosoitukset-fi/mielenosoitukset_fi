from mielenosoitukset_fi.emailer.polish import html_to_text, polish_html


def test_polish_adds_meta_and_dark_mode_once():
    html = "<html><head><title>x</title></head><body><p>hi</p></body></html>"
    out = polish_html(html)
    assert 'name="color-scheme"' in out
    assert "prefers-color-scheme: dark" in out
    assert out.index("</style>") < out.index("</head>")
    assert polish_html(out) == out


def test_polish_handles_fragment_without_head():
    out = polish_html("<p>hi</p>")
    assert out.endswith("<p>hi</p>") and "viewport" in out


def test_plain_text_is_not_html():
    text = html_to_text(
        '<html><head><style>a{}</style></head><body><h1>Otsikko</h1>'
        '<p>Moi <a href="https://mielenosoitukset.fi/x">linkki</a></p></body></html>'
    )
    assert "<" not in text
    assert "linkki (https://mielenosoitukset.fi/x)" in text
    assert "a{}" not in text
