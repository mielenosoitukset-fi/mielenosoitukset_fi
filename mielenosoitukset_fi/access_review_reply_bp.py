"""Holder's answer page for the admin account review: ``/ylla-vastaus/<token>``.

The mail asking "do you still need your rights?" carries a signed link (utils/access_review.reply_token).
The link needs no sign-in: it works for one account and one review round and only records an answer, so
a forwarded link can say "I give up my rights" but can never grant anything. Opening the page changes
nothing (prefetch safe); only the form's POST records the answer, in the same fields Hallinto reads.
"""

from flask import Blueprint, Response, render_template, request

from mielenosoitukset_fi.utils import access_review
from mielenosoitukset_fi.admin.utils import mongo

bp = Blueprint("access_review_reply", __name__)

# What the holder can pick (no_answer and unreachable are for the reviewer to record).
CHOICES = ("needs", "keep_idle", "partial", "give_up", "inactive")


def _private(response):
    """The token is in the URL: keep it out of caches, indexes and referrers."""
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _page(template, status=200, **context):
    return _private(Response(render_template(f"access_review/{template}", **context), status))


@bp.route("/ylla-vastaus/<token>", methods=["GET", "POST"])
def reply_page(token):
    db = mongo
    doc = access_review.reply_doc(db, token)
    if not doc:
        return _page("invalid.html", 404)
    roles = access_review.removable_roles(db, doc)
    role_labels = {r: access_review.SITE_ROLES.get(r, r) for r in roles}
    options = [(k, access_review.SITUATIONS[k][1].capitalize()) for k in CHOICES if k != "partial" or len(roles) > 1]
    context = dict(options=options, role_labels=role_labels, holds=access_review.holds_labels(doc),
                   current=access_review.contact_view(doc[access_review.FIELD]), error=None, token=token)
    if request.method == "GET":
        return _page("reply.html", **context)
    situation = (request.form.get("situation") or "").strip()
    picked = [r for r in request.form.getlist("roles") if r in roles]
    until = (request.form.get("until") or "").strip() if situation == "inactive" else ""
    try:
        if situation not in CHOICES:
            raise access_review.ContactError("invalid_request", 400)
        access_review.contact_reply(
            db, str(doc["_id"]), None, doc.get("username") or "", (request.form.get("note") or "").strip()[:500],
            situation=situation, roles=picked if situation == "partial" else [], until=until, via="link")
    except access_review.ContactError:
        context["error"] = ("Tarkista vastaus: valitse vaihtoehto" + (", ainakin yksi pidettävä oikeus" if situation == "partial" else "")
                            + (" ja päivämäärä, johon asti olet poissa" if situation == "inactive" else "") + ".")
        return _page("reply.html", 400, **context)
    return _page("done.html", situation=dict(options).get(situation, ""), roles=[role_labels[r] for r in picked], until=until)
