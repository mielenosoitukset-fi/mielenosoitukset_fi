"""Python twin of the Go API's public demonstration payload (``publicPayload``).

The admin preview shows an event that the public API does not serve yet
(pending, rejected or hidden), so it cannot fetch ``/api/demonstrations/<id>``.
It renders the same React detail screen with this payload preloaded instead.

Keep this module in step with ``go-api/public.go``. The fixture written by
``tests/test_go_public_payload_parity.py`` is replayed by
``go-api/public_payload_parity_test.go``, so a change on either side fails CI
until both agree.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone

from bson import ObjectId

LANGUAGES = ("fi", "en", "sv")
TEXT_FIELDS = (
    "date", "end_date", "start_time", "end_time", "city", "address", "slug", "event_type",
    "facebook", "img", "cover_picture", "cover_source", "preview_image",
)
MAX_INT32 = 2**31 - 1

# Go's strings.TrimSpace uses unicode.IsSpace, which unlike str.isspace() does
# not treat the ASCII separators U+001C..U+001F as space.
_GO_NOT_SPACE = "\x1c\x1d\x1e\x1f"
# strconv.ParseFloat follows Go literal syntax: "_" may separate digits.
_DIGITS = r"[0-9](?:_?[0-9])*"
_HEX_DIGITS = r"[0-9a-fA-F](?:_?[0-9a-fA-F])*"
_DECIMAL = re.compile(rf"[+-]?(?:{_DIGITS}(?:\.(?:{_DIGITS})?)?|\.{_DIGITS})(?:[eE][+-]?{_DIGITS})?")
_HEX_FLOAT = re.compile(
    rf"[+-]?0[xX]_?(?:{_HEX_DIGITS}(?:\.(?:{_HEX_DIGITS})?)?|\.{_HEX_DIGITS})[pP][+-]?{_DIGITS}")
_SPECIAL_FLOAT = re.compile(r"[+-]?(?:inf|infinity|nan)", re.IGNORECASE)


def _trim(value: str) -> str:
    start, end = 0, len(value)
    while start < end and value[start].isspace() and value[start] not in _GO_NOT_SPACE:
        start += 1
    while end > start and value[end - 1].isspace() and value[end - 1] not in _GO_NOT_SPACE:
        end -= 1
    return value[start:end]


def _object(value) -> dict:
    return value if isinstance(value, dict) else {}


def _array(value) -> list:
    return value if isinstance(value, list) else []


def _text(value):
    return value if isinstance(value, str) else None


def _strings_only(value) -> list[str]:
    result = []
    for item in _array(value):
        if isinstance(item, str):
            item = _trim(item)
            if item:
                result.append(item)
    return result


def _identifier(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, str) and len(value) == 24 and ObjectId.is_valid(value):
        return str(ObjectId(value))
    return None


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        if _DECIMAL.fullmatch(value):
            number = float(value.replace("_", ""))
        elif _HEX_FLOAT.fullmatch(value):
            number = float.fromhex(value.replace("_", ""))
        elif _SPECIAL_FLOAT.fullmatch(value):
            return None
        else:
            return None
    else:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _non_negative_int(value):
    number = _number(value)
    if number is None or number < 0 or number != math.trunc(number) or number > MAX_INT32:
        return None
    return int(number)


def _nonblank(value) -> bool:
    return isinstance(value, str) and bool(_trim(value))


_ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", "\\": "\\"}


def _legacy_route_list(value: str):
    """Go's legacyRouteList: a Python-literal style list of quoted strings."""
    remaining = _trim(value)
    if not (remaining.startswith("[") and remaining.endswith("]")):
        return None
    remaining = _trim(remaining[1:-1])
    result = []
    while remaining:
        quote = remaining[0]
        if quote not in "'\"":
            return None
        remaining = remaining[1:]
        item = []
        while remaining and remaining[0] != quote:
            char = remaining[0]
            if char != "\\":
                item.append(char)
                remaining = remaining[1:]
                continue
            if len(remaining) < 2:
                return None
            code = remaining[1]
            if code in _ESCAPES:
                item.append(_ESCAPES[code])
                remaining = remaining[2:]
            elif code in "'\"":
                if code != quote:
                    return None
                item.append(code)
                remaining = remaining[2:]
            elif code in "xuU":
                size = {"x": 2, "u": 4, "U": 8}[code]
                digits = remaining[2:2 + size]
                if len(digits) != size or not re.fullmatch(r"[0-9a-fA-F]+", digits):
                    return None
                point = int(digits, 16)
                if code == "x":
                    item.append(chr(point))
                else:
                    if point > 0x10FFFF or 0xD800 <= point <= 0xDFFF:
                        return None
                    item.append(chr(point))
                remaining = remaining[2 + size:]
            elif code in "01234567":
                digits = remaining[1:4]
                if len(digits) != 3 or not re.fullmatch(r"[0-7]{3}", digits) or int(digits, 8) > 255:
                    return None
                item.append(chr(int(digits, 8)))
                remaining = remaining[4:]
            else:
                return None
        if not remaining:
            return None
        cleaned = _trim("".join(item))
        if cleaned:
            result.append(cleaned)
        remaining = _trim(remaining[1:])
        if remaining:
            if remaining[0] != ",":
                return None
            remaining = _trim(remaining[1:])
    return result


def _route_stops(value) -> list[str]:
    if isinstance(value, str):
        try:
            stops = json.loads(value)
        except ValueError:
            stops = ...
        if stops is None:
            return []
        if isinstance(stops, list) and all(isinstance(stop, str) for stop in stops):
            return _strings_only(stops)
        legacy = _legacy_route_list(value)
        if legacy is not None:
            return legacy
        return _strings_only([part for part in re.split(r"[,\n]", value) if part])
    return _strings_only(value)


def _gallery_images(value) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    def add(item):
        if isinstance(item, str):
            for line in item.split("\n"):
                line = _trim(line)
                if line and line not in seen:
                    seen.add(line)
                    result.append(line)
            return
        for child in _array(item):
            add(child)

    add(value)
    return result


def _date(value):
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.strftime("%Y-%m-%d")
    return _text(value)


def _organizers(value) -> list[dict]:
    organizers = []
    for item in _array(value):
        organizer = _object(item)
        if "is_private" in organizer and not isinstance(organizer["is_private"], bool):
            continue
        if not organizer:
            continue
        private = organizer.get("is_private") is True
        show_name = organizer.get("show_name_public") is True
        entry = {
            "name": _text(organizer.get("name")) or None,
            "is_private": private,
            "website": _text(organizer.get("website")),
            "logo": _text(organizer.get("logo")),
            "organization_id": None if private else _identifier(organizer.get("organization_id")),
            "email": None,
        }
        if private and not show_name:
            # Anonymous placeholder: no identifying name, link or image.
            entry["name"] = entry["website"] = entry["logo"] = None
        if organizer.get("show_email_public") is True:
            entry["email"] = _text(organizer.get("email"))
        organizers.append(entry)
    return organizers


def public_payload(doc: dict, lang: str = "") -> dict:
    """Return what ``GET /api/demonstrations/<id>?lang=<lang>`` would return.

    Only the fields of Go's ``publicPayload`` (the event itself). The ``suggestion``
    block the API adds for the correction form is not used by the detail screen.
    """
    default_lang = doc.get("default_language")
    if default_lang not in LANGUAGES:
        default_lang = "fi"
    lang = lang or default_lang
    translations = _object(doc.get("translations"))
    localized = _object(translations.get(lang))
    payload = {"_id": _identifier(doc.get("_id")), "default_language": default_lang, "resolved_language": lang}
    for field in TEXT_FIELDS:
        payload[field] = _text(doc.get(field))
    if isinstance(doc.get("date"), datetime):
        payload["date"] = _date(doc["date"])
    for field in ("title", "description"):
        value = doc.get(field)
        candidate = localized.get(field)
        if isinstance(candidate, str) and _trim(candidate):
            value = _trim(candidate)
        payload[field] = _text(value)
    payload["tags"] = _strings_only(localized.get("tags")) or _strings_only(doc.get("tags"))
    languages = [default_lang]
    for candidate in LANGUAGES:
        values = _object(translations.get(candidate))
        if candidate != default_lang and (
            _nonblank(values.get("title")) or _nonblank(values.get("description"))
            or _strings_only(values.get("tags"))
        ):
            languages.append(candidate)
    payload["available_languages"] = sorted(languages)
    payload["cancelled"] = doc.get("cancelled") is True
    payload["running_number"] = _number(doc.get("running_number"))
    payload["parent"] = _identifier(doc.get("parent"))
    payload["likes"] = _non_negative_int(doc.get("likes"))
    payload["latitude"] = _number(doc.get("latitude"))
    payload["longitude"] = _number(doc.get("longitude"))
    payload["route"] = _route_stops(doc.get("route"))
    payload["gallery_images"] = _gallery_images(doc.get("gallery_images")) or _gallery_images(doc.get("images"))
    payload["organizers"] = _organizers(doc.get("organizers"))
    return payload
