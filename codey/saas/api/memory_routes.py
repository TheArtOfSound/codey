from __future__ import annotations

import json
import math
from datetime import datetime
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.auth.dependencies import get_current_user
from codey.saas.database import get_db
from codey.saas.memory.engine import MemoryEngine
from codey.saas.models import MemoryUpdateLog, User, UserMemory

router = APIRouter(prefix="/memory", tags=["memory"])

FIELD_BY_DIMENSION = {
    "language_preferences": "structural_preferences",
    "coding_style": "style_model",
    "communication": "communication_style",
    "project_context": "project_knowledge",
    "error_patterns": "project_knowledge",
    "workflow": "work_patterns",
    "personal": "explicit_preferences",
}

DIMENSION_BY_FIELD = {
    "structural_preferences": "language_preferences",
    "style_model": "coding_style",
    "communication_style": "communication",
    "project_knowledge": "project_context",
    "work_patterns": "workflow",
    "skill_profile": "project_context",
    "explicit_preferences": "personal",
}


class MemoryItemResponse(BaseModel):
    id: str
    dimension: str
    key: str
    value: str
    updated_at: str


class MemoryTimelineEntryResponse(BaseModel):
    id: str
    dimension: str
    action: str
    key: str
    value: str
    timestamp: str


class CreateMemoryItemRequest(BaseModel):
    dimension: str
    key: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=5000)

    @field_validator("key")
    @classmethod
    def _strip_and_validate_key(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        if _has_ascii_control(value):
            raise ValueError("must not contain control characters")
        return value

    @field_validator("value")
    @classmethod
    def _strip_and_validate_non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class UpdateMemoryItemRequest(BaseModel):
    value: str = Field(min_length=1, max_length=5000)

    @field_validator("value")
    @classmethod
    def _strip_and_validate_non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


def _encode_id(field_name: str, key: str) -> str:
    return f"{field_name}:{quote(key, safe='')}"


def _has_ascii_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _coerce_memory_log_text(value: object, fallback: str = "") -> str:
    if not isinstance(value, str):
        return fallback
    value = value.strip()
    if not value or _has_ascii_control(value):
        return fallback
    return value


def _decode_id(item_id: str) -> tuple[str, str]:
    field_name, sep, encoded_key = item_id.partition(":")
    if not sep or not field_name or not encoded_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid memory item id",
        )
    if field_name not in DIMENSION_BY_FIELD:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid memory item id",
        )

    key = unquote(encoded_key)
    if not key or _has_ascii_control(key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid memory item id",
        )
    if field_name == "explicit_preferences":
        try:
            index = int(key)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid memory item id",
            ) from exc
        if index < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid memory item id",
            )
        key = str(index)

    return field_name, key


def _stringify_value(value: object) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return "0.0"
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        return json.dumps(
            _json_safe_memory_value(value, _coerce_unknown=False),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return str(value)


def _json_safe_memory_value(
    value: object,
    _seen: set[int] | None = None,
    *,
    _coerce_unknown: bool = True,
) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else 0.0
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if _seen is None:
        _seen = set()
    if isinstance(value, dict):
        value_id = id(value)
        if value_id in _seen:
            return "[Circular]"
        _seen.add(value_id)
        try:
            return {
                str(key): _json_safe_memory_value(item, _seen)
                for key, item in value.items()
            }
        finally:
            _seen.remove(value_id)
    if isinstance(value, (set, frozenset)):
        value_id = id(value)
        if value_id in _seen:
            return "[Circular]"
        _seen.add(value_id)
        try:
            return [
                _json_safe_memory_value(item, _seen)
                for item in sorted(
                    value,
                    key=lambda item: (type(item).__name__, repr(item)),
                )
            ]
        finally:
            _seen.remove(value_id)
    if isinstance(value, (list, tuple)):
        value_id = id(value)
        if value_id in _seen:
            return "[Circular]"
        _seen.add(value_id)
        try:
            return [_json_safe_memory_value(item, _seen) for item in value]
        finally:
            _seen.remove(value_id)
    return str(value) if _coerce_unknown else value


def _coerce_memory_bucket(value: object) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (list, tuple)):
        try:
            return dict(value)
        except (TypeError, ValueError):
            return {}
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return {}
        try:
            parsed = json.loads(normalized)
        except ValueError:
            return {}
        if isinstance(parsed, dict):
            return dict(parsed)
    return {}


def _coerce_memory_preferences(value: object) -> list:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return []
        try:
            parsed = json.loads(normalized)
        except ValueError:
            return []
        if isinstance(parsed, list):
            return list(parsed)
    return []


def _coerce_memory_int(value: object, fallback: int = 0) -> int:
    normalized: float
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        normalized = value
    elif isinstance(value, str):
        try:
            normalized = float(value.strip())
        except ValueError:
            return fallback
    else:
        return fallback
    return int(normalized) if math.isfinite(normalized) else fallback


def _coerce_memory_row_list(value: object) -> list[object]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _serialize_memory_timestamp(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return str(value)


async def _get_or_create_memory(user_id, db: AsyncSession) -> UserMemory:
    memory = await db.get(UserMemory, user_id)
    if memory is None:
        memory = UserMemory(user_id=user_id)
        db.add(memory)
        await db.flush()
    return memory


async def _log_memory_update(
    db: AsyncSession,
    *,
    user_id,
    update_type: str,
    field_updated: str,
    previous_value: dict | None = None,
    new_value: dict | None = None,
    source_description: str | None = None,
) -> None:
    log_entry = MemoryUpdateLog(
        user_id=user_id,
        update_type=update_type,
        field_updated=field_updated,
        previous_value=previous_value,
        new_value=new_value,
        source_description=source_description,
    )
    db.add(log_entry)


def _flatten_memory(memory: UserMemory) -> list[MemoryItemResponse]:
    items: list[MemoryItemResponse] = []
    updated_at = _serialize_memory_timestamp(
        getattr(memory, "last_updated", None)
    ) or ""

    for field_name in (
        "style_model",
        "work_patterns",
        "project_knowledge",
        "communication_style",
        "structural_preferences",
        "skill_profile",
    ):
        bucket = _coerce_memory_bucket(getattr(memory, field_name, None))
        dimension = DIMENSION_BY_FIELD.get(field_name, "project_context")
        normalized_items: list[tuple[str, object]] = []
        for raw_key, raw_value in bucket.items():
            if isinstance(raw_key, str):
                key = raw_key.strip()
            elif raw_key is None:
                key = ""
            else:
                key = str(raw_key).strip()
            if not key or _has_ascii_control(key):
                continue
            normalized_items.append((key, raw_value))

        for key, value in sorted(normalized_items, key=lambda item: item[0]):
            items.append(
                MemoryItemResponse(
                    id=_encode_id(field_name, key),
                    dimension=dimension,
                    key=key,
                    value=_stringify_value(value),
                    updated_at=updated_at,
                )
            )

    for index, preference in enumerate(
        _coerce_memory_preferences(getattr(memory, "explicit_preferences", None))
    ):
        label = f"Preference {index + 1}"
        items.append(
            MemoryItemResponse(
                id=_encode_id("explicit_preferences", str(index)),
                dimension="personal",
                key=label,
                value=_stringify_value(preference),
                updated_at=updated_at,
            )
        )

    return items


@router.get("", response_model=list[MemoryItemResponse])
async def list_memory(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[MemoryItemResponse]:
    memory = await _get_or_create_memory(current_user.id, db)
    return _flatten_memory(memory)


@router.get("/timeline", response_model=list[MemoryTimelineEntryResponse])
async def get_memory_timeline(
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[MemoryTimelineEntryResponse]:
    if not isinstance(limit, int):
        default_limit = getattr(limit, "default", 20)
        limit = default_limit if isinstance(default_limit, int) else 20

    stmt = (
        select(MemoryUpdateLog)
        .where(MemoryUpdateLog.user_id == current_user.id)
        .order_by(MemoryUpdateLog.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    logs = _coerce_memory_row_list(result.scalars().all())

    timeline: list[MemoryTimelineEntryResponse] = []
    for log in logs:
        field_name = _coerce_memory_log_text(
            getattr(log, "field_updated", None),
            "unknown",
        )
        dimension = DIMENSION_BY_FIELD.get(field_name, "personal")
        update_type = _coerce_memory_log_text(getattr(log, "update_type", None))
        action = "updated"
        if "add" in update_type:
            action = "added"
        elif "delete" in update_type or "reset" in update_type:
            action = "removed"

        payload = _coerce_memory_bucket(getattr(log, "new_value", None))
        if not payload:
            payload = _coerce_memory_bucket(getattr(log, "previous_value", None))
        raw_key = payload.get("key")
        if raw_key is None:
            raw_key = payload.get("index")
        if raw_key is None:
            raw_key = field_name

        raw_value = payload.get("value")
        if raw_value is None:
            raw_value = payload.get("preference")
        if raw_value is None:
            raw_value = ""

        key = str(raw_key)
        value = _stringify_value(raw_value)
        timeline.append(
            MemoryTimelineEntryResponse(
                id=str(getattr(log, "id", "")),
                dimension=dimension,
                action=action,
                key=key,
                value=value,
                timestamp=_serialize_memory_timestamp(
                    getattr(log, "created_at", None)
                ) or "",
            )
        )

    return timeline


@router.post("", response_model=MemoryItemResponse, status_code=status.HTTP_201_CREATED)
async def create_memory_item(
    body: CreateMemoryItemRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MemoryItemResponse:
    field_name = FIELD_BY_DIMENSION.get(body.dimension)
    if field_name is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported memory dimension",
        )

    memory = await _get_or_create_memory(current_user.id, db)

    if field_name == "explicit_preferences":
        await MemoryEngine.add_explicit_preference(current_user.id, body.value, db)
        await db.flush()
        memory = await _get_or_create_memory(current_user.id, db)
        index = len(
            _coerce_memory_preferences(getattr(memory, "explicit_preferences", None))
        ) - 1
        return MemoryItemResponse(
            id=_encode_id(field_name, str(index)),
            dimension=body.dimension,
            key=body.key,
            value=body.value,
            updated_at=_serialize_memory_timestamp(
                getattr(memory, "last_updated", None)
            )
            or "",
        )

    memory.last_updated = datetime.utcnow()
    memory.memory_version = (
        _coerce_memory_int(getattr(memory, "memory_version", None), 0) + 1
    )
    bucket = _coerce_memory_bucket(getattr(memory, field_name, None))
    previous_value = bucket.get(body.key)
    bucket[body.key] = body.value.strip()
    setattr(memory, field_name, bucket)

    await _log_memory_update(
        db,
        user_id=current_user.id,
        update_type="manual_add" if previous_value is None else "manual_update",
        field_updated=field_name,
        previous_value={"key": body.key, "value": previous_value} if previous_value is not None else None,
        new_value={"key": body.key, "value": body.value.strip()},
        source_description="Manual memory edit",
    )
    await db.flush()

    return MemoryItemResponse(
        id=_encode_id(field_name, body.key),
        dimension=body.dimension,
        key=body.key,
        value=body.value.strip(),
        updated_at=_serialize_memory_timestamp(
            getattr(memory, "last_updated", None)
        )
        or "",
    )


@router.patch("/{item_id}", response_model=MemoryItemResponse)
async def update_memory_item(
    item_id: str,
    body: UpdateMemoryItemRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MemoryItemResponse:
    field_name, key = _decode_id(item_id)
    memory = await _get_or_create_memory(current_user.id, db)

    if field_name == "explicit_preferences":
        prefs = _coerce_memory_preferences(
            getattr(memory, "explicit_preferences", None)
        )
        index = int(key)
        if index < 0 or index >= len(prefs):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Memory item not found",
            )
        memory.last_updated = datetime.utcnow()
        memory.memory_version = (
            _coerce_memory_int(getattr(memory, "memory_version", None), 0) + 1
        )
        previous_value = prefs[index]
        prefs[index] = body.value.strip()
        memory.explicit_preferences = prefs
        await _log_memory_update(
            db,
            user_id=current_user.id,
            update_type="manual_update",
            field_updated=field_name,
            previous_value={"index": index, "preference": previous_value},
            new_value={"index": index, "preference": body.value.strip()},
            source_description="Manual memory edit",
        )
        await db.flush()
        return MemoryItemResponse(
            id=item_id,
            dimension="personal",
            key=f"Preference {index + 1}",
            value=body.value.strip(),
            updated_at=_serialize_memory_timestamp(
                getattr(memory, "last_updated", None)
            )
            or "",
        )

    bucket = _coerce_memory_bucket(getattr(memory, field_name, None))
    if key not in bucket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory item not found",
        )
    memory.last_updated = datetime.utcnow()
    memory.memory_version = (
        _coerce_memory_int(getattr(memory, "memory_version", None), 0) + 1
    )
    previous_value = bucket[key]
    bucket[key] = body.value.strip()
    setattr(memory, field_name, bucket)
    await _log_memory_update(
        db,
        user_id=current_user.id,
        update_type="manual_update",
        field_updated=field_name,
        previous_value={"key": key, "value": previous_value},
        new_value={"key": key, "value": body.value.strip()},
        source_description="Manual memory edit",
    )
    await db.flush()
    return MemoryItemResponse(
        id=item_id,
        dimension=DIMENSION_BY_FIELD.get(field_name, "project_context"),
        key=key,
        value=body.value.strip(),
        updated_at=_serialize_memory_timestamp(
            getattr(memory, "last_updated", None)
        )
        or "",
    )


@router.delete("/all", status_code=status.HTTP_204_NO_CONTENT)
async def reset_all_memory(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    try:
        await MemoryEngine.reset_memory(current_user.id, db)
    except ValueError:
        await _get_or_create_memory(current_user.id, db)
        await MemoryEngine.reset_memory(current_user.id, db)
    await db.flush()


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory_item(
    item_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    field_name, key = _decode_id(item_id)
    memory = await _get_or_create_memory(current_user.id, db)

    if field_name == "explicit_preferences":
        index = int(key)
        try:
            await MemoryEngine.delete_preference(current_user.id, index, db)
        except IndexError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Memory item not found",
            ) from exc
        await db.flush()
        return

    bucket = _coerce_memory_bucket(getattr(memory, field_name, None))
    if key not in bucket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory item not found",
        )
    memory.last_updated = datetime.utcnow()
    memory.memory_version = (
        _coerce_memory_int(getattr(memory, "memory_version", None), 0) + 1
    )
    removed_value = bucket.pop(key)
    setattr(memory, field_name, bucket)
    await _log_memory_update(
        db,
        user_id=current_user.id,
        update_type="manual_delete",
        field_updated=field_name,
        previous_value={"key": key, "value": removed_value},
        source_description="Manual memory delete",
    )
    await db.flush()
