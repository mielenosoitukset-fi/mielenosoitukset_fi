"""Tell whether the current address carries a secret, such as a one-time link token."""

from flask import has_request_context, request

# Query keys that hold a secret in links this site mails out.
_SECRET_QUERY_KEYS = frozenset({"token", "code", "key", "invite", "signature", "sig"})


def url_carries_secret() -> bool:
    """True when the path or query string holds a token, so third parties must not see the URL."""
    if not has_request_context():
        return False
    view_args = request.view_args or {}
    if any("token" in name.lower() for name in view_args):
        return True
    return any(name.lower() in _SECRET_QUERY_KEYS or "token" in name.lower() for name in request.args)
