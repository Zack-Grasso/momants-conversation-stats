from datetime import datetime, timezone


def format_momants_datetime(value: datetime) -> str:
    """Format datetimes for Momants inbox start_date/end_date query params."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")
