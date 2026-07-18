from __future__ import annotations

import logging

from telegram import Chat, Message, Update
from telegram.ext import ContextTypes

from chatauto.assistant import apply_actions
from chatauto.config import Settings
from chatauto.gemini import GeminiReplier
from chatauto.store import Store

logger = logging.getLogger(__name__)


def _rights_can_reply(connection) -> bool:
    rights = getattr(connection, "rights", None)
    if rights is None:
        return True
    return bool(getattr(rights, "can_reply", False))


async def on_business_connection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store: Store = context.application.bot_data["store"]
    connection = update.business_connection
    if connection is None:
        return

    can_reply = _rights_can_reply(connection)
    await store.upsert_connection(
        connection_id=connection.id,
        owner_user_id=connection.user.id,
        is_enabled=connection.is_enabled,
        can_reply=can_reply,
    )
    logger.info(
        "Business connection %s enabled=%s can_reply=%s",
        connection.id,
        connection.is_enabled,
        can_reply,
    )


async def _refresh_contact_profile(
    *,
    chat: Chat,
    store: Store,
    context: ContextTypes.DEFAULT_TYPE,
) -> dict | None:
    bio = None
    username = chat.username
    first_name = chat.first_name
    last_name = chat.last_name
    user_id = chat.id

    try:
        full = await context.bot.get_chat(chat.id)
        bio = getattr(full, "bio", None)
        username = full.username or username
        first_name = full.first_name or first_name
        last_name = full.last_name or last_name
    except Exception:
        logger.exception("Failed to fetch profile for chat %s", chat.id)

    await store.upsert_contact(
        chat_id=chat.id,
        user_id=user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        bio=bio,
    )
    return await store.get_contact(chat.id)


async def _split_send(
    *,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    text: str,
    business_connection_id: str | None = None,
) -> None:
    chunk = text.strip()
    if not chunk:
        return
    # Telegram hard limit 4096; keep margin
    limit = 3500
    parts = [chunk[i : i + limit] for i in range(0, len(chunk), limit)] or [chunk]
    for part in parts:
        kwargs = {"chat_id": chat_id, "text": part}
        if business_connection_id:
            kwargs["business_connection_id"] = business_connection_id
        await context.bot.send_message(**kwargs)


async def _handle_owner_assistant(
    *,
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    reply_via_business: bool,
) -> None:
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    gemini: GeminiReplier = context.application.bot_data["gemini"]

    chat_id = message.chat_id
    text = message.text or ""
    connection_id = message.business_connection_id if reply_via_business else None

    await store.add_message(chat_id, "them", text)
    history = await store.recent_messages(chat_id, settings.history_limit)
    memories = await store.list_memories(include_secrets=True)
    pending = await store.pending_jobs(15)

    try:
        if reply_via_business and connection_id:
            await context.bot.send_chat_action(
                chat_id=chat_id,
                action="typing",
                business_connection_id=connection_id,
            )
        result = await gemini.assist_owner(
            incoming=text,
            history=history,
            memories=memories,
            pending_jobs=pending,
        )
        notes = await apply_actions(
            actions=result.get("actions") or [],
            store=store,
            settings=settings,
            context=context,
            connection_id=connection_id
            or ((await store.get_active_connection()) or {}).get("connection_id"),
        )
        reply = (result.get("reply") or "Got it.").strip()
        if notes:
            reply = reply + "\n\n" + "\n".join(f"• {n}" for n in notes)

        await _split_send(
            context=context,
            chat_id=chat_id,
            text=reply,
            business_connection_id=connection_id if reply_via_business else None,
        )
        await store.add_message(chat_id, "me", reply)
    except Exception:
        logger.exception("Owner assistant failed in chat %s", chat_id)
        try:
            await _split_send(
                context=context,
                chat_id=chat_id,
                text="Something glitched on my side — say that again?",
                business_connection_id=connection_id if reply_via_business else None,
            )
        except Exception:
            logger.exception("Failed to send assistant error reply")


async def on_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    store: Store = context.application.bot_data["store"]
    gemini: GeminiReplier = context.application.bot_data["gemini"]

    message = update.business_message
    if message is None or not message.text:
        return

    connection_id = message.business_connection_id
    if not connection_id:
        return

    chat_id = message.chat_id
    sender = message.from_user
    sender_id = sender.id if sender else None

    # You messaging a stranger from the Business account → pause auto-replies.
    if settings.is_owner(sender_id) and not settings.is_owner(chat_id):
        await store.add_message(chat_id, "me", message.text)
        await store.pause_chat(chat_id, settings.owner_pause_minutes)
        logger.info("Owner manual message in chat %s — paused %sm", chat_id, settings.owner_pause_minutes)
        return

    # Talking to yourself across your accounts → personal assistant mode.
    if settings.is_owner(sender_id) and settings.is_owner(chat_id):
        await _handle_owner_assistant(
            message=message,
            context=context,
            reply_via_business=True,
        )
        return

    await store.add_message(chat_id, "them", message.text)

    if await store.is_paused(chat_id):
        logger.info("Chat %s is paused — skipping auto-reply", chat_id)
        return

    if not await store.can_reply(connection_id):
        logger.warning("No reply permission for connection %s", connection_id)
        return

    contact = await _refresh_contact_profile(chat=message.chat, store=store, context=context)
    history = await store.recent_messages(chat_id, settings.history_limit)
    public_memories = await store.list_memories(include_secrets=False)

    try:
        await context.bot.send_chat_action(
            chat_id=chat_id,
            action="typing",
            business_connection_id=connection_id,
        )
        reply = await gemini.reply_public(
            incoming=message.text,
            contact=contact,
            history=history,
            public_memories=public_memories,
        )
        await _split_send(
            context=context,
            chat_id=chat_id,
            text=reply,
            business_connection_id=connection_id,
        )
        await store.add_message(chat_id, "me", reply)
    except Exception:
        logger.exception("Failed to auto-reply in chat %s", chat_id)


async def on_direct_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    message = update.message
    if message is None or message.from_user is None or not message.text:
        return

    if not settings.is_owner(message.from_user.id):
        await message.reply_text("This bot only works for its owner via Telegram Business.")
        return

    if message.text.startswith("/start"):
        await message.reply_text(
            "Owner mode ready.\n"
            "Message me here, or message yourself from your other account.\n\n"
            "Examples:\n"
            "• remind me tomorrow 10am to ask dad for money\n"
            "• text @username tomorrow: hey, free for a call?\n"
            "• every monday 9am text @username: weekly check-in\n"
            "• remember: shipping baxtiyorov.uz redesign\n"
            "• don't tell anyone: ...\n"
        )
        return

    await _handle_owner_assistant(
        message=message,
        context=context,
        reply_via_business=False,
    )
