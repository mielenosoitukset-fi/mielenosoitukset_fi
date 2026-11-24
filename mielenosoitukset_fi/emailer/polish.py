"""One shared look for every outgoing HTML email.

Templates were written one by one with their own banners, fonts and colours,
so mail from the same site looked unrelated (and a bit like phishing). At
render time we strip each template's own styling, keep its content, and wrap
it in one branded shell: same header, typography, buttons, callouts, footer,
dark mode and mobile layout.
"""
import re
from html import unescape

MARK = "data-mfi-shell"
SITE = "https://mielenosoitukset.fi"

_KEEP_BTN = {"btn", "btn-primary", "btn-danger", "btn-muted", "button"}
_KEEP_PLAIN = {"strong", "data-list", "eyebrow", "meta", "change-value", "change-value--new",
               "change-value--original", "btn-group"}
_CALLOUT = {"message", "appeal", "steps", "note", "box", "reason", "ticket-box", "info", "changes"}

_CSS = """
body { margin:0; padding:0; background:#f3f4f8; color:#1f2937; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; font-size:16px; line-height:1.6; }
.mfi-wrap { max-width:620px; margin:0 auto; padding:24px 16px; }
.mfi-brand { padding:0 4px 14px 4px; font-size:18px; font-weight:700; letter-spacing:.01em; color:#0033a0; }
.mfi-brand a { color:#0033a0; text-decoration:none; }
.mfi-card { background:#ffffff; border:1px solid #e5e7eb; border-radius:14px; padding:28px; }
.mfi-card h1, .mfi-card h2, .mfi-card h3 { color:#0f172a; line-height:1.3; margin:0 0 14px 0; }
.mfi-card h1 { font-size:22px; } .mfi-card h2 { font-size:20px; } .mfi-card h3 { font-size:17px; }
.mfi-card p { margin:0 0 14px 0; }
.mfi-card .strong { font-weight:700; }
.mfi-card ul, .mfi-card ol { margin:0 0 14px 0; padding-left:22px; }
.mfi-card a { color:#0033a0; text-decoration:underline; }
.mfi-card hr { border:0; border-top:1px solid #e5e7eb; margin:20px 0; }
.mfi-card small, .mfi-card .meta, .mfi-small { color:#6b7280; font-size:13px; }
.mfi-card .eyebrow { text-transform:uppercase; letter-spacing:.1em; font-size:11px; color:#6b7280; margin:0 0 6px 0; }
.mfi-card a.btn, .mfi-card a.button { display:inline-block; background:#0033a0; color:#ffffff !important; text-decoration:none; font-weight:600; font-size:15px; padding:12px 20px; border-radius:8px; margin:4px 8px 8px 0; }
.mfi-card a.btn-danger { background:#b91c1c; }
.mfi-card a.btn-muted { background:#e5e7eb; color:#1f2937 !important; }
.mfi-callout, .mfi-card .data-list li { background:#f5f7fb; border:1px solid #e5e7eb; border-left:3px solid #0033a0; border-radius:8px; padding:12px 14px; margin:0 0 12px 0; white-space:normal; }
.mfi-card .data-list { list-style:none; padding:0; }
.mfi-card article { border:1px solid #e5e7eb; border-radius:10px; padding:14px; margin:0 0 12px 0; }
.mfi-card table { border-collapse:collapse; max-width:100%; }
.mfi-card td, .mfi-card th { padding:6px 8px; text-align:left; vertical-align:top; }
.mfi-card img { max-width:100%; height:auto; }
.mfi-foot { padding:16px 4px 0 4px; font-size:12px; line-height:1.5; color:#6b7280; text-align:center; }
.mfi-foot a { color:#6b7280; }
@media (max-width:600px) {
  .mfi-wrap { padding:12px 8px; } .mfi-card { padding:20px 16px; border-radius:10px; }
  .mfi-card a.btn, .mfi-card a.button { display:block; text-align:center; margin:0 0 10px 0; }
}
@media (prefers-color-scheme: dark) {
  body { background:#0f172a !important; color:#e5e7eb !important; }
  .mfi-card { background:#1e293b !important; border-color:#334155 !important; }
  .mfi-brand, .mfi-brand a { color:#93c5fd !important; }
  .mfi-card h1, .mfi-card h2, .mfi-card h3, .mfi-card p, .mfi-card li, .mfi-card td, .mfi-card th, .mfi-card strong, .mfi-card span, .mfi-card div { color:#e5e7eb !important; }
  .mfi-card a { color:#93c5fd !important; }
  .mfi-card a.btn, .mfi-card a.button { background:#3b82f6 !important; color:#ffffff !important; }
  .mfi-card a.btn-danger { background:#ef4444 !important; }
  .mfi-card a.btn-muted { background:#334155 !important; color:#e5e7eb !important; }
  .mfi-callout, .mfi-card .data-list li { background:#0f172a !important; border-color:#334155 !important; border-left-color:#3b82f6 !important; }
  .mfi-card article, .mfi-card hr { border-color:#334155 !important; }
  .mfi-card small, .mfi-card .meta, .mfi-small, .mfi-card .eyebrow, .mfi-foot, .mfi-foot a { color:#94a3b8 !important; }
}
"""

_ATTR_STYLE = re.compile(r"""\sstyle\s*=\s*("[^"]*"|'[^']*')""", re.I)
_ATTR_CLASS = re.compile(r"""\sclass\s*=\s*("([^"]*)"|'([^']*)')""", re.I)
_TAG = re.compile(r"<(?!/)(?!!)([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>")


def _retag(m):
    name, attrs = m.group(1), m.group(2)
    lname = name.lower()
    style = _ATTR_STYLE.search(attrs)
    cls = _ATTR_CLASS.search(attrs)
    classes = (cls.group(2) if cls and cls.group(2) is not None else (cls.group(3) if cls else "")) or ""
    words = classes.split()
    keep = []
    if lname == "a":
        if any(w in _KEEP_BTN for w in words) or (style and re.search(r"background", style.group(1), re.I)):
            keep.append("btn")
            keep.extend(w for w in words if w in ("btn-danger", "btn-muted"))
    elif lname in ("div", "p", "section"):
        if any(w in _CALLOUT for w in words):
            keep.append("mfi-callout")
        elif "footer" in words or "meta" in words:
            keep.append("mfi-small")
        else:
            keep.extend(w for w in words if w in _KEEP_PLAIN)
    else:
        keep.extend(w for w in words if w in _KEEP_PLAIN)
    attrs = _ATTR_STYLE.sub("", attrs)
    attrs = _ATTR_CLASS.sub("", attrs)
    if keep:
        attrs += ' class="%s"' % " ".join(dict.fromkeys(keep))
    return "<%s%s>" % (name, attrs)


def _title(html):
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    return m.group(1).strip() if m else "Mielenosoitukset.fi"


def polish_html(html: str) -> str:
    """Wrap an email's content in the shared branded shell."""
    if not html or MARK in html:
        return html
    title = _title(html)
    m = re.search(r"(?is)<body[^>]*>(.*)</body>", html)
    inner = m.group(1) if m else re.sub(r"(?is)<head\b.*?</head>|<!doctype[^>]*>|</?html[^>]*>", "", html)
    inner = re.sub(r"(?is)<style\b.*?</style>|<script\b.*?</script>", "", inner)
    inner = _TAG.sub(_retag, inner)
    return (
        '<!DOCTYPE html><html lang="fi" %s><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        '<meta name="supported-color-schemes" content="light dark">'
        '<title>%s</title><style>%s</style></head><body>'
        '<div class="mfi-wrap"><div class="mfi-brand"><a href="%s">Mielenosoitukset.fi</a></div>'
        '<div class="mfi-card">%s</div>'
        '<div class="mfi-foot">Tämä viesti on lähetetty Mielenosoitukset.fi-palvelusta. '
        'Tarkista aina, että viestin linkit alkavat osoitteella <a href="%s">https://mielenosoitukset.fi</a>.<br>'
        'Älä vastaa tähän viestiin, ellei toisin mainita.</div></div></body></html>'
    ) % (MARK, title, _CSS, SITE, inner, SITE)


def html_to_text(html: str) -> str:
    """Readable plain-text alternative from HTML (links kept as 'text (url)')."""
    text = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", "", html or "")
    text = re.sub(
        r'(?is)<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        lambda m: f"{re.sub(r'<[^>]+>', '', m.group(2)).strip()} ({m.group(1)})"
        if not m.group(1).startswith(("mailto:", "#")) else re.sub(r'<[^>]+>', '', m.group(2)),
        text,
    )
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr|article)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()
