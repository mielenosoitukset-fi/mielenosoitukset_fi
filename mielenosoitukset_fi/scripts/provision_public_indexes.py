"""Provision public query indexes explicitly, before application worker reload."""

from mielenosoitukset_fi.database_manager import DatabaseManager
from mielenosoitukset_fi.utils.public_pagination import ensure_public_demo_pagination_index
from mielenosoitukset_fi.utils.step_up import ensure_step_up_failure_indexes


def ensure_organization_request_indexes(db):
    """Drop unconfirmed organization requests once their link expires.

    Go sets ``expires_at`` only on unconfirmed requests and unsets it when the
    address is confirmed (go-api/org_request.go), so confirmed ones are kept.
    """
    db.organization_requests.create_index(
        "expires_at", expireAfterSeconds=0, name="organization_requests_unconfirmed_ttl"
    )


def main():
    """Fail deployment on provisioning errors without interrupting old workers."""
    db = DatabaseManager.get_instance().get_db()
    ensure_public_demo_pagination_index(db)
    ensure_step_up_failure_indexes(db)
    ensure_organization_request_indexes(db)


if __name__ == "__main__":
    main()
