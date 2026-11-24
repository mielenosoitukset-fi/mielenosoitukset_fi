"""One place that turns a template into the finished HTML and plain text of an email.

Every sender (queue, send now, background delivery scripts) uses this, so each
message gets the shared layout, a real plain-text part and, for staff mail, the
open pixel.
"""
from .open_tracking import add_pixel, is_tracked
from .polish import html_to_text, polish_html


def render_email(env, db, template_name, context, track=None):
    """Return ``(html, text)`` for ``template_name``.

    ``db`` may be ``None`` (no pixel is added then). ``track`` may link the open
    to a case, staff account or demonstration; see ``open_tracking.add_pixel``.
    """
    html = polish_html(env.get_template(template_name).render(context))
    if db is not None and is_tracked(template_name):
        html = add_pixel(db, html, template_name, meta=track)
    return html, html_to_text(html)
