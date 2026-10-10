"""Environment-derived settings shared by scripts: database and data root."""
import os
from pathlib import Path

import psycopg


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set (see .env.example)")
    return url


def connect(**kwargs) -> psycopg.Connection:
    return psycopg.connect(database_url(), **kwargs)


def data_root() -> Path:
    """$PC_DATA, default ./data (docs/FILESYSTEM.md). Created on first use."""
    root = Path(os.environ.get("PC_DATA", "data")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root
