from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
PERSONA_PATH = ROOT / "persona.txt"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    bot_token: str
    owner_user_id: int
    gemini_api_key: str
    gemini_model: str = "gemini-3.5-flash"
    # polling = local testing; webhook = Render / production
    mode: str = "polling"
    webhook_url: str = ""
    webhook_secret: str = "change-me"
    port: int = 8080
    data_dir: Path = Path("./data")
    history_limit: int = 40
    owner_pause_minutes: int = 30

    @model_validator(mode="after")
    def resolve_webhook_url(self) -> Settings:
        self.mode = self.mode.strip().lower()
        if self.mode not in {"polling", "webhook"}:
            raise ValueError("MODE must be 'polling' or 'webhook'")

        if self.mode == "polling":
            return self

        if not self.webhook_url:
            render_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
            if render_url:
                self.webhook_url = render_url
        if not self.webhook_url:
            raise ValueError(
                "WEBHOOK_URL is required in webhook mode "
                "(or set automatically via RENDER_EXTERNAL_URL on Render)"
            )
        return self

    @field_validator("webhook_secret")
    @classmethod
    def sanitize_secret(cls, value: str) -> str:
        cleaned = "".join(ch for ch in value if ch.isalnum() or ch in "_-")
        return cleaned or "chatauto-secret"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "chatauto.db"

    @property
    def webhook_path(self) -> str:
        return f"/webhook/{self.webhook_secret}"

    @property
    def webhook_full_url(self) -> str:
        return f"{self.webhook_url.rstrip('/')}{self.webhook_path}"

    def load_persona(self) -> str:
        if PERSONA_PATH.exists():
            return PERSONA_PATH.read_text(encoding="utf-8").strip()
        return "Reply casually as the account owner. Keep messages short."


@lru_cache
def get_settings() -> Settings:
    return Settings()
