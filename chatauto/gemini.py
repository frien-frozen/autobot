from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


def _extract_text(response) -> str:
    """Pull plain text even when models return thought signatures / mixed parts."""
    try:
        if response.text:
            return response.text.strip()
    except Exception:
        pass

    chunks: list[str] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            text = getattr(part, "text", None)
            if text:
                chunks.append(text)
    return "\n".join(chunks).strip()


def _thinking_config() -> types.ThinkingConfig | None:
    try:
        return types.ThinkingConfig(thinking_budget=0)
    except Exception:
        return None


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
        label = "Known facts about me (owner-only context):" if for_owner else "Public facts I may use naturally:"
        lines = []
        for item in memories:
            prefix = "[secret] " if item.get("is_secret") else ""
            lines.append(f"- {prefix}{item['fact']}")
        return label + "\n" + "\n".join(lines)

    async def _generate(self, prompt: str, *, max_output_tokens: int = 2048, temperature: float = 0.8) -> str:
        config_kwargs: dict = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }
        thinking = _thinking_config()
        if thinking is not None:
            config_kwargs["thinking_config"] = thinking

        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(**config_kwargs),
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
                "Never mention private/secret facts. Never say you are a bot.",
                self._contact_block(contact),
                self._history_block(history),
                f"New message from them:\n{incoming}",
                "Write only the next Telegram reply as me. Complete sentences. No quotes, no labels.",
            ]
        )
        text = await self._generate(prompt, max_output_tokens=2048, temperature=0.85)
        if not text:
            return "Сейчас не могу нормально ответить, чуть позже напишу."
        return text[:4000]

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
Return ONLY valid JSON (no markdown) with this shape:
{
  "reply": "short Telegram reply to me",
  "actions": [
    {"type": "remember", "fact": "...", "secret": false},
    {"type": "remind", "when": "ISO-8601 datetime with timezone", "text": "reminder text", "ask_after_hours": 3},
    {"type": "send", "to": "@username or numeric chat id", "when": "now" or ISO-8601, "text": "message to send as me", "repeat": null or "daily" or "weekly:mon|tue|wed|thu|fri|sat|sun"},
    {"type": "forget", "contains": "substring of fact to deactivate"}
  ]
}
Rules:
- You are my private chief-of-staff living in Telegram.
- I am the owner. Help with reminders, outbound texts, and memory.
- If I share project/life facts, add remember actions.
- If I say don't tell anyone / keep secret / between us → remember with secret=true.
- If I ask to remind myself → remind action + reply confirming when.
- If I ask to text someone now/later/recurring → send action.
- when must use timezone Asia/Tashkent unless I specify otherwise.
- ask_after_hours: after a reminder, ping me again asking if I actually did it (default 3).
- reply must be complete, short, natural.
- actions may be empty.
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
        raw = await self._generate(prompt, max_output_tokens=4096, temperature=0.4)
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
    return {"reply": text[:3500] or "Got it.", "actions": []}
