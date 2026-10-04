"""Gateway settings, read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Transport = Literal["stdio", "streamable-http"]


@dataclass(frozen=True)
class Settings:
    writer_dsn: str
    reader_dsn: str
    profile_path: Path
    transport: Transport = "streamable-http"
    host: str = "0.0.0.0"
    port: int = 8000
    embedding_url: str | None = None
    embedding_model: str | None = None
    embedding_api_key: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        transport = os.environ.get("WMK_TRANSPORT", "streamable-http")
        if transport not in ("stdio", "streamable-http"):
            raise ValueError("WMK_TRANSPORT must be stdio or streamable-http")
        return cls(
            writer_dsn=_required("WMK_WRITER_DSN"),
            reader_dsn=_required("WMK_READER_DSN"),
            profile_path=Path(os.environ.get("WMK_PROFILE", "profiles/interactive.yaml")),
            transport=transport,  # type: ignore[arg-type]
            host=os.environ.get("WMK_HOST", "0.0.0.0"),
            port=int(os.environ.get("WMK_PORT", "8000")),
            embedding_url=os.environ.get("WMK_EMBEDDING_URL") or None,
            embedding_model=os.environ.get("WMK_EMBEDDING_MODEL") or None,
            embedding_api_key=os.environ.get("WMK_EMBEDDING_API_KEY") or None,
        )


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} is required")
    return value
