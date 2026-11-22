"""Finnish labels for stored codes shown in the admin UI.

Templates use these as filters (``case_type_label``, ``urgency_label``,
``task_status_label``) so admins never see raw values like NEW_DEMO or High.
Unknown values fall back to a readable form of the code.
"""

from flask_babel import lazy_gettext as _l

CASE_TYPES = {
    "new_demo": _l("Uusi mielenosoitus"),
    "demo_error_report": _l("Virheilmoitus"),
    "demo_edit_suggestion": _l("Muutosehdotus"),
    "organization_edit_suggestion": _l("Organisaation muutospyyntö"),
    "demo_cancellation_request": _l("Peruutuspyyntö"),
    "demo_cancelled": _l("Peruttu mielenosoitus"),
    "support_ticket": _l("Tukipyyntö"),
    "city_admin_application": _l("Kaupunkiadminhakemus"),
    "account_deletion_request": _l("Tilin poistopyyntö"),
    "organization_right_request": _l("Hyväksymisoikeuspyyntö"),
}

URGENCY = {
    "high": _l("Kiireellinen"),
    "medium": _l("Kohtalainen"),
    "low": _l("Ei kiireellinen"),
}

TASK_STATUS = {
    "waiting_approval": _l("Odottaa"),
    "pending": _l("Uusi"),
    "in_review": _l("Käsittelyssä"),
}


CASE_ACTIONS = {
    "created": _l("Luotu"),
    "note": _l("Muistiinpano"),
    "follow_up": _l("Seurantatoimi"),
    "contacted": _l("Oltu yhteydessä"),
    "auto_close": _l("Suljettu automaattisesti"),
    "close": _l("Suljettu"),
    "close_case": _l("Suljettu"),
    "reopen_case": _l("Avattu uudelleen"),
    "reject_reason": _l("Hylkäyksen syy"),
    "undo_approval": _l("Hyväksyntä peruttu"),
    "reply_to_ticket": _l("Vastaus lähetetty"),
    "escalate_demo": _l("Eskaloitu"),
    "deescalate": _l("Eskalointi poistettu"),
    "approve_cancellation": _l("Peruutus hyväksytty"),
    "cancellation_accepted": _l("Peruutus hyväksytty"),
    "decline_cancellation": _l("Peruutuspyyntö hylätty"),
}


EVENT_TYPES = {
    "march": _l("Marssi"),
    "stay_still": _l("Paikallaan"),
    "road_block": _l("Tieblokki"),
    "other": _l("Muut"),
}


def event_type_label(value):
    return str(EVENT_TYPES.get(str(value or "").lower(), _readable(value)))


def _readable(value):
    return str(value or "").replace("_", " ").strip().capitalize() or "-"


def case_type_label(value):
    return str(CASE_TYPES.get(value, _readable(value)))


def urgency_label(value):
    return str(URGENCY.get(str(value or "").lower(), _readable(value)))


def task_status_label(value):
    return str(TASK_STATUS.get(value, _readable(value)))


def case_action_label(value):
    """Known codes get a Finnish label; free text written by people stays as is."""
    if value in CASE_ACTIONS:
        return str(CASE_ACTIONS[value])
    text = str(value or "")
    return _readable(text) if text and text == text.lower() and " " not in text else (text or "-")


def register(app):
    app.jinja_env.filters["case_type_label"] = case_type_label
    app.jinja_env.filters["urgency_label"] = urgency_label
    app.jinja_env.filters["task_status_label"] = task_status_label
    app.jinja_env.filters["case_action_label"] = case_action_label
    app.jinja_env.filters["event_type_label"] = event_type_label
