"""Extract Pipeline：扫盘 → path →（可选）Agent → 正文正则 → mtime → Manifest。"""

from __future__ import annotations

from src.extract.pipeline import run_extract_pipeline

__all__ = ["run_extract_pipeline"]
