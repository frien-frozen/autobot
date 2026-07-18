from __future__ import annotations

import logging

from telegram import Chat, Update
from telegram.ext import ContextTypes

from chatauto.config import Settings
from chatauto.gemini import GeminiReplier
from chatauto.store import Store

logger = logging.getLogger(__name__)


def _rights_can_reply(connection) -> bool:
    rights = getattr(connection, "rights", None)
    if rights is None:
        # Older API / missing rights payload — try reply and let Telegram reject if needed.
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

    # You typed yourself → store + pause auto-replies so the bot does not talk over you.
    if sender and sender.id == settings.owner_user_id:
        await store.add_message(chat_id, "me", message.text)
        await store.pause_chat(chat_id, settings.owner_pause_minutes)
        logger.info("Owner message in chat %s — paused %sm", chat_id, settings.owner_pause_minutes)
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

    try:
        await context.bot.send_chat_action(
            chat_id=chat_id,
            action="typing",
            business_connection_id=connection_id,
        )
        reply = await gemini.reply(
            incoming=message.text,
            contact=contact,
            history=history,
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=reply,
            business_connection_id=connection_id,
        )
        await store.add_message(chat_id, "me", reply)
    except Exception:
        logger.exception("Failed to auto-reply in chat %s", chat_id)


async def on_direct_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner can DM the bot for a simple status ping."""
    settings: Settings = context.application.bot_data["settings"]
    message = update.message
    if message is None or message.from_user is None:
        return
    if message.from_user.id != settings.owner_user_id:
        await message.reply_text("This bot only works via Telegram Business chat automation.")
        return
    await message.reply_text(
        "chatauto is running.\n"
        "Connect it under Settings → Business → Chatbots, then message yourself from another account to test."
    )
