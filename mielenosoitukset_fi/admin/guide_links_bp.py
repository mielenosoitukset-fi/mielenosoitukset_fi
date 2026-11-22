"""Ohjeet: the personal link page and the admin page for sending links.

``/ohjeet/k/<token>`` shows the role guides to the person the link belongs to.
``/admin/ohjeet/`` shows the same guides to a signed-in volunteer, lets them
mail themselves a fresh link and lets a user editor send one to someone else.
Rules and tokens live in ``utils/guide_links.py``.
"""

import hmac

from bson import ObjectId
from flask import Blueprint, Response, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from mielenosoitukset_fi.emailer.EmailSender import EmailSender
from mielenosoitukset_fi.users.models import User
from mielenosoitukset_fi.utils import guide_links
from mielenosoitukset_fi.utils.logger import logger

from .board_compliance import governance_csrf_token
from .utils import _ADMIN_TEMPLATE_FOLDER, mongo

guide_links_bp = Blueprint("guide_links", __name__)
_SENDER = None


def _sender():
    global _SENDER
    if _SENDER is None:
        _SENDER = EmailSender()
    return _SENDER


def _load_user(user_id):
    try:
        user = User.from_OID(user_id)
        user.apply_active_duty()
        return user
    except Exception:  # noqa: BLE001 - an unknown account is just "no link"
        return None


def _private(response):
    """The token is in the URL: keep it out of caches, indexes and referrers."""
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def link_url(token):
    return url_for("guide_links.personal_page", token=token, _external=True)


def send_link(db, account_doc, actor):
    """Issue a new link and queue it by mail to the account's own address."""
    email = (account_doc.get("email") or "").strip()
    if not email:
        return False
    token = guide_links.issue_link(db, account_doc["_id"], actor=actor)
    _sender().queue_email(
        template_name="guide_link.html", subject="Henkilökohtainen linkkisi ohjevideoihin",
        recipients=[email],
        context={"user_name": account_doc.get("displayname") or account_doc.get("username") or "",
                 "guides_url": link_url(token)},
    )
    return True


@guide_links_bp.route("/ohjeet/k/", methods=["GET", "POST"])
def code_form():
    """For someone who has only the code, not the link."""
    if request.method == "POST":
        code = (request.form.get("code") or "").strip().rsplit("/", 1)[-1]
        if code:
            return redirect(url_for("guide_links.personal_page", token=code), code=303)
    return _private(Response(render_template("guide_links/code.html"), 200))


@guide_links_bp.route("/ohjeet/k/<token>")
def personal_page(token):
    user = guide_links.resolve_link(mongo, token, _load_user)
    if not user:
        return _private(Response(render_template("guide_links/invalid.html"), 404))
    return _private(Response(render_template(
        "guide_links/page.html", groups=guide_links.guides_for(user), name=user.displayname or user.username), 200))


@guide_links_bp.route("/admin/ohjeet/", methods=["GET", "POST"])
@login_required
def admin_page():
    if not current_user.has_admin_access():
        abort(403)
    can_send_to_others = current_user.has_permission("EDIT_USER")
    if request.method == "POST":
        expected = session.get("governance_csrf_token", "")
        if not expected or not hmac.compare_digest(expected, request.form.get("csrf_token", "")):
            abort(403)
        username = (request.form.get("username") or "").strip().lower()
        if username and not can_send_to_others:
            abort(403)
        query = {"username_canonical": username} if username else {"_id": ObjectId(str(current_user.id))}
        account = mongo.users.find_one(query)
        if not account or not guide_links.audiences_for(_load_user(account["_id"])):
            flash("Käyttäjää ei löytynyt tai hänellä ei ole roolia, johon ohjeet kuuluvat.", "danger")
        elif guide_links.sent_recently(mongo, account["_id"]):
            flash("Linkki lähetettiin juuri. Odota hetki ennen kuin lähetät uuden.", "warning")
        elif send_link(mongo, account, actor=current_user.username):
            logger.info("guide link sent to %s by %s", account.get("username"), current_user.username)
            flash("Linkki lähetettiin sähköpostiin.", "success")
        else:
            flash("Tilillä ei ole sähköpostiosoitetta.", "danger")
        return redirect(url_for("guide_links.admin_page"), code=303)
    return render_template(
        f"{_ADMIN_TEMPLATE_FOLDER}guides.html", groups=guide_links.guides_for(current_user),
        can_send_to_others=can_send_to_others, governance_csrf_token=governance_csrf_token())
