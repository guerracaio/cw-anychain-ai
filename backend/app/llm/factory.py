import os
from collections.abc import Mapping

from app.config.models import LlmConfig
from app.llm.base import LLMProvider


def create_provider(
    config: LlmConfig, environ: Mapping[str, str] | None = None
) -> tuple[LLMProvider | None, str | None]:
    """Build the configured provider, or return a safe reason why the agent is disabled."""
    environ = os.environ if environ is None else environ
    if not config.provider:
        return None, "llm_not_configured"
    if not config.model:
        return None, "llm_model_not_configured"
    key = config.api_key.get_secret_value() if config.api_key else None
    key = key or environ.get(f"{config.provider.upper()}_API_KEY")
    if not key:
        return None, "llm_api_key_missing"
    if config.provider == "gemini":
        # Imported lazily so other providers never load this SDK.
        from app.llm.gemini_provider import GeminiProvider

        provider = GeminiProvider(
            key,
            config.model,
            config.timeout_seconds,
            config.temperature,
            thinking_level=config.thinking_level,
        )
        return provider, None
    if config.provider == "openai":
        from app.llm.openai_provider import OpenAIProvider

        provider = OpenAIProvider(
            key,
            config.model,
            config.timeout_seconds,
            config.temperature,
            # thinking_level maps to the Responses API reasoning effort.
            reasoning_effort=config.thinking_level,
        )
        return provider, None
    return None, "llm_provider_not_supported"
