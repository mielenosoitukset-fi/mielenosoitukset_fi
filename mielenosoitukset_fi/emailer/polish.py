"""Shared look fixes applied to every outgoing HTML email at render time.

Most templates are standalone with inline light-only styles. Instead of
editing 50 files, inject one responsive + dark-mode stylesheet and the meta
tags mail clients need.
"""
import re
from html import unescape

_META = (
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    '<meta name="color-scheme" content="light dark">'
    '<meta name="supported-color-schemes" content="light dark">'
)

_STYLE = """<style>
img { max-width: 100%; height: auto; }
table { max-width: 100%; }
@media (max-width: 600px) {
  body { padding: 8px !important; }
  .container, .email-card, .card { padding: 18px !important; border-radius: 10px !important; }
  a.btn, .btn, .button { display: block !important; text-align: center !important; margin: 0 0 10px 0 !important; }
}
@media (prefers-color-scheme: dark) {
  body, .email-wrapper { background-color: #0f172a !important; color: #e5e7eb !important; }
  .container, .email-card, .card, article { background-color: #1e293b !important; border-color: #334155 !important; color: #e5e7eb !important; box-shadow: none !important; }
  h1, h2, h3, p, li, td, span, div, strong { color: #e5e7eb !important; }
  .eyebrow, .meta, .footer, small { color: #94a3b8 !important; }
  a { color: #93c5fd !important; }
  a.btn, .btn, .btn-primary, .button { background-color: #3b82f6 !important; color: #ffffff !important; }
  .btn-danger { background-color: #ef4444 !important; }
  .btn-muted { background-color: #334155 !important; color: #e5e7eb !important; }
  .data-list li { background-color: #0f172a !important; border-color: #334155 !important; }
  hr { border-top-color: #334155 !important; }
}
</style>"""


def polish_html(html: str) -> str:
    """Add viewport/color-scheme meta and a dark-mode + mobile stylesheet."""
    if not html or "data-mfi-polished" in html:
        return html
    block = _META + _STYLE.replace("<style>", "<style data-mfi-polished>", 1)
    if re.search(r"</head>", html, re.I):
        return re.sub(r"</head>", lambda m: block + m.group(0), html, count=1, flags=re.I)
    if re.search(r"<body", html, re.I):
        return re.sub(r"<body", lambda m: "<head>" + block + "</head>" + m.group(0), html, count=1, flags=re.I)
    return block + html


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
