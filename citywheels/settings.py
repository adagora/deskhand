"""One set of environment names for the help desk.

    CITYWHEELS_PROVIDER=openai
    OPENAI_API_KEY=
    OPENAI_MODEL=gpt-4o-mini
    OPENAI_BASE_URL=https://api.openai.com/v1
    GEMINI_API_KEY=
    GEMINI_MODEL=gemini-3.8-flash

Plain KEY=value lines. The CLI also accepts quotes and a leading `export`.
"""

import os
from dataclasses import dataclass

from citywheels.dotenv import load_dotenv

OPENAI_MODEL_DEFAULT = "gpt-4o-mini"
GEMINI_MODEL_DEFAULT = "gemini-3.8-flash"
OPENAI_BASE_URL_DEFAULT = "https://api.openai.com/v1"


@dataclass(frozen=True)
class Settings:
    provider: str
    openai_key: str
    openai_model: str
    openai_base_url: str
    gemini_key: str
    gemini_model: str


def from_mapping(env, explicit_provider: str | None = None) -> Settings:
    def get(name: str) -> str:
        return str(env.get(name, "")).strip()

    openai_key = get("OPENAI_API_KEY")
    gemini_key = get("GEMINI_API_KEY")
    provider = (explicit_provider or get("CITYWHEELS_PROVIDER")).strip().lower()
    if not provider:
        if openai_key:
            provider = "openai"
        elif gemini_key:
            provider = "gemini"
        else:
            provider = "fake"
    return Settings(
        provider=provider,
        openai_key=openai_key,
        openai_model=get("OPENAI_MODEL") or OPENAI_MODEL_DEFAULT,
        openai_base_url=(get("OPENAI_BASE_URL") or OPENAI_BASE_URL_DEFAULT).rstrip("/"),
        gemini_key=gemini_key,
        gemini_model=get("GEMINI_MODEL") or GEMINI_MODEL_DEFAULT,
    )


def current(explicit_provider: str | None = None) -> Settings:
    load_dotenv()
    return from_mapping(os.environ, explicit_provider)
