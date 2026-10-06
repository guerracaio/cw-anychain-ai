import re
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    TypeAdapter,
    field_validator,
    model_validator,
)


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class NetworkConfig(ConfigModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=100)
    name: str = Field(min_length=1, max_length=100)
    chain_id: int = Field(gt=0, strict=True)
    native_currency: str = Field(min_length=1, max_length=20)
    native_decimals: int = Field(default=18, ge=0, le=255)


class ExplorerConfig(ConfigModel):
    type: Literal["blockscout"] = "blockscout"
    base_url: HttpUrl

    @field_validator("base_url")
    @classmethod
    def public_base_url(cls, value: HttpUrl) -> HttpUrl:
        if value.username or value.password or value.query or value.fragment:
            raise ValueError("Explorer base URL must not include credentials, query or fragment")
        return value


class RpcConfig(ConfigModel):
    url: SecretStr | None = None

    @field_validator("url", mode="before")
    @classmethod
    def validate_url(cls, value: object) -> object:
        if value is None or value == "":
            return None
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        TypeAdapter(HttpUrl).validate_python(raw)
        return value


class ContractMapping(ConfigModel):
    """Names a deployed contract when the explorer cannot (e.g. unverified, private networks)."""

    address: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    name: str = Field(pattern=r"^[A-Za-z_$][A-Za-z0-9_$]*$", max_length=100)

    @field_validator("address")
    @classmethod
    def lowercase(cls, value: str) -> str:
        return value.lower()


class RepositoryConfig(ConfigModel):
    url: HttpUrl
    branch: str = Field(default="main", min_length=1, max_length=200, pattern=r"^[\w./-]+$")
    contracts: list[ContractMapping] = Field(default_factory=list)

    @field_validator("branch")
    @classmethod
    def safe_branch(cls, value: str) -> str:
        # The branch becomes part of an API path; dot segments could address other endpoints.
        if ".." in value or value.startswith("/") or value.endswith("/"):
            raise ValueError("Invalid branch name")
        return value

    @field_validator("url")
    @classmethod
    def github_repository(cls, value: HttpUrl) -> HttpUrl:
        parts = [part for part in (value.path or "").split("/") if part]
        if (
            value.scheme != "https"
            or value.username
            or value.password
            or value.query
            or value.fragment
            or len(parts) != 2
            or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts)
        ):
            raise ValueError("Repository URL must be https://<host>/<owner>/<repo>")
        return value

    @property
    def owner_repo(self) -> tuple[str, str]:
        owner, repo = [part for part in (self.url.path or "").split("/") if part]
        return owner, repo.removesuffix(".git")


class GitHubConfig(ConfigModel):
    token: SecretStr | None = None

    @field_validator("token", mode="before")
    @classmethod
    def empty_to_none(cls, value: object) -> object:
        return None if value == "" else value


class AbiConfig(ConfigModel):
    # This tuple fixes the required resolution priority.
    strategies: tuple[Literal["explorer"], Literal["repository"]] = ("explorer", "repository")


class LlmConfig(ConfigModel):
    provider: str | None = None
    model: str | None = None
    # Optional; otherwise <PROVIDER>_API_KEY is read from the environment (e.g. GEMINI_API_KEY).
    api_key: SecretStr | None = None
    timeout_seconds: float = Field(default=60, gt=0, le=120)
    temperature: float | None = Field(default=None, ge=0, le=2)
    # Reasoning effort for models that support it; lower is faster and cheaper.
    thinking_level: Literal["minimal", "low", "medium", "high"] | None = None
    # Optional prices (USD per 1M tokens) used only to log an estimated cost; never hardcoded.
    input_price_per_million: float | None = Field(default=None, ge=0)
    output_price_per_million: float | None = Field(default=None, ge=0)

    @field_validator(
        "provider",
        "model",
        "api_key",
        "thinking_level",
        "input_price_per_million",
        "output_price_per_million",
        mode="before",
    )
    @classmethod
    def empty_to_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("thinking_level", mode="before")
    @classmethod
    def no_reasoning(cls, value: object) -> object:
        # "none" disables the reasoning setting for models without it.
        return None if value == "none" else value

    @field_validator("provider")
    @classmethod
    def lowercase(cls, value: str | None) -> str | None:
        return value.lower() if value else value


class AnalysisConfig(ConfigModel):
    max_explorer_pages: int = Field(default=3, ge=1, le=10)
    max_collection_items: int = Field(default=100, ge=1, le=1000)
    max_abi_contracts: int = Field(default=10, ge=1, le=50)
    collection_timeout_seconds: float = Field(default=25, gt=0, le=120)
    max_agent_steps: int = Field(default=10, ge=1, le=50)
    # Thinking models count reasoning tokens against this limit.
    max_output_tokens: int = Field(default=8192, ge=256, le=65536)
    agent_timeout_seconds: float = Field(default=90, gt=0, le=120)
    max_llm_context_chars: int = Field(default=60000, ge=4000, le=400000)
    max_tool_result_chars: int = Field(default=8000, ge=1000, le=100000)
    tool_timeout_seconds: float = Field(default=15, gt=0, le=120)
    max_payload_bytes: int = Field(default=65536, ge=1024, le=1048576)
    # Verified-contract responses embed source code and are much larger than tx data.
    max_contract_payload_bytes: int = Field(default=2097152, ge=65536, le=16777216)
    max_repository_excerpt_chars: int = Field(default=12000, ge=100, le=100000)
    include_security_notes: bool = True

    @model_validator(mode="after")
    def fits_frontend_timeout(self) -> "AnalysisConfig":
        # The frontend proxy waits 135 s; collection and the agent must finish before that.
        if self.collection_timeout_seconds + self.agent_timeout_seconds > 125:
            raise ValueError("collection_timeout_seconds + agent_timeout_seconds must be <= 125")
        return self


class AppConfig(ConfigModel):
    network: NetworkConfig
    explorer: ExplorerConfig
    rpc: RpcConfig = Field(default_factory=RpcConfig)
    repositories: list[RepositoryConfig] = Field(default_factory=list, max_length=20)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    abi: AbiConfig = Field(default_factory=AbiConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
