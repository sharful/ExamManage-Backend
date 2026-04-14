import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


def _serialize(value: Any) -> Any:
    """Convert non-JSON-serializable types to serializable equivalents."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "value"):  # enums
        return value.value
    return value


def _serialize_dict(d: dict) -> dict:
    return {k: _serialize(v) for k, v in d.items()}


async def log_action(
    db: AsyncSession,
    user_id: uuid.UUID | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    old_values: dict | None = None,
    new_values: dict | None = None,
) -> None:
    """
    Append an entry to audit_logs.

    action      : "create" | "update" | "delete"
    entity_type : e.g. "invigilator", "room", "exam", "assignment"
    old_values  : changed fields BEFORE the operation (None for create)
    new_values  : changed fields AFTER the operation  (None for delete)
    """
    entry = AuditLog(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        old_values=_serialize_dict(old_values) if old_values is not None else None,
        new_values=_serialize_dict(new_values) if new_values is not None else None,
    )
    db.add(entry)
