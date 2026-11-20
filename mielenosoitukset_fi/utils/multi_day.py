"""Multi-day demonstrations: an optional ``end_date`` after the start ``date``.

A demonstration that runs over several days keeps ``date`` as its first day
and stores its last day in ``end_date`` (both ``YYYY-MM-DD``). A missing,
empty or same-day ``end_date`` means a one-day demonstration. This is separate
from recurring series, which are independent occurrences.
"""

from datetime import date as _date, timedelta

from flask_babel import _

MAX_DEMO_DAYS = 14


def _parse(value):
    try:
        parsed = _date.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed.isoformat() == str(value).strip() else None


def normalize_end_date(start, end):
    """Return the stored ``end_date`` for a form value, or None for one day.

    Raises ValueError with a Finnish message when the end date is not a date,
    is before the start, or makes the demonstration longer than
    ``MAX_DEMO_DAYS`` days.
    """
    raw = (end or "").strip() if isinstance(end, str) else end
    if not raw:
        return None
    end_day = _parse(raw)
    if end_day is None:
        raise ValueError(_("Päättymispäivän pitää olla muodossa VVVV-KK-PP."))
    start_day = _parse(start)
    if start_day is None:
        raise ValueError(_("Anna alkamispäivä ennen päättymispäivää."))
    if end_day < start_day:
        raise ValueError(_("Päättymispäivä ei voi olla ennen alkamispäivää."))
    if end_day == start_day:
        return None
    if end_day > start_day + timedelta(days=MAX_DEMO_DAYS - 1):
        raise ValueError(
            _("Monipäiväinen mielenosoitus voi kestää enintään %(days)s päivää.", days=MAX_DEMO_DAYS)
        )
    return end_day.isoformat()


def demo_last_day(demo):
    """The last day a demonstration runs, as ``YYYY-MM-DD`` (its date if one day)."""
    start = demo.get("date")
    end = demo.get("end_date")
    if end and _parse(end) and _parse(start) and end > start:
        return end
    return start
