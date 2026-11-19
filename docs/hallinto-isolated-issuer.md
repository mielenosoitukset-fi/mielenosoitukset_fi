# Isolated Hallinto OIDC issuer

This development slice registers an issuer only when the Flask app is in test mode and its factory receives explicit `HALLINTO_OIDC` overrides. It uses the existing sign-in and sign-out routes, one registered confidential client, exact HTTPS redirect, S256 and short-lived, single-use authorization codes. The ID token carries an opaque owner login reference. The confidential `/hallinto-oidc/session` check validates the reference against the live account, epoch, credentials and per-browser revocation.

Run `python -m pytest -q tests/test_hallinto_issuer.py tests/test_hallinto_sessions.py tests/test_logout_post.py` with the development dependencies and the existing disposable test database. Coverage includes real Flask sign-in and sign-out, separate browsers, concurrent token exchange, invalid redirects and PKCE, account invalidation and disabled-by-default routes.

Production preparation still needs reviewed non-test configuration, a production dependency install, restricted key and client-secret provisioning, expiration indexes, proxy configuration and the live approval gate. The production Flask account data stays on lc-db. Hallinto receives only the authenticated identity and eligibility response and uses separate persistence for its records. Existing permission execution remains with its current owner.
