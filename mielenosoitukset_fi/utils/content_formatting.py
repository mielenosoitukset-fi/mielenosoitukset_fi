import html
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

import markdown


_MARKDOWN_HTML_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "code",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "ul",
}


def markdown_to_html(value):
    """Convert editor Markdown to safe HTML without accepting raw HTML."""
    text = (value or "").strip()
    if not text:
        return ""

    rendered = markdown.markdown(
        html.escape(text),
        extensions=["sane_lists", "nl2br"],
    )
    sanitizer = _SafeMarkdownHTMLParser()
    sanitizer.feed(rendered)
    sanitizer.close()
    return sanitizer.html()


_DISCARDED_HTML_CONTENT = {"script", "style", "template", "iframe", "object", "noscript", "svg", "math", "title", "head"}


def sanitize_stored_html(value):
    """Rebuild stored rich text with the Markdown tag allowlist.

    Descriptions and e-mailed messages are stored as raw HTML from public
    input. Never render them with ``|safe``: this keeps the small formatting
    vocabulary, drops every attribute except a safe link target and discards
    script-like content entirely.
    """
    text = value if isinstance(value, str) else ""
    if not text.strip():
        return ""
    sanitizer = _SafeMarkdownHTMLParser(discard=_DISCARDED_HTML_CONTENT)
    sanitizer.feed(text)
    sanitizer.close()
    return sanitizer.html()


def markdown_to_plain_text(value):
    """Convert Markdown into readable plain text through the safe HTML form."""
    rendered = markdown_to_html(value)
    if not rendered:
        return ""

    parser = _HTMLToPlainTextParser()
    parser.feed(rendered)
    parser.close()
    return parser.text()


def safe_href(value):
    """Return ``value`` when it is an http(s) or mailto link, otherwise ""."""
    if not isinstance(value, str):
        return ""
    target = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in target):
        return ""
    return _safe_link_target(target)


def _safe_link_target(value):
    target = (value or "").strip()
    parsed = urlparse(target)
    if parsed.scheme.lower() in {"http", "https", "mailto"}:
        return target
    return ""


class _SafeMarkdownHTMLParser(HTMLParser):
    def __init__(self, discard=()):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.discard = set(discard)
        self.discard_depth = 0
        self.open_tags = []

    def handle_starttag(self, tag, attrs):
        if tag in self.discard:
            self.discard_depth += 1
            return
        if self.discard_depth:
            return
        if tag not in _MARKDOWN_HTML_TAGS:
            return
        if tag == "a":
            target = _safe_link_target(dict(attrs).get("href"))
            href = f' href="{html.escape(target, quote=True)}"' if target else ""
            self.parts.append(f"<a{href}>")
            self.open_tags.append(tag)
            return
        self.parts.append(f"<{tag}>")
        if tag not in {"br", "hr"}:
            self.open_tags.append(tag)

    def handle_startendtag(self, tag, attrs):
        if self.discard_depth or tag in self.discard:
            return
        if tag in {"br", "hr"}:
            self.parts.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in self.discard:
            self.discard_depth = max(self.discard_depth - 1, 0)
            return
        if self.discard_depth:
            return
        if tag in _MARKDOWN_HTML_TAGS and tag not in {"br", "hr"}:
            # Close only tags this parser opened, so stored markup cannot
            # close or leave open elements of the page around it.
            if tag not in self.open_tags:
                return
            while self.open_tags:
                opened = self.open_tags.pop()
                self.parts.append(f"</{opened}>")
                if opened == tag:
                    break

    def handle_data(self, data):
        if self.discard_depth:
            return
        self.parts.append(html.escape(data))

    def html(self):
        closing = "".join(f"</{tag}>" for tag in reversed(self.open_tags))
        return "".join(self.parts) + closing


class _HTMLToPlainTextParser(HTMLParser):
    """Render the allowlisted Markdown HTML as a useful email text part."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.list_stack = []
        self.link_stack = []

    def _append(self, value):
        self.parts.append(value)

    def _ensure_newline(self, count=1):
        current = "".join(self.parts)
        missing = count - len(current) + len(current.rstrip("\n"))
        if missing > 0:
            self._append("\n" * missing)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {
            "p",
            "blockquote",
            "pre",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
        }:
            self._ensure_newline(2)
        elif tag == "br":
            self._ensure_newline()
        elif tag in {"ul", "ol"}:
            self._ensure_newline()
            self.list_stack.append({"tag": tag, "index": 0})
        elif tag == "li":
            self._ensure_newline()
            indent = "  " * max(len(self.list_stack) - 1, 0)
            if self.list_stack and self.list_stack[-1]["tag"] == "ol":
                self.list_stack[-1]["index"] += 1
                marker = f"{self.list_stack[-1]['index']}. "
            else:
                marker = "- "
            self._append(f"{indent}{marker}")
        elif tag == "a":
            self.link_stack.append(_safe_link_target(attrs.get("href")))

    def handle_endtag(self, tag):
        if tag in {
            "p",
            "blockquote",
            "pre",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
        }:
            self._ensure_newline(2)
        elif tag == "li":
            self._ensure_newline()
        elif tag in {"ul", "ol"}:
            if self.list_stack:
                self.list_stack.pop()
            self._ensure_newline(2)
        elif tag == "a":
            target = self.link_stack.pop() if self.link_stack else ""
            if target:
                self._append(f" ({target})")

    def handle_data(self, data):
        self._append(data)

    def text(self):
        value = "".join(self.parts)
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()


class _HTMLToMarkdownParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.list_stack = []
        self.link_stack = []

    def _append(self, value):
        self.parts.append(value)

    def _ensure_newline(self, count=1):
        current = "".join(self.parts)
        missing = count - len(current) + len(current.rstrip("\n"))
        if missing > 0:
            self._append("\n" * missing)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"p", "div", "blockquote"}:
            self._ensure_newline(2)
        elif tag == "br":
            self._ensure_newline()
        elif tag in {"strong", "b"}:
            self._append("**")
        elif tag in {"em", "i"}:
            self._append("*")
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._ensure_newline(2)
            self._append(f"{'#' * int(tag[1])} ")
        elif tag in {"ul", "ol"}:
            self._ensure_newline()
            self.list_stack.append({"tag": tag, "index": 0})
        elif tag == "li":
            self._ensure_newline()
            indent = "  " * max(len(self.list_stack) - 1, 0)
            if self.list_stack and self.list_stack[-1]["tag"] == "ol":
                self.list_stack[-1]["index"] += 1
                marker = f"{self.list_stack[-1]['index']}. "
            else:
                marker = "- "
            self._append(f"{indent}{marker}")
        elif tag == "a":
            target = _safe_link_target(attrs.get("href"))
            self.link_stack.append(target)
            if target:
                self._append("[")

    def handle_endtag(self, tag):
        if tag in {"p", "div", "blockquote"}:
            self._ensure_newline(2)
        elif tag in {"strong", "b"}:
            self._append("**")
        elif tag in {"em", "i"}:
            self._append("*")
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._ensure_newline(2)
        elif tag in {"ul", "ol"}:
            if self.list_stack:
                self.list_stack.pop()
            self._ensure_newline(2)
        elif tag == "li":
            self._ensure_newline()
        elif tag == "a":
            target = self.link_stack.pop() if self.link_stack else ""
            if target:
                self._append(f"]({target})")

    def handle_data(self, data):
        self._append(data)

    def markdown(self):
        value = "".join(self.parts)
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()


def html_to_markdown(value):
    """Convert stored HTML into readable Markdown for text editors."""
    text = (value or "").strip()
    if not text:
        return ""

    parser = _HTMLToMarkdownParser()
    parser.feed(text)
    parser.close()
    return parser.markdown()
