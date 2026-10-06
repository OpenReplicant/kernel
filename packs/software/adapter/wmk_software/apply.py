"""Playing a plan through the kernel's gateway: the shared kit's (`wmk_adapter.apply`)."""

from __future__ import annotations

from wmk_adapter.apply import Applier, ApplyError, Report, apply, chunk_at

__all__ = ["Applier", "ApplyError", "Report", "apply", "chunk_at"]
