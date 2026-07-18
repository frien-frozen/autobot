from __future__ import annotations

from google import genai
from google.genai import types


class GeminiReplier:
    def __init__(self, api_key: str, model: str, persona: str) -> None:
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.persona = persona

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

    async def reply(
        self,
        *,
        incoming: str,
        contact: dict | None,
        history: list[dict],
    ) -> str:
        prompt = "\n\n".join(
            [
                self.persona,
                self._contact_block(contact),
                self._history_block(history),
                f"New message from them:\n{incoming}",
                "Write only the next Telegram reply as me. No quotes, no labels.",
            ]
        )
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.8,
                max_output_tokens=400,
            ),
        )
        text = (response.text or "").strip()
        if not text:
            return "Сейчас не могу нормально ответить, чуть позже напишу."
        return text
