"""Backward-compat shim — delegates to core.config."""
from core.config import load_config, get_config, get_desk_config, REQUIRED_KEYS, OPTIONAL_KEYS

__all__ = ['load_config', 'get_config', 'get_desk_config', 'REQUIRED_KEYS', 'OPTIONAL_KEYS']
