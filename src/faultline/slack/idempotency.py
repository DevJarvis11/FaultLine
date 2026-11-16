"""Idempotency keys (DESIGN: Persistence, idempotency and concurrency).

- `action_key`: one per (incident, action kind, target); the `actions.action_key` column.
- `decision_key`: one decision per action; passed to the workflow resume so a retried
  resume for the same action is a no-op.
"""

import hashlib
import json
from uuid import UUID

from faultline.schemas import ActionKind, ActionTarget


def action_key(incident_id: UUID, kind: ActionKind, target: ActionTarget) -> str:
    """SHA-256 hex of the canonical (incident, kind, target) tuple; unset fields are ignored."""
    canonical = json.dumps(
        {
            "incident_id": str(incident_id),
            "kind": kind,
            "target": target.model_dump(mode="json", exclude_none=True),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def decision_key(action_id: UUID) -> str:
    return f"decision:{action_id}"
