# Emergency scripts

These bypass the admin UI and Hallinto. Use only when no administrator can act.
Each run needs `EMERGENCY_GRANT_ALLOWED=1`, a named actor, a written reason and a
repeated target email, and is recorded in `audit_events` and `admin_logs`.
See `grant_rights.py --help`. The old root scripts `grant_admin_rights.py` and
`grant_all_rights.py` are removed (they had no audit, a hard-coded email and an
invalid `superuser` role).
