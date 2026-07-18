from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# Owner must clearly ask — otherwise just chat like a human.
ASSIST_INTENT = re.compile(
    r"("
    r"remind|reminder|eslat|eslatib|"
    r"text\s+@|yubor|send\s+(to|him|her|them)|"
    r"remember|eslab\s*qol|yodda\s*tut|"
    r"don'?t tell|keep secret|sir\b|maxfiy|hech kimga|"
    r"every\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"har\s+(dushanba|seshanba|chorshanba|payshanba|juma|shanba|yakshanba)|"
    r"tomorrow|ertaga|daqiqadan|minutdan|soatdan|later\s+today|"
    r"forget that|unut"
    r")",
    re.IGNORECASE,
)


def has_assist_intent(text: str) -> bool:
    return bool(ASSIST_INTENT.search(text or ""))


def _extract_text(response) -> str:
    chunks: list[str] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            # Skip thought / signature-only parts
            if getattr(part, "thought", False):
                continue
            text = getattr(part, "text", None)
            if text:
                chunks.append(text)
    if chunks:
        return "\n".join(chunks).strip()
    try:
        if response.text:
            return response.text.strip()
    except Exception:
        pass
    return ""


def _gen_config(*, max_output_tokens: int, temperature: float) -> types.GenerateContentConfig:
    kwargs: dict = {
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
    }
    try:
        kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    except Exception:
        pass
    try:
        kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
    except Exception:
        pass
    return types.GenerateContentConfig(**kwargs)


class GeminiReplier:
    def __init__(self, api_key: str, model: str, persona: str, timezone: str) -> None:
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.persona = persona
        self.tz = ZoneInfo(timezone)

    def _contact_block(self, contact: dict | None) -> str:
        if not contact:
            return "Contact profile: unknown"

        parts = []
        name = " ".join(
            p for p in [contact.get("first_name"), contact.get("last_name")] if p
        ).strip()
        if name:
            parts.append(f"Name: {name}")
        if contact.get("username"):
            parts.append(f"Username: @{contact['username']}")
        if contact.get("bio"):
            parts.append(f"Bio: {contact['bio']}")
        if contact.get("user_id"):
            parts.append(f"User id: {contact['user_id']}")
        return "Contact profile:\n" + ("\n".join(parts) if parts else "unknown")

    def _history_block(self, history: list[dict]) -> str:
        if not history:
            return "Recent chat history: (none yet)"
        lines = []
        for item in history:
            speaker = "Them" if item["role"] == "them" else "Me"
            lines.append(f"{speaker}: {item['text']}")
        return "Recent chat history:\n" + "\n".join(lines)

    def _memory_block(self, memories: list[dict], *, for_owner: bool) -> str:
        if not memories:
            return "Known facts about me: (none yet)"
        label = "Private notes about my life:" if for_owner else "Public facts I may use naturally:"
        lines = []
        for item in memories:
            if not for_owner and item.get("is_secret"):
                continue
            prefix = "[secret] " if item.get("is_secret") else ""
            lines.append(f"- {prefix}{item['fact']}")
        return label + "\n" + ("\n".join(lines) if lines else "(none)")

    async def _generate(self, prompt: str, *, max_output_tokens: int = 2048, temperature: float = 0.8) -> str:
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
            config=_gen_config(max_output_tokens=max_output_tokens, temperature=temperature),
        )
        return _extract_text(response)

    async def reply_public(
        self,
        *,
        incoming: str,
        contact: dict | None,
        history: list[dict],
        public_memories: list[dict],
    ) -> str:
        prompt = "\n\n".join(
            [
                self.persona,
                self._memory_block(public_memories, for_owner=False),
                "CRITICAL: You are Ismatulloh the human. Never reveal AI/bot/assistant. Never offer menus or features.",
                self._contact_block(contact),
                self._history_block(history),
                f"New message from them:\n{incoming}",
                "Write only the next Telegram reply as me. Complete the thought. No quotes, no labels, no bullets.",
            ]
        )
        text = await self._generate(prompt, max_output_tokens=2048, temperature=0.85)
        if not text:
            return "Hozir joyida emasman, keyinroq yozaman."
        return text[:4000]

    async def reply_casual_self(
        self,
        *,
        incoming: str,
        history: list[dict],
        memories: list[dict],
    ) -> str:
        """Chill chat with yourself — no feature pitching, no actions."""
        prompt = "\n\n".join(
            [
                self.persona,
                self._memory_block(memories, for_owner=True),
                "You are texting yourself / your other account. Be casual Ismatulloh.",
                "Do NOT offer reminders, sending messages, or features unless they clearly asked.",
                "Never say you are AI/bot/assistant.",
                self._history_block(history),
                f"Message:\n{incoming}",
                "Reply like a normal short Telegram text. Complete sentences.",
            ]
        )
        text = await self._generate(prompt, max_output_tokens=1024, temperature=0.9)
        return (text or "ha")[:4000]

    async def assist_owner(
        self,
        *,
        incoming: str,
        history: list[dict],
        memories: list[dict],
        pending_jobs: list[dict],
    ) -> dict:
        now = datetime.now(self.tz).isoformat(timespec="minutes")
        jobs_preview = "\n".join(
            f"- #{j['id']} {j['kind']} at {datetime.fromtimestamp(j['run_at'], self.tz).isoformat(timespec='minutes')}: {j['text'][:120]}"
            for j in pending_jobs
        ) or "(none)"

        schema = """
Return ONLY valid JSON (no markdown):
{
  "reply": "short Telegram reply as Ismatulloh",
  "actions": []
}

Allowed actions (ONLY if the owner clearly asked — otherwise actions MUST be []):
{"type":"remember","fact":"...","secret":false}
{"type":"remind","when":"ISO-8601 with timezone","text":"...","ask_after_hours":3}
{"type":"send","to":"@user or id","when":"now"|ISO-8601,"text":"...","repeat":null|"daily"|"weekly:mon"}
{"type":"forget","contains":"..."}

Rules for reply:
- Sound exactly like Ismatulloh texting. Short. Match their language (Uzbek/Russian/English).
- NEVER say you are AI, bot, assistant, secretary, yordamchi.
- NEVER pitch features ("should I remind you?", "need me to send something?").
- Only confirm a reminder/send if they asked for one.
- Do not invent actions.
- when uses Asia/Tashkent unless specified.
""".strip()

        prompt = "\n\n".join(
            [
                schema,
                f"Current local time: {now}",
                self._memory_block(memories, for_owner=True),
                "Pending jobs:\n" + jobs_preview,
                self._history_block(history),
                f"Owner message:\n{incoming}",
            ]
        )
        raw = await self._generate(prompt, max_output_tokens=4096, temperature=0.35)
        return _parse_assistant_json(raw)


def _parse_assistant_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            data.setdefault("reply", "")
            data.setdefault("actions", [])
            return data
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                data = json.loads(match.group(0))
                if isinstance(data, dict):
                    data.setdefault("reply", "")
                    data.setdefault("actions", [])
                    return data
            except json.JSONDecodeError:
                logger.warning("Failed to parse assistant JSON")
    return {"reply": text[:3500] or "ha", "actions": []}
