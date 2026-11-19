# Hallinto OIDC protocol proof

I01 evaluates a separate, library-backed issuer contract for Hallinto. The current Flask MCP authorization server issues access tokens for its admin audience. Its OpenID discovery alias does not add ID tokens, published JWKS or logout/revocation semantics. Keep those paths and existing login behavior intact. [Existing MCP](admin_mcp.md) · [Session bridge](auth-bridge.md)

## Reproduce the synthetic proof

Run an isolated environment with no service credentials or account data.

```sh
python3 -m venv /tmp/hallinto-oidc-proof
/tmp/hallinto-oidc-proof/bin/pip install Authlib==1.9.9 Flask==3.1.3 joserfc==1.8.0 pytest==9.2.0
/tmp/hallinto-oidc-proof/bin/python -m pytest -q scripts/verification/hallinto_oidc_proof.py
```

Five tests cover a confidential authorization-code client, discovery, public JWKS, RS256 identity issuance, precise redirect matching, replay, mandatory S256 and nonce, wrong verifier, callback state, wrong issuer/audience and overlapping signing-key rotation. The provider lives inside Flask's test client, signs with ephemeral keys, and uses a single synthetic subject. Its maps and code lifecycle are test fixtures. It runs independently of Flask application startup, protected account paths and the current MCP OAuth routes.

## What I02 must establish

- Register a separate issuer and fixed HTTPS callbacks. Pin discovery and issuer in the Go relying party. Use a maintained Go OIDC verifier. Validate ID token audience and authorized party when applicable, state/nonce, algorithm, key rotation and expiry. Map exact `(issuer, subject)` to the immutable account identifier.
- Authlib's code challenge extension does not by itself require an S256 challenge at authorization time for a confidential client. The fixture installs an explicit S256 check. The production design needs its own test that omitted/downgraded PKCE and redirect mismatches fail before code issuance.
- The library exposes hooks for issuer claims and code storage, but the existing security owner must supply the actual authenticated identity and MFA assurance. In particular, library defaults can supply `auth_time` at token issuance. That is insufficient for claiming a fresh login or step-up. Supply evidence from the existing authenticated session and keep privileged execution proof separate and bound to exact intent.
- Bind codes transactionally to their registered client, redirect, verifier, nonce, original session and expiry. Consume atomically. Add owner-backed revocation with proof for logout, account suspension, factor changes and loss of freshness, and use host-only Hallinto sessions. Stage tests must cross the real Flask session boundary and exercise simultaneous code exchanges, consent/CSRF and revocation propagation.
- The synthetic proof covers protocol behavior, not a full conformance suite, production identity availability, assurance issuance, durable replay protection, or the Go relying party. Review the issuer namespace, authentication owner checks, key custody and rollout under A2 before live activation. Disable only Hallinto routing and revoke its clients/sessions on rollback.

The next implementation unit is the disabled-by-default Flask owner bridge and Go relying-party verification against the same synthetic protocol. No production OIDC route is introduced by this proof.
