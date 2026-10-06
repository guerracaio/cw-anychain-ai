import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.analyze import router
from app.api.settings import SettingsError
from app.api.settings import router as settings_router
from app.blockchain.rpc_client import RpcClient
from app.config.loader import PROJECT_ROOT, selected_config_path
from app.config.models import AppConfig, NetworkConfig
from app.config.store import ConfigStore
from app.domain.analysis import ApiError, ErrorResponse
from app.explorer.blockscout_client import BlockscoutClient
from app.llm.base import LLMProvider
from app.llm.factory import create_provider
from app.repositories.github_client import GitHubClient
from app.repositories.resolver import RepositoryService
from app.services.analysis import AnalysisService
from app.services.http import JsonHttpClient

logger = logging.getLogger("anychain.api")


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    network: str
    analysis_available: bool = True
    # Configuration only: whether a provider is configured, not whether it is reachable.
    explanation_available: bool = False


def configure_logging() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # The SDK logs retried provider errors verbatim; keep only warnings and above.
    logging.getLogger("google_genai").setLevel(logging.WARNING)
    app_logger = logging.getLogger("anychain")
    app_logger.setLevel(logging.INFO)
    if not app_logger.handlers:
        app_logger.addHandler(logging.StreamHandler())


def make_service(
    settings: AppConfig,
    client: httpx.AsyncClient,
    *,
    llm: LLMProvider | None = None,
    use_llm: bool = True,
) -> AnalysisService:
    """Wire clients from configuration over a shared HTTP client (no resources of its own)."""
    http = JsonHttpClient(
        client, settings.analysis.tool_timeout_seconds, settings.analysis.max_payload_bytes
    )
    explorer = BlockscoutClient(
        http,
        str(settings.explorer.base_url),
        settings.analysis.max_explorer_pages,
        settings.analysis.max_collection_items,
        settings.analysis.max_contract_payload_bytes,
    )
    rpc = RpcClient(http, settings.rpc.url.get_secret_value()) if settings.rpc.url else None
    token = settings.github.token
    github = GitHubClient(
        http,
        token.get_secret_value() if token else None,
        settings.analysis.max_contract_payload_bytes,
    )
    repositories = RepositoryService(github, settings.repositories)
    if not use_llm:
        provider, llm_issue = None, "llm_disabled"
    elif llm:
        provider, llm_issue = llm, None
    else:
        provider, llm_issue = create_provider(settings.llm)
    return AnalysisService(settings, explorer, rpc, repositories, provider, llm_issue)


@asynccontextmanager
async def build_services(
    settings: AppConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    llm: LLMProvider | None = None,
    use_llm: bool = True,
) -> AsyncIterator[AnalysisService]:
    """Services with their own HTTP client. Used by the offline evaluation."""
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
        yield make_service(settings, client, llm=llm, use_llm=use_llm)


def default_store() -> ConfigStore:
    """Local, git-ignored profiles seeded from config/*.yaml and APP_CONFIG on first run."""
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    store = ConfigStore(PROJECT_ROOT / "config" / "local", os.environ)
    selected = selected_config_path(os.environ)
    sources = sorted((PROJECT_ROOT / "config").glob("*.yaml"))
    if selected not in sources:
        sources.append(selected)
    store.seed(sources, selected)
    return store


def create_app(
    config: AppConfig | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    llm: LLMProvider | None = None,
    store: ConfigStore | None = None,
) -> FastAPI:
    """With an injected config (tests), settings are read-only unless a store is given."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        profiles = store if store is not None or config is not None else default_store()
        active = profiles.active() if profiles else None
        settings = config if config is not None else profiles.load(active)  # type: ignore[union-attr]
        app.state.store = profiles
        async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
            app.state.http_client = client

            def activate(new_settings: AppConfig) -> None:
                # Atomic swap: requests in flight finish with the service they started with.
                service = make_service(new_settings, client, llm=llm)
                app.state.config, app.state.analysis = new_settings, service

            app.state.activate = activate
            activate(settings)
            yield

    app = FastAPI(title="Anychain Transaction Assistant", version="0.1.0", lifespan=lifespan)
    app.include_router(router)
    app.include_router(settings_router)

    @app.exception_handler(SettingsError)
    async def settings_error(request: Request, exc: SettingsError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # Only the exception type is logged: messages may carry URLs or upstream content.
        identifier = getattr(request.state, "request_id", "-")
        logger.error("request_id=%s unexpected_error=%s", identifier, type(exc).__name__)
        body = ErrorResponse(
            error=ApiError(
                code="internal_error",
                message="Erro interno inesperado. Tente novamente; se persistir, consulte os "
                f"logs do servidor com o request_id {identifier}.",
            )
        )
        headers = {"X-Request-ID": identifier}
        return JSONResponse(body.model_dump(), status_code=500, headers=headers)

    @app.get("/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        return HealthResponse(
            network=request.app.state.config.network.id,
            explanation_available=request.app.state.analysis.llm is not None,
        )

    @app.get("/api/network", response_model=NetworkConfig)
    async def network(request: Request) -> NetworkConfig:
        # Explicit allowlist: RPC credentials and integration settings stay server-side.
        return request.app.state.config.network

    return app


app = create_app()
