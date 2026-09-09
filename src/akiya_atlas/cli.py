"""`akiya-atlas` コマンド。sitemill の CLI をこのサービスのルートで起動する薄い包み。"""

from __future__ import annotations

import os
from pathlib import Path


def main() -> None:
    from sitemill.cli import app

    root = Path(__file__).resolve().parents[2]
    if (root / "site.toml").is_file() and "--root" not in os.sys.argv:  # type: ignore[attr-defined]
        os.chdir(root)
    app()
