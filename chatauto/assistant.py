from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram.ext import ContextTypes

from chatauto.config import Settings
from chatauto.store import Store

logger = logging.getLogger(__name__)

WEEKDAYS = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

DONE_RE = re.compile(
    r"\b("
    r"ha|xa|yes|yep|done|did|qildim|yozdim|yubordim|jonatdim|jo'?natdim|"
    r"gaplashdim|telefon\s*qildim|chqardim|bo'?ldi|ok\b|okay"
    r")\b",
    re.IGNORECASE,
)


def parse_when(value: str, tz: ZoneInfo) -> datetime | None:
    value = (value or "").strip()
    if not value or value.lower() == "now":
        return datetime.now(tz)

    cleaned = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return dt.astimezone(tz)
    except ValueError:
        return None


async def resolve_target(
    *,
    to_raw: str,
    store: Store,
    context: ContextTypes.DEFAULT_TYPE,
) -> tuple[int | None, str | None, str | None]:
    """Returns (chat_id, username, error)."""
    to_raw = to_raw.strip()
    if re.fullmatch(r"-?\d+", to_raw):
        return int(to_raw), None, None

    username = to_raw.lstrip("@")
    known = await store.get_contact_by_username(username)
    if known:
        return int(known["chat_id"]), username, None

    try:
        chat = await context.bot.get_chat(f"@{username}")
        await store.upsert_contact(
            chat_id=chat.id,
            user_id=chat.id,
            username=username,
            first_name=getattr(chat, "first_name", None),
            last_name=getattr(chat, "last_name", None),
            bio=getattr(chat, "bio", None),
        )
        return int(chat.id), username, None
    except Exception as exc:
        logger.warning("Cannot resolve @%s: %s", username, exc)
        return None, username, f"@{username} ni topa olmadim (avval u senga yozgan bo'lishi kerak)"


async def apply_actions(
    *,
    actions: list[dict],
    store: Store,
    settings: Settings,
    context: ContextTypes.DEFAULT_TYPE,
    connection_id: str | None,
) -> list[str]:
    notes: list[str] = []
    tz = ZoneInfo(settings.timezone)

    for action in actions or []:
        if not isinstance(action, dict):
            continue
        kind = str(action.get("type", "")).lower().strip()
        try:
            if kind == "remember":
                fact = str(action.get("fact", "")).strip()
                if not fact:
                    continue
                # Relationships / private life default to secret
                secret = bool(action.get("secret", False)) or _looks_personal(fact)
                await store.add_memory(fact, is_secret=secret, source="owner_chat")
                notes.append(("secret saved: " if secret else "saved: ") + fact[:80])

            elif kind == "forget":
                needle = str(action.get("contains", "")).strip().lower()
                if not needle:
                    continue
                memories = await store.list_memories(include_secrets=True)
                removed = 0
                for mem in memories:
                    if needle in mem["fact"].lower():
                        await store.db.execute(
                            "UPDATE memories SET active = 0 WHERE id = ?",
                            (mem["id"],),
                        )
                        removed += 1
                await store.db.commit()
                notes.append(f"forgot {removed} matching '{needle}'")

            elif kind == "remind":
                when = parse_when(str(action.get("when", "")), tz)
                text = str(action.get("text", "")).strip()
                if when is None or not text:
                    notes.append("FAIL: reminder time/text bad")
                    continue
                job_id = await store.add_job(
                    kind="remind",
                    run_at=when.timestamp(),
                    text=text,
                    target_chat_id=settings.owner_user_id,
                )
                # Human nag: keep asking until owner confirms done
                nag_minutes = 30.0
                if action.get("ask_after_minutes") is not None:
                    try:
                        nag_minutes = float(action["ask_after_minutes"])
                    except (TypeError, ValueError):
                        nag_minutes = 30.0
                elif action.get("ask_after_hours") is not None:
                    try:
                        nag_minutes = float(action["ask_after_hours"]) * 60
                    except (TypeError, ValueError):
                        nag_minutes = 30.0
                nag_minutes = max(5.0, nag_minutes)
                await store.add_job(
                    kind="nag",
                    run_at=(when + timedelta(minutes=nag_minutes)).timestamp(),
                    text=text,
                    repeat_rule=f"nag:{int(nag_minutes)}",
                    target_chat_id=settings.owner_user_id,
                )
                notes.append(
                    f"reminder #{job_id} at {when.isoformat(timespec='minutes')} "
                    f"(then I'll keep asking until you say ha/yozdim)"
                )

            elif kind == "send":
                to_raw = str(action.get("to", "")).strip()
                text = str(action.get("text", "")).strip()
                when_raw = str(action.get("when", "now")).strip()
                repeat = action.get("repeat")
                repeat_rule = str(repeat).strip().lower() if repeat else None
                if not to_raw or not text:
                    notes.append("FAIL: send missing to/text")
                    continue

                if not connection_id:
                    active = await store.get_active_connection()
                    connection_id = active["connection_id"] if active else None
                if not connection_id:
                    notes.append("FAIL: no business connection — can't send as you")
                    continue

                target_chat_id, target_username, err = await resolve_target(
                    to_raw=to_raw,
                    store=store,
                    context=context,
                )
                if err and target_chat_id is None:
                    notes.append(f"FAIL: {err}")
                    continue

                when = parse_when(when_raw, tz)
                if when is None:
                    notes.append("FAIL: bad send time")
                    continue

                send_now = when_raw.lower() == "now" or when <= datetime.now(tz) + timedelta(seconds=15)
                if send_now:
                    try:
                        await context.bot.send_message(
                            chat_id=target_chat_id,
                            text=text,
                            business_connection_id=connection_id,
                        )
                        await store.add_message(int(target_chat_id), "me", text)
                        notes.append(f"SENT to {to_raw}: {text[:60]}")
                        if repeat_rule:
                            nxt = _bump_repeat(when, repeat_rule, tz)
                            if nxt:
                                job_id = await store.add_job(
                                    kind="send",
                                    run_at=nxt.timestamp(),
                                    text=text,
                                    repeat_rule=repeat_rule,
                                    target_chat_id=target_chat_id,
                                    target_username=target_username,
                                )
                                notes.append(
                                    f"queued next #{job_id} at {nxt.isoformat(timespec='minutes')}"
                                )
                    except Exception as exc:
                        logger.exception("Immediate send failed to %s", to_raw)
                        notes.append(f"FAIL: could not send to {to_raw} ({exc})")
                    continue

                job_id = await store.add_job(
                    kind="send",
                    run_at=when.timestamp(),
                    text=text,
                    repeat_rule=repeat_rule,
                    target_chat_id=target_chat_id,
                    target_username=target_username,
                )
                notes.append(
                    f"QUEUED send #{job_id} → {to_raw} at {when.isoformat(timespec='minutes')} "
                    f"(not sent yet)"
                )

            elif kind == "cancel_nags":
                n = await store.cancel_jobs_by_kinds(("nag", "ask"))
                notes.append(f"stopped {n} follow-up nags")

            else:
                notes.append(f"unknown action: {kind}")
        except Exception:
            logger.exception("Failed action %s", action)
            notes.append(f"FAIL: action {kind}")

    return notes


async def maybe_clear_nags_on_done(store: Store, text: str) -> int:
    if not DONE_RE.search(text or ""):
        return 0
    return await store.cancel_jobs_by_kinds(("nag", "ask"))


def _looks_personal(fact: str) -> bool:
    f = fact.lower()
    keys = (
        "mom", "dad", "ona", "ota", "oyim", "otam", "wife", "girlfriend", "boyfriend",
        "aunt", "xola", "amma", "relationship", "money", "pul", "secret", "family",
        "oshna", "sevgili", "nikoh", "divorce", "scam",
    )
    return any(k in f for k in keys)


def _bump_repeat(from_dt: datetime, repeat_rule: str, tz: ZoneInfo) -> datetime | None:
    rule = repeat_rule.strip().lower()
    if rule == "daily":
        return from_dt + timedelta(days=1)
    if rule.startswith("nag:"):
        try:
            minutes = int(rule.split(":", 1)[1])
        except ValueError:
            minutes = 30
        return from_dt + timedelta(minutes=max(5, minutes))
    if rule.startswith("weekly:"):
        day = rule.split(":", 1)[1]
        if day not in WEEKDAYS:
            return None
        candidate = from_dt + timedelta(days=1)
        candidate = candidate.replace(
            hour=from_dt.hour, minute=from_dt.minute, second=0, microsecond=0
        )
        while candidate.weekday() != WEEKDAYS[day]:
            candidate += timedelta(days=1)
        return candidate
    return None


def bump_job_repeat(run_at: float, repeat_rule: str | None, tz_name: str) -> float | None:
    if not repeat_rule:
        return None
    tz = ZoneInfo(tz_name)
    dt = datetime.fromtimestamp(run_at, tz)
    nxt = _bump_repeat(dt, repeat_rule, tz)
    return nxt.timestamp() if nxt else None
