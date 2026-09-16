"""Describe one dashboard build without changing its metric payload."""
from datetime import datetime, timezone
from uuid import uuid4


VERSION = "display-evidence-v1"


def snapshot(payload, start, end, collection_start):
    """Attach build and window identity; source timestamps remain authoritative."""
    payload["display_snapshot"] = {
        "version": VERSION,
        "id": str(uuid4()),
        "schema_version": payload["schema_version"],
        "collection_start": collection_start.isoformat(),
        "collection_end": datetime.now(timezone.utc).isoformat(),
        "consistency": "sources collected independently",
        "window": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "timezone": "UTC",
        },
        "reports_period": "ISO week; independent report snapshot",
    }
    return payload
