import os
from pathlib import Path
from typing import Any, Dict

import yaml


def load_settings() -> Dict[str, Any]:
    config_path = Path(__file__).resolve().parent.parent / "config.yml"

    with config_path.open("r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}

    engine = config.setdefault("engine", {})
    sources = config.setdefault("sources", {})

    engine["max_workers"] = int(os.getenv("MAX_WORKERS", engine.get("max_workers", 5)))
    engine["cache_ttl_seconds"] = int(os.getenv("CACHE_TTL_SECONDS", engine.get("cache_ttl_seconds", 900)))
    engine["request_timeout_seconds"] = float(os.getenv("REQUEST_TIMEOUT_SECONDS", engine.get("request_timeout_seconds", 14)))
    engine["enable_web_sources"] = os.getenv("ENABLE_WEB_SOURCES", "1") == "1"

    disabled = {
        s.strip()
        for s in os.getenv("DISABLED_SOURCES", "").split(",")
        if s.strip()
    }
    for source_id in disabled:
        if source_id in sources:
            sources[source_id]["enabled"] = False

    return config
