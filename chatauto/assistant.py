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


def next_weekly(from_dt: datetime, weekday: int) -> datetime:
    days_ahead = (weekday - from_dt.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return from_dt + timedelta(days=days_ahead)


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
                secret = bool(action.get("secret", False))
                await store.add_memory(fact, is_secret=secret, source="owner_chat")
                notes.append(("🔒 saved secret: " if secret else "saved: ") + fact[:80])

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
                notes.append(f"forgot {removed} fact(s) matching '{needle}'")

            elif kind == "remind":
                when = parse_when(str(action.get("when", "")), tz)
                text = str(action.get("text", "")).strip()
                if when is None or not text:
                    notes.append("couldn't schedule reminder (bad time/text)")
                    continue
                job_id = await store.add_job(
                    kind="remind",
                    run_at=when.timestamp(),
                    text=text,
                    target_chat_id=settings.owner_user_id,
                )
                ask_after = action.get("ask_after_hours", settings.reminder_followup_hours)
                try:
                    ask_after_h = float(ask_after)
                except (TypeError, ValueError):
                    ask_after_h = float(settings.reminder_followup_hours)
                if ask_after_h > 0:
                    await store.add_job(
                        kind="ask",
                        run_at=(when + timedelta(hours=ask_after_h)).timestamp(),
                        text=f"Did you actually do this? → {text}",
                        target_chat_id=settings.owner_user_id,
                    )
                notes.append(f"reminder #{job_id} at {when.isoformat(timespec='minutes')}")

            elif kind == "send":
                to_raw = str(action.get("to", "")).strip()
                text = str(action.get("text", "")).strip()
                when_raw = str(action.get("when", "now")).strip()
                repeat = action.get("repeat")
                repeat_rule = str(repeat).strip().lower() if repeat else None
                if not to_raw or not text:
                    notes.append("couldn't queue send (missing to/text)")
                    continue

                target_chat_id = None
                target_username = None
                if re.fullmatch(r"-?\d+", to_raw):
                    target_chat_id = int(to_raw)
                else:
                    target_username = to_raw.lstrip("@")
                    known = await store.get_contact_by_username(target_username)
                    if known:
                        target_chat_id = known["chat_id"]

                when = parse_when(when_raw, tz)
                if when is None:
                    notes.append("couldn't queue send (bad time)")
                    continue

                if when_raw.lower() == "now" and connection_id and target_chat_id:
                    try:
                        await context.bot.send_message(
                            chat_id=target_chat_id,
                            text=text,
                            business_connection_id=connection_id,
                        )
                        await store.add_message(target_chat_id, "me", text)
                        notes.append(f"sent now to {to_raw}")
                        if not repeat_rule:
                            continue
                        # schedule next occurrence only
                        when = _bump_repeat(when, repeat_rule, tz)
                        if when is None:
                            continue
                    except Exception:
                        logger.exception("Immediate send failed to %s", to_raw)
                        notes.append(f"send failed now to {to_raw}, queued instead")

                job_id = await store.add_job(
                    kind="send",
                    run_at=when.timestamp(),
                    text=text,
                    repeat_rule=repeat_rule,
                    target_chat_id=target_chat_id,
                    target_username=target_username,
                )
                notes.append(f"send #{job_id} → {to_raw} at {when.isoformat(timespec='minutes')}")

            else:
                notes.append(f"unknown action: {kind}")
        except Exception:
            logger.exception("Failed action %s", action)
            notes.append(f"action failed: {kind}")

    return notes


def _bump_repeat(from_dt: datetime, repeat_rule: str, tz: ZoneInfo) -> datetime | None:
    rule = repeat_rule.strip().lower()
    if rule == "daily":
        return from_dt + timedelta(days=1)
    if rule.startswith("weekly:"):
        day = rule.split(":", 1)[1]
        if day not in WEEKDAYS:
            return None
        # keep same clock time, jump to next matching weekday
        candidate = from_dt + timedelta(days=1)
        candidate = candidate.replace(hour=from_dt.hour, minute=from_dt.minute, second=0, microsecond=0)
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
