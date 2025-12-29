"""Configuration loader for the SPX Vol Signal system (Railway Production)"""
import os


def load_config():
    """Load configuration from environment variables (Railway)"""
    config = {
        'OPENAI_API_KEY': os.environ.get('OPENAI_API_KEY'),
        'POLYGON_API_KEY': os.environ.get('POLYGON_API_KEY'),
        'TRADE_AGGRESSIVE_URL': os.environ.get('TRADE_AGGRESSIVE_URL'),
        'TRADE_NORMAL_URL': os.environ.get('TRADE_NORMAL_URL'),
        'TRADE_CONSERVATIVE_URL': os.environ.get('TRADE_CONSERVATIVE_URL'),
        'NO_TRADE_URL': os.environ.get('NO_TRADE_URL')
    }
    
    missing = [k for k, v in config.items() if not v]
    if missing:
        raise ValueError(f"Missing environment variables: {', '.join(missing)}")
    
    return config


# Global config instance (loaded once at import)
_CONFIG = None


def get_config():
    """Get the global config instance (lazy loading)"""
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = load_config()
    return _CONFIG
