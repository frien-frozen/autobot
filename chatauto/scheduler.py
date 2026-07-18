from __future__ import annotations

import asyncio
import logging
import time

from telegram.ext import Application

from chatauto.assistant import bump_job_repeat
from chatauto.config import Settings
from chatauto.store import Store

logger = logging.getLogger(__name__)


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
            if kind in {"remind", "ask"}:
                # DM from the bot so reminders still work even if Business chat is quiet
                target = job["target_chat_id"] or settings.owner_user_id
                await application.bot.send_message(chat_id=target, text=f"⏰ {text}")
            elif kind == "send":
                if not connection_id:
                    logger.warning("No business connection for send job #%s", job["id"])
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
                            continue
                if chat_id is None:
                    logger.warning("Send job #%s has no target chat", job["id"])
                    continue
                await application.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    business_connection_id=connection_id,
                )
                await store.add_message(chat_id, "me", text)
            else:
                logger.warning("Unknown job kind %s", kind)
                await store.mark_job_done(job["id"])
                continue

            next_ts = bump_job_repeat(job["run_at"], job["repeat_rule"], settings.timezone)
            if next_ts is not None:
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
