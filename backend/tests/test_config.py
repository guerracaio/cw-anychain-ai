from pathlib import Path

import pytest

from app.config import loader
from app.config.loader import ConfigurationError, load_config

BASE = """
network:
  id: test-chain
  name: Test Chain
  chain_id: 123
  native_currency: TEST
explorer:
  base_url: https://explorer.example
rpc:
  url: ${RPC_URL:-}
"""


def write_config(tmp_path: Path, contents: str = BASE) -> Path:
    path = tmp_path / "network.yaml"
    path.write_text(contents, encoding="utf-8")
    return path


def test_config_loads_without_credentials(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path), environ={})
    assert config.network.chain_id == 123
    assert config.rpc.url is None
    assert config.abi.strategies == ("explorer", "repository")


def test_environment_interpolation_is_scalar_and_secret_is_hidden(tmp_path: Path) -> None:
    secret = "https://rpc.example/private-api-key"
    config = load_config(write_config(tmp_path), environ={"RPC_URL": secret})
    assert config.rpc.url is not None
    assert config.rpc.url.get_secret_value() == secret
    assert secret not in repr(config)
    assert secret not in config.model_dump_json()
    config = load_config(
        write_config(tmp_path, BASE + "\nllm:\n  model: ${MODEL}\n"),
        environ={"MODEL": "value\nanalysis:\n  max_agent_steps: 50"},
    )
    assert config.analysis.max_agent_steps == 10
    assert config.llm.model == "value\nanalysis:\n  max_agent_steps: 50"


def test_missing_required_variable(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="RPC_URL"):
        load_config(write_config(tmp_path, BASE.replace("${RPC_URL:-}", "${RPC_URL}")), environ={})


@pytest.mark.parametrize(
    "contents",
    [
        BASE.replace("chain_id: 123", "chain_id: 0"),
        BASE + "\nanalysis:\n  max_agent_steps: 0",
        BASE + "\nanalysis:\n  max_abi_contracts: 0",
        BASE + "\nanalysis:\n  max_abi_contracts: 51",
        BASE + "\nanalysis:\n  max_contract_payload_bytes: 1024",
        BASE + "\nabi:\n  strategies: [repository, explorer]",
        BASE + "\nunknown_setting: true",
        "[invalid: yaml",
        "- not a config mapping",
    ],
)
def test_invalid_config_is_rejected(tmp_path: Path, contents: str) -> None:
    with pytest.raises(ConfigurationError):
        load_config(write_config(tmp_path, contents), environ={})


def test_invalid_url_does_not_leak_secret(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as error:
        load_config(write_config(tmp_path), environ={"RPC_URL": "secret-credential"})
    assert "secret-credential" not in str(error.value)
    assert "rpc.url" in str(error.value)


def test_missing_file_is_clear(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="Cannot read"):
        load_config(tmp_path / "missing.yaml", environ={})


def test_network_switch_is_only_configuration() -> None:
    first = load_config("config/ethereum-mainnet.example.yaml", environ={})
    second = load_config(environ={"APP_CONFIG": "config/sepolia.example.yaml"})
    assert first.network.chain_id == 1
    assert second.network.chain_id == 11155111
    assert first.explorer.base_url != second.explorer.base_url
    assert first.analysis.max_abi_contracts == second.analysis.max_abi_contracts == 10


def test_dotenv_loading_preserves_existing_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(tmp_path)
    (tmp_path / ".env").write_text(
        "APP_CONFIG=network.yaml\nRPC_URL=https://from-file.example\n", encoding="utf-8"
    )
    monkeypatch.setattr(loader, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("APP_CONFIG", raising=False)
    monkeypatch.setenv("RPC_URL", "https://from-environment.example")
    config = load_config()
    assert config.network.id == "test-chain"
    assert config.rpc.url is not None
    assert config.rpc.url.get_secret_value() == "https://from-environment.example"


REPOSITORIES = """
github:
  token: ${GITHUB_TOKEN:-}
repositories:
  - url: https://github.com/org/contracts.git
    branch: release/v2
    contracts:
      - address: "0xAAaAaAaaAaAaAaaAaAAAAAAAAaaaAaAaAaaAaaAa"
        name: Vault
"""


def test_repositories_mapping_and_hidden_github_token(tmp_path: Path) -> None:
    config = load_config(
        write_config(tmp_path, BASE + REPOSITORIES), environ={"GITHUB_TOKEN": "ghp_secret"}
    )
    (repository,) = config.repositories
    assert repository.owner_repo == ("org", "contracts")
    assert repository.contracts[0].address == "0x" + "aa" * 20
    assert config.github.token is not None
    assert "ghp_secret" not in repr(config) and "ghp_secret" not in config.model_dump_json()
    assert load_config(write_config(tmp_path, BASE + REPOSITORIES), environ={}).github.token is None


@pytest.mark.parametrize(
    "repository",
    [
        "url: http://github.com/org/contracts",
        "url: https://github.com/org",
        "url: https://github.com/org/contracts/tree/main",
        "url: https://token@github.com/org/contracts",
        "url: https://github.com/org/contracts\n    branch: ../../users",
        "url: https://github.com/org/contracts\n    contracts: [{address: '0x12', name: A}]",
    ],
)
def test_invalid_repository_config_is_rejected(tmp_path: Path, repository: str) -> None:
    with pytest.raises(ConfigurationError):
        load_config(write_config(tmp_path, BASE + f"repositories:\n  - {repository}\n"), environ={})
