import os
import re
from collections.abc import Mapping
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import ValidationError

from app.config.models import AppConfig

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigurationError(ValueError):
    """Safe configuration error that never contains resolved secrets."""

    def __init__(self, message: str, fields: list[str] | None = None):
        super().__init__(message)
        self.fields = fields or []


def _expand(value: object, environ: Mapping[str, str]) -> object:
    if isinstance(value, dict):
        return {key: _expand(item, environ) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item, environ) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name, default = match.groups()
        resolved = environ.get(name)
        if resolved:
            return resolved
        if default is not None:
            return default
        raise ConfigurationError(f"Required environment variable is missing: {name}")

    # Expand parsed scalar values, never YAML text: secrets cannot inject YAML keys.
    return ENV_PATTERN.sub(replace, value)


def expand(value: object, environ: Mapping[str, str]) -> object:
    """Public alias: resolve ${VAR} / ${VAR:-default} in parsed YAML values."""
    return _expand(value, environ)


def selected_config_path(environ: Mapping[str, str]) -> Path:
    selected = environ.get("APP_CONFIG") or "config/ethereum-mainnet.example.yaml"
    path = Path(selected)
    return path if path.is_absolute() else PROJECT_ROOT / path


def validate_config(data: object, environ: Mapping[str, str]) -> AppConfig:
    """Expand and validate parsed YAML; errors name fields only, never values."""
    try:
        return AppConfig.model_validate(_expand(data, environ))
    except ValidationError as exc:
        # Pydantic error details may include input values and URLs with API keys.
        fields = sorted({".".join(map(str, err["loc"])) or "root" for err in exc.errors()})
        raise ConfigurationError(
            f"Invalid configuration fields: {', '.join(fields)}", fields
        ) from None


def load_config(
    path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> AppConfig:
    if environ is None:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        environ = os.environ
    config_path = Path(path) if path else selected_config_path(environ)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError:
        raise ConfigurationError("Cannot read the selected configuration file.") from None
    except yaml.YAMLError:
        raise ConfigurationError("Invalid YAML in the selected configuration file.") from None
    return validate_config(data, environ)
