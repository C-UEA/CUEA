"""
Secrets and config helpers.

Responsibilities:
- load .env into environment (optional)
- load YAML or JSON config file (optional)
- build an exchange adapter from environment variables
"""
from __future__ import annotations

import os
from typing import Optional, Dict, Any
import json

# optional imports
try:
    import yaml  # type: ignore
except Exception:
    yaml = None  # type: ignore

from dotenv import load_dotenv  # type: ignore
from .registry import get_exchange_adapter


def load_env_file(env_path: Optional[str] = None) -> None:
    """
    Load environment variables from .env file if present.
    If env_path is None, will attempt to load ".env" in cwd silently.
    """
    if env_path:
        if os.path.exists(env_path):
            load_dotenv(env_path, override=False)
    else:
        # load default .env if present
        load_dotenv(override=False)


def load_config_file(path: Optional[str]) -> Dict[str, Any]:
    """
    Load YAML or JSON config file. Returns empty dict if no file.
    Requires PyYAML for YAML files.
    """
    if not path:
        return {}
    if not os.path.exists(path):
        return {}
    ext = os.path.splitext(path)[1].lower()
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    if ext in (".yaml", ".yml"):
        if yaml is None:
            raise RuntimeError("PyYAML is required to load YAML config files. Install with `pip install pyyaml`.")
        return yaml.safe_load(text) or {}
    elif ext == ".json":
        return json.loads(text)
    else:
        # try YAML first, then JSON
        if yaml is not None:
            try:
                return yaml.safe_load(text) or {}
            except Exception:
                pass
        try:
            return json.loads(text)
        except Exception:
            return {}


def get_exchange_adapter_from_env(
    *,
    env_file: Optional[str] = None,
    config_file: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
):
    """
    Build and return an exchange adapter instance using environment variables.

    Order of sources:
    1. If env_file provided, load it.
    2. Use provided `env` dict to override os.environ (useful for tests).
    3. Read CUEA_EXCHANGE, CUEA_API_KEY, CUEA_API_SECRET and optional CUEA_CONFIG_FILE.
    4. Load config file (YAML/JSON) and pass the per-exchange config into adapter.

    Returns:
        Exchange adapter instance (as returned by registry.get_exchange_adapter).
    Raises:
        KeyError if exchange not specified or not available.
    """
    # Step 1: load .env
    if env_file is not None:
        load_env_file(env_file)
    else:
        # If CUEA_ENV_FILE points to a non-default env file, load that
        if os.getenv("CUEA_ENV_FILE"):
            load_env_file(os.getenv("CUEA_ENV_FILE"))

    # Step 2: overlay `env` dict if provided (useful in tests)
    if env:
        for k, v in env.items():
            os.environ[k] = v

    # Step 3: gather credentials / exchange name
    exchange = os.getenv("CUEA_EXCHANGE")
    api_key = os.getenv("CUEA_API_KEY")
    api_secret = os.getenv("CUEA_API_SECRET")
    # config file path argument takes precedence over env var
    cfg_path = config_file or os.getenv("CUEA_CONFIG_FILE")

    if not exchange:
        raise KeyError("CUEA_EXCHANGE environment variable not set. Example: CUEA_EXCHANGE=binance")

    # Step 4: load config and get per-exchange config
    cfg = load_config_file(cfg_path)
    exchange_cfg: Dict[str, Any] = {}
    if isinstance(cfg, dict):
        # prefer cfg[exchange] if key present (even if empty dict)
        if exchange in cfg:
            exchange_cfg = cfg.get(exchange) or {}
        else:
            # fallback to full config when no per-exchange section present
            exchange_cfg = cfg

    # instantiate adapter via registry
    adapter = get_exchange_adapter(exchange, api_key=api_key, secret=api_secret, config=exchange_cfg or {})
    return adapter
