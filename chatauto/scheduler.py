from __future__ import annotations

import asyncio
import logging
import time

from telegram.ext import Application

from chatauto.assistant import bump_job_repeat
from chatauto.config import Settings
from chatauto.store import Store

logger = logging.getLogger(__name__)


async def _notify_owner(application: Application, text: str) -> None:
    """Ping owner via Business chat(s) first, then bot DM fallback."""
    settings: Settings = application.bot_data["settings"]
    store: Store = application.bot_data["store"]
    connection = await store.get_active_connection()
    connection_id = connection["connection_id"] if connection else None

    sent = False
    if connection_id:
        for oid in settings.all_owner_ids:
            if oid == settings.owner_user_id:
                # Prefer alts that you actually chat with yourself
                continue
            try:
                await application.bot.send_message(
                    chat_id=oid,
                    text=text,
                    business_connection_id=connection_id,
                )
                sent = True
            except Exception:
                logger.exception("Business notify failed to %s", oid)

        # Also try primary business account's private chat with itself is impossible;
        # try owner_user_id as chat anyway in case it's an alt listed wrong.
        if not sent:
            try:
                await application.bot.send_message(
                    chat_id=settings.owner_user_id,
                    text=text,
                    business_connection_id=connection_id,
                )
                sent = True
            except Exception:
                logger.exception("Business notify to primary failed")

    if not sent:
        for oid in settings.all_owner_ids:
            try:
                await application.bot.send_message(chat_id=oid, text=text)
                sent = True
                break
            except Exception:
                logger.exception("Bot DM notify failed to %s", oid)

    if not sent:
        logger.error("Could not deliver owner notify: %s", text[:120])


async def process_due_jobs(application: Application) -> None:
    settings: Settings = application.bot_data["settings"]
    store: Store = application.bot_data["store"]
    now = time.time()
    jobs = await store.due_jobs(now)
    if not jobs:
        return

    connection = await store.get_active_connection()
    connection_id = connection["connection_id"] if connection else None

    for job in jobs:
        kind = job["kind"]
        text = job["text"]
        try:
            if kind == "remind":
                await _notify_owner(application, f"⏰ Eslatma: {text}")
            elif kind in {"ask", "nag"}:
                await _notify_owner(
                    application,
                    f"Ey — qildingmi?\n→ {text}\n\n(ha / yozdim deb yozsang to'xtataman)",
                )
            elif kind == "send":
                if not connection_id:
                    logger.warning("No business connection for send job #%s — will retry", job["id"])
                    await store.reschedule_job(job["id"], now + 120)
                    continue
                chat_id = job["target_chat_id"]
                if chat_id is None and job["target_username"]:
                    known = await store.get_contact_by_username(job["target_username"])
                    if known:
                        chat_id = known["chat_id"]
                    else:
                        try:
                            chat = await application.bot.get_chat(f"@{job['target_username']}")
                            chat_id = chat.id
                            await store.upsert_contact(
                                chat_id=chat.id,
                                user_id=chat.id,
                                username=job["target_username"],
                                first_name=getattr(chat, "first_name", None),
                                last_name=getattr(chat, "last_name", None),
                                bio=getattr(chat, "bio", None),
                            )
                        except Exception:
                            logger.exception(
                                "Cannot resolve @%s for job #%s",
                                job["target_username"],
                                job["id"],
                            )
                            await _notify_owner(
                                application,
                                f"⚠️ @{job['target_username']} ga yubora olmadim — topilmadi.",
                            )
                            await store.mark_job_done(job["id"])
                            continue
                if chat_id is None:
                    await _notify_owner(application, f"⚠️ Send job #{job['id']} — target yo'q.")
                    await store.mark_job_done(job["id"])
                    continue
                try:
                    await application.bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        business_connection_id=connection_id,
                    )
                    await store.add_message(chat_id, "me", text)
                    await _notify_owner(
                        application,
                        f"✅ Yuborildi → {job['target_username'] or chat_id}: {text[:80]}",
                    )
                except Exception:
                    logger.exception("Send job #%s failed", job["id"])
                    await _notify_owner(
                        application,
                        f"⚠️ Yuborilmadi → {job['target_username'] or chat_id}. Keyinroq urinib ko'raman.",
                    )
                    await store.reschedule_job(job["id"], now + 180)
                    continue
            else:
                logger.warning("Unknown job kind %s", kind)
                await store.mark_job_done(job["id"])
                continue

            next_ts = bump_job_repeat(job["run_at"], job["repeat_rule"], settings.timezone)
            if next_ts is not None:
                # For nag, schedule from now so interval is from last ask
                if str(job.get("repeat_rule") or "").startswith("nag:"):
                    next_ts = bump_job_repeat(now, job["repeat_rule"], settings.timezone) or next_ts
                await store.reschedule_job(job["id"], next_ts)
                logger.info("Rescheduled job #%s → %s", job["id"], next_ts)
            else:
                await store.mark_job_done(job["id"])
                logger.info("Completed job #%s (%s)", job["id"], kind)
        except Exception:
            logger.exception("Failed job #%s", job["id"])


async def scheduler_loop(application: Application, stop: asyncio.Event) -> None:
    settings: Settings = application.bot_data["settings"]
    logger.info("Scheduler started (every %ss)", settings.scheduler_poll_seconds)
    while not stop.is_set():
        try:
            await process_due_jobs(application)
        except Exception:
            logger.exception("Scheduler tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.scheduler_poll_seconds)
        except asyncio.TimeoutError:
            continue
