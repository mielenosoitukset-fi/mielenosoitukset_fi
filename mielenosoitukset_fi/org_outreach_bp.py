"""Public opt-out page for outbound organization invitations (O6).

``GET /organisaatiot/ei-kutsuja?t=<token>`` shows a confirm button and changes
nothing, so a mail scanner that prefetches the link cannot opt anyone out.
``POST`` records the opt-out (also the RFC 8058 one-click request that mail
programs send). The token is unguessable and only identifies an invitation.
"""

from __future__ import annotations

from flask import Blueprint, make_response, render_template, request

from mielenosoitukset_fi.admin import org_outreach

org_outreach_bp = Blueprint("org_outreach", __name__)


def build_blueprint(db) -> Blueprint:
    bp = Blueprint("org_outreach", __name__)

    def page(state: str, status: int = 200):
        response = make_response(render_template("organizations/outreach_optout.html", state=state,
                                                 token=request.values.get("t", "")), status)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Robots-Tag"] = "noindex"
        return response

    @bp.get("/organisaatiot/ei-kutsuja")
    def optout_page():
        token = request.args.get("t", "")
        return page("confirm" if org_outreach.optout_target(db, token) else "unknown",
                    200 if org_outreach.optout_target(db, token) else 404)

    @bp.post("/organisaatiot/ei-kutsuja")
    def optout_confirm():
        token = request.values.get("t", "")
        if not org_outreach.record_optout(db, token):
            return page("unknown", 404)
        return page("done")

    return bp
