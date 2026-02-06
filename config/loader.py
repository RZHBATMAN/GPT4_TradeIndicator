"""Configuration loader for the SPX Vol Signal system.

Load order: local .config file first (project root), then fall back to
environment variables (e.g. Railway). Use .config for local dev and
env vars for deployed environments.
"""
import logging
import os
from configparser import ConfigParser
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Keys required for the app (same names as env vars)
REQUIRED_KEYS = [
    "OPENAI_API_KEY",
    "POLYGON_API_KEY",
    "TRADE_AGGRESSIVE_URL",
    "TRADE_NORMAL_URL",
    "TRADE_CONSERVATIVE_URL",
    "NO_TRADE_URL",
]


def _project_root() -> Path:
    """Project root: parent of the config package directory."""
    return Path(__file__).resolve().parent.parent


def _load_from_file(config_path: Path) -> Optional[Dict[str, str]]:
    """Load config from INI-style .config file. Returns None if file missing or unreadable."""
    if not config_path.is_file():
        return None
    try:
        parser = ConfigParser()
        parser.read(config_path, encoding="utf-8")
        out: Dict[str, str] = {}
        if parser.has_section("API_KEYS"):
            out["OPENAI_API_KEY"] = parser.get("API_KEYS", "OPENAI_API_KEY", fallback="").strip() or None
            out["POLYGON_API_KEY"] = parser.get("API_KEYS", "POLYGON_API_KEY", fallback="").strip() or None
        if parser.has_section("WEBHOOKS"):
            out["TRADE_AGGRESSIVE_URL"] = parser.get("WEBHOOKS", "TRADE_AGGRESSIVE_URL", fallback="").strip() or None
            out["TRADE_NORMAL_URL"] = parser.get("WEBHOOKS", "TRADE_NORMAL_URL", fallback="").strip() or None
            out["TRADE_CONSERVATIVE_URL"] = parser.get("WEBHOOKS", "TRADE_CONSERVATIVE_URL", fallback="").strip() or None
            out["NO_TRADE_URL"] = parser.get("WEBHOOKS", "NO_TRADE_URL", fallback="").strip() or None
        return out
    except Exception as e:
        logger.warning("Could not load .config file %s: %s", config_path, e)
        return None


def load_config() -> Dict[str, str]:
    """Load config: .config in project root first, then environment variables.

    - Local: place .config in project root (see .config.example for format).
    - Railway/deployed: set env vars; .config is absent so only env is used.
    """
    config_path = _project_root() / ".config"
    result: Dict[str, str] = {}

    # 1) Try local .config first
    file_config = _load_from_file(config_path)
    if file_config:
        for k in REQUIRED_KEYS:
            result[k] = (file_config.get(k) or os.environ.get(k)) or ""
        logger.info("Config loaded from .config with env fallback: %s", config_path)
    else:
        # 2) No .config or failed: use environment only (e.g. Railway)
        for k in REQUIRED_KEYS:
            result[k] = os.environ.get(k) or ""
        logger.info("Config loaded from environment variables (no .config)")

    missing = [k for k in REQUIRED_KEYS if not (result.get(k) or "").strip()]
    if missing:
        raise ValueError(f"Missing configuration: {', '.join(missing)}. Set in .config or environment.")

    return result


# Global config instance (loaded once at import)
_CONFIG: Optional[Dict[str, str]] = None


def get_config() -> Dict[str, str]:
    """Return the global config (lazy-loaded)."""
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = load_config()
    return _CONFIG
