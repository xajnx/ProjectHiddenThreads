from __future__ import annotations

import logging
import logging.config
from pathlib import Path

import yaml


def setup_logging(config_path: Path) -> None:
    if not config_path.exists():
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        return

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    logging.config.dictConfig(config)
