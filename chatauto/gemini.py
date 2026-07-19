from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

ASSIST_INTENT = re.compile(
    r"("
    r"remind|reminder|eslat|eslatib|"
    r"text\s+@|@\w+\s+shunga|yozvor|yozib\s*qo'?y|yubor|send\s+(to|him|her|them)|"
    r"remember|eslab\s*qol|yodda\s*tut|"
    r"don'?t tell|keep secret|sir\b|maxfiy|hech kimga|"
    r"every\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"har\s+(dushanba|seshanba|chorshanba|payshanba|juma|shanba|yakshanba)|"
    r"tomorrow|ertaga|daqiqadan|minutdan|soatdan|later\s+today|"
    r"forget that|unut|"
    r"kim\s+yozdi|kimdir|chqirdi|chaqirdi|call\s+me|ovqat|eat|"
    r"yozaman|qilaman|eslatib\s*tur"
    r")",
    re.IGNORECASE,
)


def has_assist_intent(text: str) -> bool:
    return bool(ASSIST_INTENT.search(text or ""))


def _looks_sensitive_fact(fact: str) -> bool:
    f = fact.lower()
    keys = (
        "mom", "dad", "ona", "ota", "oyim", "otam", "wife", "girlfriend", "aunt", "xola",
        "relationship", "money", "pul", "family", "secret", "ovqat", "eat at", "invite",
        "chaqirdi", "scam", "inbox:",
    )
    return any(k in f for k in keys)


def _extract_text(response) -> str:
    chunks: list[str] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
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
        label = "Private notes (OWNER ONLY):" if for_owner else "Safe public facts only:"
        lines = []
        for item in memories:
            if not for_owner:
                if item.get("is_secret"):
                    continue
                src = str(item.get("source") or "")
                if src.startswith("inbox:"):
                    continue
                if _looks_sensitive_fact(item.get("fact") or ""):
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
                "CRITICAL: You are Ismatulloh the human. Never reveal AI/bot/assistant.",
                "NEVER mention family, relationships, money drama, private plans, or other people's invites "
                "unless THIS contact already brought it up in THIS chat.",
                self._contact_block(contact),
                self._history_block(history),
                f"New message from them:\n{incoming}",
                "Write only the next Telegram reply as me. Complete the thought. No quotes/labels/bullets.",
            ]
        )
        text = await self._generate(prompt, max_output_tokens=2048, temperature=0.85)
        if not text:
            return "Hozir joyida emasman, keyinroq yozaman."
        return text[:4000]

    async def extract_inbox_event(
        self,
        *,
        contact: dict | None,
        incoming: str,
        reply: str,
    ) -> str | None:
        who = "someone"
        if contact:
            bits = [contact.get("first_name"), contact.get("last_name")]
            name = " ".join(b for b in bits if b).strip()
            if contact.get("username"):
                who = f"{name} (@{contact['username']})" if name else f"@{contact['username']}"
            elif name:
                who = name
        prompt = f"""
Summarize this Telegram inbound into ONE short factual note for the account owner.
Include who, what they want, time/place if any.
If pure small talk with nothing useful, return exactly: NONE

From: {who}
Them: {incoming}
My reply: {reply}

Return only the note or NONE.
""".strip()
        text = await self._generate(prompt, max_output_tokens=200, temperature=0.1)
        text = (text or "").strip()
        if not text or text.upper().startswith("NONE"):
            return None
        return text[:300]

    async def reply_casual_self(
        self,
        *,
        incoming: str,
        history: list[dict],
        memories: list[dict],
    ) -> str:
        prompt = "\n\n".join(
            [
                self.persona,
                self._memory_block(memories, for_owner=True),
                "You are texting yourself. Be casual Ismatulloh.",
                "Use inbox notes if relevant (e.g. someone invited you to eat).",
                "No reminder menus. Never say AI/bot/assistant.",
                self._history_block(history),
                f"Message:\n{incoming}",
                "Reply like a normal short Telegram text.",
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
            f"- #{j['id']} {j['kind']} at "
            f"{datetime.fromtimestamp(j['run_at'], self.tz).isoformat(timespec='minutes')}: "
            f"{j['text'][:120]}"
            for j in pending_jobs
        ) or "(none)"

        schema = """
Return ONLY valid JSON (no markdown):
{
  "reply": "short Telegram reply as Ismatulloh",
  "actions": []
}

Allowed actions (ONLY if clearly requested):
{"type":"remember","fact":"...","secret":true}
{"type":"remind","when":"ISO-8601 Asia/Tashkent","text":"...","ask_after_minutes":30}
{"type":"send","to":"@user","when":"now"|ISO-8601,"text":"...","repeat":null|"daily"|"weekly:mon"}
{"type":"forget","contains":"..."}
{"type":"cancel_nags"}

Rules:
- Sound like Ismatulloh. Short. Match Uzbek/Russian/English.
- Do NOT claim sent/done in reply yet — system will append truth notes.
- If they ask whether someone called/wrote/invited, use inbox notes and answer truthfully.
- Family/relationships/money → remember secret:true.
- Commitments like "I'll text mom" → remind + ask_after_minutes nag.
- Never say AI/bot/assistant.
""".strip()

        raw = await self._generate(
            "\n\n".join(
                [
                    schema,
                    f"Current local time: {now}",
                    self._memory_block(memories, for_owner=True),
                    "Pending jobs:\n" + jobs_preview,
                    self._history_block(history),
                    f"Owner message:\n{incoming}",
                ]
            ),
            max_output_tokens=4096,
            temperature=0.35,
        )
        return _parse_assistant_json(raw)

    async def rewrite_with_action_truth(self, *, draft_reply: str, notes: list[str]) -> str:
        """Make the spoken reply match SENT/FAIL/QUEUED reality."""
        if not notes:
            return draft_reply
        prompt = f"""
Rewrite the Telegram reply so it matches ACTION RESULTS exactly.
If FAIL — admit it didn't send / couldn't do it. Don't invent screenshots.
If SENT — confirm briefly.
If QUEUED — say it's scheduled, not sent yet.
Keep short, casual Uzbek/Russian/English like Ismatulloh. No AI talk.

Draft: {draft_reply}

ACTION RESULTS:
{chr(10).join('- ' + n for n in notes)}

Return only the final reply text.
""".strip()
        text = await self._generate(prompt, max_output_tokens=400, temperature=0.2)
        return (text or draft_reply)[:4000]


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
