from datetime import datetime, timezone


def _utcnow() -> datetime:
    """Return the current UTC time as a naive datetime (Postgres-compatible)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
