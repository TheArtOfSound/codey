"""Memory Engine — learns user preferences, style, and project knowledge from sessions.

Runs after every coding session to extract signals and build a persistent user
model that the LLM system prompt can reference for personalized behavior.
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from codey.saas.models.coding_session import CodingSession
from codey.saas.models.memory_update_log import MemoryUpdateLog
from codey.saas.models.user_memory import UserMemory

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Extraction keywords and heuristics
# ---------------------------------------------------------------------------

_STYLE_SIGNALS = {
    "concise": ["brief", "concise", "short", "terse", "minimal"],
    "verbose": ["verbose", "detailed", "explain", "thorough"],
    "formal": ["formal", "professional", "enterprise"],
    "casual": ["casual", "chill", "relaxed"],
}

_FRAMEWORK_KEYWORDS = [
    "react", "vue", "angular", "svelte", "next", "nuxt", "django", "flask",
    "fastapi", "express", "rails", "spring", "laravel", "tailwind", "bootstrap",
]

_LANGUAGE_KEYWORDS = [
    "python", "javascript", "typescript", "rust", "go", "java", "kotlin",
    "swift", "ruby", "php", "c++", "c#", "elixir", "haskell",
]

REFERRAL_CREDITS = 5
"""Credits awarded per referral conversion."""


class MemoryEngine:
    """Stateless service — all state lives in the database."""

    # ------------------------------------------------------------------
    # Post-session extraction
    # ------------------------------------------------------------------

    @staticmethod
    async def run_memory_extraction(
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        db: AsyncSession,
    ) -> UserMemory:
        """Analyze a completed session and update the user's memory model.

        Extracts:
        - style signals (conciseness, formality)
        - corrections (user pushed back on something)
        - explicit preferences ("always use …", "never …")
        - project knowledge (languages, frameworks, file patterns)
        - work patterns (session length, time-of-day, modes used)
        """
        session = await db.get(CodingSession, session_id)
        if session is None:
            raise ValueError(f"Session {session_id} not found")

        memory = await _get_or_create_memory(user_id, db)
        memory_version = _coerce_memory_int(
            getattr(memory, "memory_version", None), 0
        )
        total_sessions = _coerce_memory_int(
            getattr(memory, "total_sessions_analyzed", None), 0
        )

        prompt = getattr(session, "prompt", None)
        output_summary = getattr(session, "output_summary", None)
        prompt_text = prompt.lower() if isinstance(prompt, str) else ""
        output_text = (
            output_summary.lower() if isinstance(output_summary, str) else ""
        )
        combined = f"{prompt_text} {output_text}"

        # --- Style extraction ---
        style = _coerce_memory_mapping(getattr(memory, "style_model", None))
        for label, keywords in _STYLE_SIGNALS.items():
            hits = sum(1 for kw in keywords if kw in combined)
            if hits > 0:
                prev = style.get(label, 0.0)
                # Exponential moving average — new signal has 30% weight
                style[label] = round(prev * 0.7 + min(hits / 3, 1.0) * 0.3, 4)
        memory.style_model = style

        # --- Communication style ---
        comm = _coerce_memory_mapping(
            getattr(memory, "communication_style", None)
        )
        if any(w in combined for w in ["don't explain", "no comments", "just code"]):
            comm["prefers_code_only"] = True
        if any(w in combined for w in ["explain", "walk me through", "why"]):
            comm["prefers_explanations"] = True
        if any(w in combined for w in ["step by step", "one at a time"]):
            comm["prefers_incremental"] = True
        memory.communication_style = comm

        # --- Structural preferences ---
        structural = _coerce_memory_mapping(
            getattr(memory, "structural_preferences", None)
        )
        if "tabs" in combined and "spaces" not in combined:
            structural["indentation"] = "tabs"
        elif "spaces" in combined and "tabs" not in combined:
            structural["indentation"] = "spaces"
        if "semicolons" in combined:
            structural["semicolons"] = "no" not in combined
        if "single quotes" in combined:
            structural["quotes"] = "single"
        elif "double quotes" in combined:
            structural["quotes"] = "double"
        memory.structural_preferences = structural

        # --- Project knowledge ---
        project = _coerce_memory_mapping(getattr(memory, "project_knowledge", None))
        detected_langs = [lang for lang in _LANGUAGE_KEYWORDS if lang in combined]
        detected_fw = [fw for fw in _FRAMEWORK_KEYWORDS if fw in combined]
        if detected_langs:
            existing = set(project.get("languages", []))
            existing.update(detected_langs)
            project["languages"] = sorted(existing)
        if detected_fw:
            existing = set(project.get("frameworks", []))
            existing.update(detected_fw)
            project["frameworks"] = sorted(existing)
        memory.project_knowledge = project

        # --- Skill profile ---
        skill = _coerce_memory_mapping(getattr(memory, "skill_profile", None))
        session_mode = getattr(session, "mode", None)
        if isinstance(session_mode, str) and session_mode:
            mode_counts = _coerce_memory_mapping(skill.get("mode_usage"))
            mode_counts[session_mode] = (
                _coerce_memory_int(mode_counts.get(session_mode), 0) + 1
            )
            skill["mode_usage"] = mode_counts
        lines_generated = _coerce_memory_int(
            getattr(session, "lines_generated", None), 0
        )
        if lines_generated > 0:
            total_lines = _coerce_memory_int(skill.get("total_lines_generated"), 0)
            skill["total_lines_generated"] = total_lines + lines_generated
        memory.skill_profile = skill

        # --- Work patterns ---
        patterns = _coerce_memory_mapping(getattr(memory, "work_patterns", None))
        started_at = getattr(session, "started_at", None)
        completed_at = getattr(session, "completed_at", None)
        if isinstance(started_at, datetime):
            hour = started_at.hour
            hour_bucket = "morning" if 5 <= hour < 12 else (
                "afternoon" if 12 <= hour < 17 else (
                    "evening" if 17 <= hour < 21 else "night"
                )
            )
            time_dist = _coerce_memory_mapping(patterns.get("time_distribution"))
            time_dist[hour_bucket] = (
                _coerce_memory_int(time_dist.get(hour_bucket), 0) + 1
            )
            patterns["time_distribution"] = time_dist

        if isinstance(started_at, datetime) and isinstance(completed_at, datetime):
            duration_min = (completed_at - started_at).total_seconds() / 60
            avg = _coerce_memory_float(patterns.get("avg_session_minutes"), 0.0)
            if total_sessions > 0:
                patterns["avg_session_minutes"] = round(
                    (avg * total_sessions + duration_min) / (total_sessions + 1), 1
                )
            else:
                patterns["avg_session_minutes"] = round(duration_min, 1)
        memory.work_patterns = patterns

        # --- Explicit preferences (from "always" / "never" statements) ---
        explicit = _coerce_memory_list(
            getattr(memory, "explicit_preferences", None)
        )
        _extract_explicit_preferences(combined, explicit)
        memory.explicit_preferences = explicit

        # --- Finalize ---
        memory.memory_version = memory_version + 1
        memory.total_sessions_analyzed = total_sessions + 1
        memory.last_updated = datetime.utcnow()

        # Log the update
        log_entry = MemoryUpdateLog(
            user_id=user_id,
            session_id=session_id,
            update_type="session_extraction",
            field_updated="multiple",
            extraction_confidence=0.7,
            source_description=f"Auto-extracted from session {session_id}",
            memory_version_after=memory.memory_version,
        )
        db.add(log_entry)
        await db.flush()

        logger.info(
            "Memory extraction complete for user=%s session=%s version=%d",
            user_id, session_id, memory.memory_version,
        )
        return memory

    # ------------------------------------------------------------------
    # System prompt context builder
    # ------------------------------------------------------------------

    @staticmethod
    async def build_memory_context(user_id: uuid.UUID, db: AsyncSession) -> str:
        """Build the 'WHAT YOU KNOW ABOUT THIS USER' block for the LLM system prompt.

        Returns an empty string if no memory exists yet.
        """
        memory = await db.get(UserMemory, user_id)
        if memory is None:
            return ""

        sections: list[str] = []
        sections.append("## WHAT YOU KNOW ABOUT THIS USER\n")

        # Style
        style_model = _coerce_memory_mapping(getattr(memory, "style_model", None))
        if style_model:
            dominant = sorted(
                (
                    (key, float(value))
                    for key, value in style_model.items()
                    if isinstance(value, (int, float)) and not isinstance(value, bool)
                ),
                key=lambda x: x[1],
                reverse=True,
            )
            top_styles = [f"{k} ({v:.0%})" for k, v in dominant[:3] if v > 0.1]
            if top_styles:
                sections.append(f"**Style tendencies:** {', '.join(top_styles)}")

        # Communication
        communication_style = _coerce_memory_mapping(
            getattr(memory, "communication_style", None)
        )
        if communication_style:
            comm_notes: list[str] = []
            if communication_style.get("prefers_code_only"):
                comm_notes.append("prefers code without lengthy explanations")
            if communication_style.get("prefers_explanations"):
                comm_notes.append("appreciates detailed explanations")
            if communication_style.get("prefers_incremental"):
                comm_notes.append("likes step-by-step walkthroughs")
            if comm_notes:
                sections.append(f"**Communication:** {'; '.join(comm_notes)}")

        # Structural
        structural_preferences = _coerce_memory_mapping(
            getattr(memory, "structural_preferences", None)
        )
        if structural_preferences:
            sp = structural_preferences
            prefs: list[str] = []
            if "indentation" in sp:
                prefs.append(f"uses {sp['indentation']}")
            if "quotes" in sp:
                prefs.append(f"{sp['quotes']} quotes")
            if "semicolons" in sp:
                prefs.append("semicolons" if sp["semicolons"] else "no semicolons")
            if prefs:
                sections.append(f"**Code style:** {', '.join(prefs)}")

        # Project knowledge
        project_knowledge = _coerce_memory_mapping(
            getattr(memory, "project_knowledge", None)
        )
        if project_knowledge:
            pk = project_knowledge
            if pk.get("languages"):
                sections.append(f"**Languages:** {', '.join(pk['languages'])}")
            if pk.get("frameworks"):
                sections.append(f"**Frameworks:** {', '.join(pk['frameworks'])}")

        # Work patterns
        work_patterns = _coerce_memory_mapping(getattr(memory, "work_patterns", None))
        if work_patterns:
            wp = work_patterns
            time_distribution = {
                key: _coerce_memory_float(value, 0.0)
                for key, value in _coerce_memory_mapping(
                    wp.get("time_distribution")
                ).items()
                if isinstance(key, str)
                and key
                and _coerce_memory_float(value, 0.0) > 0.0
            }
            if time_distribution:
                peak = max(time_distribution, key=time_distribution.get)
                sections.append(f"**Peak working time:** {peak}")
            avg_session_minutes = _coerce_memory_float(
                wp.get("avg_session_minutes"), 0.0
            )
            if avg_session_minutes > 0.0:
                sections.append(
                    f"**Avg session length:** {avg_session_minutes:.0f} min"
                )

        # Explicit preferences
        explicit_preferences = _coerce_memory_list(
            getattr(memory, "explicit_preferences", None)
        )
        if explicit_preferences:
            prefs_list = explicit_preferences[:10]
            formatted = "\n".join(f"  - {p}" for p in prefs_list)
            sections.append(f"**Explicit preferences:**\n{formatted}")

        # Skill profile
        skill_profile = _coerce_memory_mapping(getattr(memory, "skill_profile", None))
        modes = {
            key: _coerce_memory_int(value, 0)
            for key, value in _coerce_memory_mapping(
                skill_profile.get("mode_usage")
            ).items()
            if isinstance(key, str) and key and _coerce_memory_int(value, 0) > 0
        }
        if modes:
            mode_str = ", ".join(f"{k}: {v}" for k, v in sorted(
                modes.items(), key=lambda x: x[1], reverse=True
            )[:3])
            sections.append(f"**Favorite modes:** {mode_str}")

        total_sessions = _coerce_memory_int(
            getattr(memory, "total_sessions_analyzed", None), 0
        )
        if total_sessions > 0:
            sections.append(
                f"\n_Based on {total_sessions} sessions analyzed._"
            )

        return "\n".join(sections)

    # ------------------------------------------------------------------
    # Proactive analysis
    # ------------------------------------------------------------------

    @staticmethod
    async def run_proactive_analysis(
        user_id: uuid.UUID,
        db: AsyncSession,
    ) -> list[dict[str, Any]]:
        """Generate daily proactive insights for the user.

        Checks for:
        - Stress trends (long sessions, late nights, error spikes)
        - Repeated patterns (same files, same errors)
        - Security vulnerabilities (hardcoded secrets, outdated deps)
        """
        memory = await db.get(UserMemory, user_id)
        if memory is None:
            return []

        insights: list[dict[str, Any]] = []

        # --- Stress trend detection ---
        wp = _coerce_memory_mapping(getattr(memory, "work_patterns", None))
        time_dist = _coerce_memory_mapping(wp.get("time_distribution"))
        night_count = _coerce_memory_int(time_dist.get("night"), 0)
        total_sessions = max(
            _coerce_memory_int(getattr(memory, "total_sessions_analyzed", None), 0),
            1,
        )
        night_ratio = night_count / total_sessions

        if night_ratio > 0.4 and total_sessions >= 5:
            insights.append({
                "type": "stress_trend",
                "severity": "warning",
                "message": (
                    f"{night_ratio:.0%} of your sessions are late-night. "
                    "Consider scheduling complex work during your peak hours."
                ),
                "data": {"night_ratio": night_ratio, "total_sessions": total_sessions},
            })

        # --- Session length trend ---
        avg_min = _coerce_memory_float(wp.get("avg_session_minutes"), 0.0)
        if avg_min > 120:
            insights.append({
                "type": "session_length",
                "severity": "info",
                "message": (
                    f"Your average session is {avg_min:.0f} minutes. "
                    "Consider breaking work into smaller chunks for better focus."
                ),
                "data": {"avg_minutes": avg_min},
            })

        # --- Repeated error pattern detection ---
        result = await db.execute(
            select(CodingSession)
            .where(CodingSession.user_id == user_id)
            .where(CodingSession.error_message.isnot(None))
            .order_by(CodingSession.started_at.desc())
            .limit(20)
        )
        recent_errors = _coerce_memory_row_list(result.scalars().all())

        if len(recent_errors) >= 5:
            error_messages = [
                message
                for session in recent_errors
                if isinstance((message := getattr(session, "error_message", None)), str)
                and message
            ]
            # Simple duplicate detection via first 50 chars
            prefixes = [m[:50] for m in error_messages]
            from collections import Counter
            repeated = Counter(prefixes).most_common(1)
            if repeated and repeated[0][1] >= 3:
                insights.append({
                    "type": "repeated_error",
                    "severity": "warning",
                    "message": (
                        f"The same error has occurred {repeated[0][1]} times recently. "
                        "This might indicate an underlying issue worth investigating."
                    ),
                    "data": {"error_prefix": repeated[0][0], "count": repeated[0][1]},
                })

        # --- Mode diversity check ---
        skill = _coerce_memory_mapping(getattr(memory, "skill_profile", None))
        mode_usage = _coerce_memory_mapping(skill.get("mode_usage"))
        if len(mode_usage) == 1 and total_sessions >= 10:
            only_mode = list(mode_usage.keys())[0]
            insights.append({
                "type": "mode_diversity",
                "severity": "info",
                "message": (
                    f"You've only used '{only_mode}' mode. "
                    "Other modes like autonomous or review might boost your workflow."
                ),
                "data": {"current_mode": only_mode},
            })

        # Store insights in proactive queue
        if insights:
            memory.proactive_queue = insights
            memory.last_updated = datetime.utcnow()
            await db.flush()

        return insights

    # ------------------------------------------------------------------
    # Manual preference management
    # ------------------------------------------------------------------

    @staticmethod
    async def add_explicit_preference(
        user_id: uuid.UUID,
        preference: str,
        db: AsyncSession,
    ) -> UserMemory:
        """User manually adds a preference to their memory."""
        memory = await _get_or_create_memory(user_id, db)
        prefs = _coerce_memory_list(getattr(memory, "explicit_preferences", None))
        prefs.append(preference.strip())
        memory.explicit_preferences = prefs
        memory.memory_version = (
            _coerce_memory_int(getattr(memory, "memory_version", None), 0) + 1
        )
        memory.last_updated = datetime.utcnow()

        log_entry = MemoryUpdateLog(
            user_id=user_id,
            update_type="explicit_add",
            field_updated="explicit_preferences",
            new_value={"preference": preference.strip()},
            memory_version_after=memory.memory_version,
        )
        db.add(log_entry)
        await db.flush()
        return memory

    @staticmethod
    async def delete_preference(
        user_id: uuid.UUID,
        index: int,
        db: AsyncSession,
    ) -> UserMemory:
        """Delete a preference by its index in the explicit_preferences list."""
        memory = await db.get(UserMemory, user_id)
        if memory is None:
            raise ValueError(f"No memory found for user {user_id}")

        prefs = _coerce_memory_list(getattr(memory, "explicit_preferences", None))
        if index < 0 or index >= len(prefs):
            raise IndexError(
                f"Preference index {index} out of range (0-{len(prefs) - 1})"
            )

        removed = prefs.pop(index)
        memory.explicit_preferences = prefs
        memory.memory_version = (
            _coerce_memory_int(getattr(memory, "memory_version", None), 0) + 1
        )
        memory.last_updated = datetime.utcnow()

        log_entry = MemoryUpdateLog(
            user_id=user_id,
            update_type="explicit_delete",
            field_updated="explicit_preferences",
            previous_value={"preference": removed, "index": index},
            memory_version_after=memory.memory_version,
        )
        db.add(log_entry)
        await db.flush()
        return memory

    @staticmethod
    async def reset_memory(user_id: uuid.UUID, db: AsyncSession) -> UserMemory:
        """Wipe the entire memory model back to defaults."""
        memory = await db.get(UserMemory, user_id)
        if memory is None:
            raise ValueError(f"No memory found for user {user_id}")

        old_version = _coerce_memory_int(getattr(memory, "memory_version", None), 0)

        memory.style_model = {}
        memory.work_patterns = {}
        memory.project_knowledge = {}
        memory.communication_style = {}
        memory.structural_preferences = {}
        memory.skill_profile = {}
        memory.explicit_preferences = []
        memory.proactive_queue = []
        memory.memory_version = old_version + 1
        memory.total_sessions_analyzed = 0
        memory.last_updated = datetime.utcnow()

        log_entry = MemoryUpdateLog(
            user_id=user_id,
            update_type="full_reset",
            field_updated="all",
            memory_version_before=old_version,
            memory_version_after=memory.memory_version,
        )
        db.add(log_entry)
        await db.flush()

        logger.info("Memory reset for user=%s", user_id)
        return memory

    @staticmethod
    def _serialize_memory_timestamp(value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return str(value)

    @staticmethod
    async def export_memory(
        user_id: uuid.UUID,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Export the full memory model as a JSON-serializable dict."""
        memory = await db.get(UserMemory, user_id)
        if memory is None:
            return {"error": "No memory found", "user_id": str(user_id)}

        return {
            "user_id": str(getattr(memory, "user_id", user_id)),
            "style_model": _coerce_memory_mapping(getattr(memory, "style_model", None)),
            "work_patterns": _coerce_memory_mapping(
                getattr(memory, "work_patterns", None)
            ),
            "project_knowledge": _coerce_memory_mapping(
                getattr(memory, "project_knowledge", None)
            ),
            "communication_style": _coerce_memory_mapping(
                getattr(memory, "communication_style", None)
            ),
            "structural_preferences": _coerce_memory_mapping(
                getattr(memory, "structural_preferences", None)
            ),
            "skill_profile": _coerce_memory_mapping(
                getattr(memory, "skill_profile", None)
            ),
            "explicit_preferences": _coerce_memory_list(
                getattr(memory, "explicit_preferences", None)
            ),
            "proactive_queue": _coerce_memory_list(
                getattr(memory, "proactive_queue", None)
            ),
            "memory_version": _coerce_memory_int(
                getattr(memory, "memory_version", None), 0
            ),
            "last_updated": MemoryEngine._serialize_memory_timestamp(
                getattr(memory, "last_updated", None)
            ),
            "total_sessions_analyzed": _coerce_memory_int(
                getattr(memory, "total_sessions_analyzed", None), 0
            ),
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _get_or_create_memory(
    user_id: uuid.UUID,
    db: AsyncSession,
) -> UserMemory:
    """Retrieve existing memory or create a blank one."""
    memory = await db.get(UserMemory, user_id)
    if memory is None:
        memory = UserMemory(user_id=user_id)
        db.add(memory)
        await db.flush()
    return memory


def _coerce_memory_mapping(value: object) -> dict[str, Any]:
    """Normalize legacy JSONB payloads to dicts for runtime safety."""
    if value is None:
        return {}
    if isinstance(value, Mapping):
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
        if isinstance(parsed, Mapping):
            return dict(parsed)
    return {}


def _coerce_memory_list(value: object) -> list[Any]:
    """Normalize legacy JSONB payloads to lists for runtime safety."""
    if value is None:
        return []
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


def _coerce_memory_row_list(value: object) -> list[object]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _coerce_memory_int(value: object, fallback: int = 0) -> int:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return fallback
        return int(value)
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return fallback
        if not math.isfinite(parsed):
            return fallback
        return int(parsed)
    return fallback


def _coerce_memory_float(value: object, fallback: float = 0.0) -> float:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        try:
            parsed = float(value)
        except OverflowError:
            return fallback
        return parsed if math.isfinite(parsed) else fallback
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return fallback
        return parsed if math.isfinite(parsed) else fallback
    return fallback


def _extract_explicit_preferences(text: str, prefs: list[Any]) -> None:
    """Scan text for 'always …' / 'never …' statements and append to prefs."""
    import re

    patterns = [
        r"always\s+(.{5,80}?)(?:\.|$)",
        r"never\s+(.{5,80}?)(?:\.|$)",
        r"prefer\s+(.{5,80}?)(?:\.|$)",
        r"i\s+(?:want|like|need)\s+(.{5,80}?)(?:\.|$)",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            cleaned = match.strip().rstrip(".")
            # Avoid duplicates (case-insensitive)
            if cleaned and not any(
                p.lower() == cleaned.lower() for p in prefs
            ):
                prefs.append(cleaned)
