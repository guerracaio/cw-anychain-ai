"""Settings API: network profiles edited from the UI. Secret values are write-only."""

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.blockchain.rpc_client import RpcClient
from app.config.models import AppConfig
from app.config.store import ConfigStore, ProfileError, new_profile
from app.explorer.blockscout_client import BlockscoutClient
from app.llm.factory import create_provider
from app.repositories.github_client import GitHubClient
from app.repositories.resolver import RepositoryService
from app.services.http import JsonHttpClient, UpstreamError

logger = logging.getLogger("anychain.settings")
router = APIRouter(prefix="/api/settings")
MESSAGES = {
    "settings_unavailable": "As configurações não podem ser editadas nesta execução.",
    "invalid_profile_id": "Identificador de perfil inválido: use letras minúsculas, números e -.",
    "profile_not_found": "Perfil não encontrado.",
    "profile_exists": "Já existe um perfil com esse identificador.",
    "profile_active": "O perfil ativo não pode ser excluído; ative outro antes.",
    "invalid_profile_file": "O arquivo do perfil está corrompido ou ilegível.",
    "invalid_config": "Há campos inválidos na configuração.",
}


class SettingsError(Exception):
    def __init__(self, status: int, code: str, fields: list[str] | None = None):
        self.status = status
        self.code = code
        self.fields = fields or []
        super().__init__(code)

    def body(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": MESSAGES.get(self.code, self.code),
                "fields": self.fields,
            }
        }


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class NetworkInput(Input):
    name: str | None = Field(default=None, max_length=100)
    chain_id: int | None = None
    native_currency: str | None = Field(default=None, max_length=20)
    native_decimals: int | None = None


class ExplorerInput(Input):
    base_url: str | None = Field(default=None, max_length=500)


class ContractInput(Input):
    address: str = Field(max_length=42)
    name: str = Field(max_length=100)


class RepositoryInput(Input):
    url: str = Field(max_length=500)
    branch: str = Field(default="main", max_length=200)
    contracts: list[ContractInput] = Field(default_factory=list, max_length=100)


class LlmInput(Input):
    provider: str | None = Field(default=None, max_length=50)
    model: str | None = Field(default=None, max_length=200)
    thinking_level: str | None = Field(default=None, max_length=20)
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None


class SecretChange(Input):
    action: Literal["keep", "set", "clear", "env"] = "keep"
    value: str | None = Field(default=None, max_length=4000)


class ProfileUpdate(Input):
    network: NetworkInput
    explorer: ExplorerInput
    repositories: list[RepositoryInput] = Field(default_factory=list, max_length=20)
    llm: LlmInput
    secrets: dict[Literal["rpc_url", "github_token", "llm_api_key"], SecretChange] = Field(
        default_factory=dict
    )
    # Creation only: start from another profile (keeps its analysis limits and secrets).
    copy_from: str | None = None


class ActivateInput(Input):
    id: str


def store_of(request: Request) -> ConfigStore:
    store = request.app.state.store
    if store is None:
        raise SettingsError(404, "settings_unavailable")
    return store


def lock_of(request: Request) -> asyncio.Lock:
    state = request.app.state
    if not hasattr(state, "settings_lock"):
        state.settings_lock = asyncio.Lock()
    return state.settings_lock


def failure(exc: ProfileError) -> SettingsError:
    status = {"profile_not_found": 404, "profile_active": 409, "invalid_config": 422}
    return SettingsError(status.get(exc.code, 400), exc.code, exc.fields)


def audit(action: str, profile_id: str, result: str) -> None:
    # Never log submitted values: they may contain secrets.
    logger.info("settings action=%s profile=%s result=%s", action, profile_id, result)


@router.get("/status")
async def status(request: Request) -> dict:
    store = request.app.state.store
    return {
        "available": store is not None,
        "active": store.active() if store else None,
    }


@router.get("/profiles")
async def profiles(request: Request) -> list[dict]:
    return store_of(request).summaries()


@router.get("/profiles/{profile_id}")
async def profile(profile_id: str, request: Request) -> dict:
    try:
        return store_of(request).view(profile_id)
    except ProfileError as exc:
        raise failure(exc) from None


@router.put("/profiles/{profile_id}")
async def save_profile(profile_id: str, body: ProfileUpdate, request: Request) -> dict:
    store = store_of(request)
    async with lock_of(request):
        try:
            if store.exists(profile_id):
                if body.copy_from:
                    raise SettingsError(409, "profile_exists")
                base = store.raw(profile_id)
            else:
                base = store.raw(body.copy_from) if body.copy_from else new_profile(profile_id)
                base.setdefault("network", {})["id"] = profile_id
            raw = store.apply(base, body.model_dump(exclude={"copy_from"}))
            config = store.save(profile_id, raw)
        except ProfileError as exc:
            audit("save", profile_id, exc.code)
            raise failure(exc) from None
        if profile_id == store.active():
            request.app.state.activate(config)
    audit("save", profile_id, "ok")
    return store.view(profile_id)


@router.delete("/profiles/{profile_id}")
async def delete_profile(profile_id: str, request: Request) -> dict:
    store = store_of(request)
    async with lock_of(request):
        try:
            store.delete(profile_id)
        except ProfileError as exc:
            audit("delete", profile_id, exc.code)
            raise failure(exc) from None
    audit("delete", profile_id, "ok")
    return {"deleted": profile_id}


@router.post("/active")
async def activate(body: ActivateInput, request: Request) -> dict:
    store = store_of(request)
    async with lock_of(request):
        try:
            config = store.load(body.id)
            store.set_active(body.id)
        except ProfileError as exc:
            audit("activate", body.id, exc.code)
            raise failure(exc) from None
        request.app.state.activate(config)
    audit("activate", body.id, "ok")
    return {"active": body.id}


async def _check(name: str, label: str, operation) -> dict:
    try:
        detail = await operation
    except UpstreamError as exc:
        return {"id": name, "label": label, "ok": False, "code": exc.code}
    except TimeoutError:
        return {"id": name, "label": label, "ok": False, "code": "timeout"}
    if isinstance(detail, dict):
        return {"id": name, "label": label, **detail}
    return {"id": name, "label": label, "ok": True, "code": "ok"}


async def run_checks(config: AppConfig, request: Request) -> list[dict]:
    client = request.app.state.http_client
    limits = config.analysis
    http = JsonHttpClient(client, limits.tool_timeout_seconds, limits.max_payload_bytes)

    async def rpc() -> dict:
        if not config.rpc.url:
            return {"ok": None, "code": "not_configured"}
        chain_id = await RpcClient(http, config.rpc.url.get_secret_value()).get_chain_id()
        if chain_id != config.network.chain_id:
            return {"ok": False, "code": "chain_mismatch"}
        return {"ok": True, "code": "ok"}

    async def llm() -> dict:
        provider, issue = create_provider(config.llm)
        # Configuration check only: calling the model would spend tokens.
        return {
            "ok": None if issue == "llm_not_configured" else provider is not None,
            "code": issue or "configured",
        }

    explorer = BlockscoutClient(http, str(config.explorer.base_url), 1, 1)
    token = config.github.token
    github = GitHubClient(
        http, token.get_secret_value() if token else None, limits.max_contract_payload_bytes
    )
    repositories = RepositoryService(github, config.repositories).repositories
    checks = [
        _check("explorer", "Explorer (Blockscout)", explorer.ping()),
        _check("rpc", "RPC", rpc()),
        *(
            _check(
                f"repository.{ref.label}",
                f"Repositório {ref.label}@{ref.branch}",
                github.get_branch_commit(ref),
            )
            for ref, _ in repositories
        ),
        _check("llm", "LLM", llm()),
    ]
    return list(await asyncio.gather(*checks))


@router.post("/profiles/{profile_id}/test")
async def test_profile(profile_id: str, request: Request) -> dict:
    store = store_of(request)
    try:
        config = store.load(profile_id)
    except ProfileError as exc:
        raise failure(exc) from None
    checks = await run_checks(config, request)
    audit("test", profile_id, ",".join(f"{c['id']}:{c['code']}" for c in checks))
    return {"checks": checks}
