"""Network profiles editable from the UI, persisted as local YAML files.

A profile is a complete AppConfig document kept *raw*: values may still contain ${VAR}
placeholders, expanded by the regular loader. Secrets (RPC URL, GitHub token, LLM key) live in
the same git-ignored files, like the .env, and never leave this module in clear text.
"""

import copy
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from app.config.loader import ConfigurationError, expand, validate_config
from app.config.models import AppConfig

PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
PLACEHOLDER = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}$")
# Secret paths and the environment variable a profile can defer to.
SECRETS: dict[str, tuple[tuple[str, str], str | None]] = {
    "rpc_url": (("rpc", "url"), "RPC_URL"),
    "github_token": (("github", "token"), "GITHUB_TOKEN"),
    # No fixed variable: the provider factory falls back to <PROVIDER>_API_KEY.
    "llm_api_key": (("llm", "api_key"), None),
}
# Non-secret fields edited by the UI (others, like analysis limits, are preserved).
EDITABLE = [
    ("network", "name"),
    ("network", "chain_id"),
    ("network", "native_currency"),
    ("network", "native_decimals"),
    ("explorer", "base_url"),
    ("llm", "provider"),
    ("llm", "model"),
    ("llm", "thinking_level"),
    ("llm", "input_price_per_million"),
    ("llm", "output_price_per_million"),
]


class ProfileError(Exception):
    def __init__(self, code: str, fields: list[str] | None = None):
        self.code = code
        self.fields = fields or []
        super().__init__(code)


def _get(raw: Mapping, path: tuple[str, ...]) -> Any:
    node: Any = raw
    for key in path:
        if not isinstance(node, Mapping) or key not in node:
            return None
        node = node[key]
    return node


def _set(raw: dict, path: tuple[str, ...], value: Any) -> None:
    node = raw
    for key in path[:-1]:
        if not isinstance(node.get(key), dict):
            node[key] = {}
        node = node[key]
    node[path[-1]] = value


def _remove(raw: dict, path: tuple[str, ...]) -> None:
    parent = _get(raw, path[:-1])
    if isinstance(parent, dict):
        parent.pop(path[-1], None)


def _placeholder(value: Any) -> str | None:
    match = PLACEHOLDER.match(value) if isinstance(value, str) else None
    return match.group(1) if match else None


def _same(expanded: Any, new: Any) -> bool:
    if new is None or new == "":
        return expanded in (None, "")
    if str(new) == str(expanded):
        return True
    try:
        return float(new) == float(expanded)
    except (TypeError, ValueError):
        return False


def new_profile(profile_id: str) -> dict:
    """Skeleton for a profile created from scratch; secrets default to the .env."""
    return {
        "network": {"id": profile_id},
        "explorer": {"type": "blockscout"},
        "rpc": {"url": None},
        "repositories": [],
        "github": {"token": "${GITHUB_TOKEN:-}"},
        "abi": {"strategies": ["explorer", "repository"]},
        "llm": {"provider": "${LLM_PROVIDER:-}", "model": "${LLM_MODEL:-}"},
    }


class ConfigStore:
    def __init__(self, root: Path, environ: Mapping[str, str]):
        self.root = root
        self.profiles = root / "profiles"
        self.state = root / "state.json"
        self.environ = environ

    # Files ------------------------------------------------------------------------------

    def _path(self, profile_id: str) -> Path:
        if not PROFILE_ID.fullmatch(profile_id) or len(profile_id) > 100:
            raise ProfileError("invalid_profile_id")
        return self.profiles / f"{profile_id}.yaml"

    def _write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)  # Best effort: no effect on Windows ACLs.
        except OSError:
            pass
        os.replace(temporary, path)

    def seed(self, sources: list[Path], active_source: Path | None) -> None:
        """First run only: copy the YAML files verbatim, placeholders included."""
        if self.ids():
            return
        active = None
        for source in sources:
            try:
                text = source.read_text(encoding="utf-8")
                profile_id = _get(yaml.safe_load(text), ("network", "id"))
                path = self._path(str(profile_id))
            except (OSError, yaml.YAMLError, ProfileError):
                continue
            if not path.exists():
                self._write(path, text)
            if active_source and source.resolve() == active_source.resolve():
                active = profile_id
        if active:
            self.set_active(active)

    def ids(self) -> list[str]:
        if not self.profiles.is_dir():
            return []
        return sorted(p.stem for p in self.profiles.glob("*.yaml") if PROFILE_ID.fullmatch(p.stem))

    def exists(self, profile_id: str) -> bool:
        return self._path(profile_id).exists()

    def raw(self, profile_id: str) -> dict:
        path = self._path(profile_id)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ProfileError("profile_not_found") from None
        except (OSError, yaml.YAMLError):
            raise ProfileError("invalid_profile_file") from None
        if not isinstance(data, dict):
            raise ProfileError("invalid_profile_file")
        return data

    def load(self, profile_id: str) -> AppConfig:
        return self.validate(profile_id, self.raw(profile_id))

    def validate(self, profile_id: str, raw: dict) -> AppConfig:
        try:
            config = validate_config(raw, self.environ)
        except ConfigurationError as exc:
            raise ProfileError("invalid_config", exc.fields) from None
        if config.network.id != profile_id:
            raise ProfileError("invalid_config", ["network.id"])
        return config

    def save(self, profile_id: str, raw: dict) -> AppConfig:
        config = self.validate(profile_id, raw)
        text = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
        self._write(self._path(profile_id), text)
        return config

    def delete(self, profile_id: str) -> None:
        if profile_id == self.active():
            raise ProfileError("profile_active")
        path = self._path(profile_id)
        if not path.exists():
            raise ProfileError("profile_not_found")
        path.unlink()

    def active(self) -> str | None:
        try:
            value = json.loads(self.state.read_text(encoding="utf-8")).get("active")
        except (OSError, ValueError, AttributeError):
            value = None
        ids = self.ids()
        if isinstance(value, str) and value in ids:
            return value
        return ids[0] if ids else None

    def set_active(self, profile_id: str) -> None:
        if not self.exists(profile_id):
            raise ProfileError("profile_not_found")
        self._write(self.state, json.dumps({"active": profile_id}))

    def summaries(self) -> list[dict]:
        active = self.active()
        items = []
        for profile_id in self.ids():
            try:
                config = self.load(profile_id)
                name, chain_id, valid = config.network.name, config.network.chain_id, True
            except ProfileError:
                raw = self._safe_raw(profile_id)
                name = _get(raw, ("network", "name")) or profile_id
                chain_id, valid = _get(raw, ("network", "chain_id")), False
            items.append(
                {
                    "id": profile_id,
                    "name": str(name),
                    "chain_id": chain_id if isinstance(chain_id, int) else None,
                    "active": profile_id == active,
                    "valid": valid,
                }
            )
        return items

    def _safe_raw(self, profile_id: str) -> dict:
        try:
            return self.raw(profile_id)
        except ProfileError:
            return {}

    # Views and updates ------------------------------------------------------------------

    def secret_status(self, raw: dict, name: str) -> dict:
        path, env_var = SECRETS[name]
        value = _get(raw, path)
        variable = _placeholder(value)
        if variable:
            resolved = expand(value, self.environ)
            return {"configured": bool(resolved), "source": "env", "env_var": variable}
        if value not in (None, ""):
            return {"configured": True, "source": "file", "env_var": None}
        if name == "llm_api_key":
            provider = expand(_get(raw, ("llm", "provider")) or "", self.environ)
            fallback = f"{str(provider).upper()}_API_KEY" if provider else None
            if fallback and self.environ.get(fallback):
                return {"configured": True, "source": "env", "env_var": fallback}
            return {"configured": False, "source": None, "env_var": fallback}
        return {"configured": False, "source": None, "env_var": env_var}

    def view(self, profile_id: str) -> dict:
        """Editable values (expanded) and secret status. Never contains secret values."""
        raw = self.raw(profile_id)
        expanded: Any
        try:
            expanded = expand(raw, self.environ)
        except ConfigurationError:
            expanded = raw
        env_bound = {
            ".".join(path): variable
            for path in EDITABLE
            if (variable := _placeholder(_get(raw, path)))
        }

        def value(path: tuple[str, ...]) -> Any:
            item = _get(expanded, path)
            return None if item == "" else item

        def section(name: str) -> dict:
            return {path[1]: value(path) for path in EDITABLE if path[0] == name}

        repositories = []
        for item in _get(expanded, ("repositories",)) or []:
            if isinstance(item, dict):
                repositories.append(
                    {
                        "url": item.get("url"),
                        "branch": item.get("branch", "main"),
                        "contracts": [
                            {"address": c.get("address"), "name": c.get("name")}
                            for c in item.get("contracts") or []
                            if isinstance(c, dict)
                        ],
                    }
                )
        return {
            "id": profile_id,
            "network": section("network"),
            "explorer": {"base_url": value(("explorer", "base_url"))},
            "repositories": repositories,
            "llm": section("llm"),
            "secrets": {name: self.secret_status(raw, name) for name in SECRETS},
            "env_bound": env_bound,
        }

    def apply(self, raw: dict, update: dict) -> dict:
        """Apply a UI update to a raw document; unchanged env-bound values stay bound."""
        result = copy.deepcopy(raw)
        for path in EDITABLE:
            section, key = path
            if section not in update or key not in update[section]:
                continue
            new = update[section][key]
            current = _get(result, path)
            if _placeholder(current):
                try:
                    if _same(expand(current, self.environ), new):
                        continue
                except ConfigurationError:
                    pass
            if new is None or new == "":
                _set(result, path, None)
            else:
                _set(result, path, new)
        if "repositories" in update:
            result["repositories"] = [
                {
                    "url": item["url"],
                    "branch": item.get("branch") or "main",
                    **(
                        {"contracts": [dict(c) for c in item["contracts"]]}
                        if item.get("contracts")
                        else {}
                    ),
                }
                for item in update["repositories"]
            ]
        for name, change in (update.get("secrets") or {}).items():
            path, env_var = SECRETS[name]
            action = change.get("action", "keep")
            if action == "set" and change.get("value"):
                _set(result, path, change["value"])
            elif action == "clear":
                _set(result, path, None)
            elif action == "env":
                if env_var:
                    _set(result, path, f"${{{env_var}:-}}")
                else:
                    _remove(result, path)
        return result
