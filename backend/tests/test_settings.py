import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from test_analysis import TX

from app.config.loader import PROJECT_ROOT
from app.config.store import ConfigStore
from app.main import create_app

MAINNET = PROJECT_ROOT / "config" / "ethereum-mainnet.example.yaml"
SEPOLIA = PROJECT_ROOT / "config" / "sepolia.example.yaml"
RPC_SECRET = "https://rpc.example/secret-rpc-key"
ENVIRON = {"RPC_URL": RPC_SECRET, "LLM_PROVIDER": "", "LLM_MODEL": "", "GITHUB_TOKEN": ""}


class Upstream:
    """Explorer, RPC and GitHub stand-ins; records the hosts that were called."""

    def __init__(self, chain_id: int = 1):
        self.chain_id = chain_id
        self.hosts: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.hosts.append(request.url.host)
        if request.method == "POST":
            body = json.loads(request.content)
            result = hex(self.chain_id) if body["method"] == "eth_chainId" else None
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"object": {"sha": "a" * 40, "type": "commit"}})
        if request.url.path.endswith("/api/v2/stats"):
            return httpx.Response(200, json={"total_blocks": "1"})
        return httpx.Response(404)


def make_store(tmp_path: Path, environ=None) -> ConfigStore:
    store = ConfigStore(tmp_path / "local", environ or dict(ENVIRON))
    store.seed([MAINNET, SEPOLIA], MAINNET)
    return store


def client(tmp_path, upstream=None, store=None):
    app = create_app(
        transport=httpx.MockTransport(upstream or Upstream()),
        store=store or make_store(tmp_path),
    )
    return TestClient(app)


def update_body(**overrides):
    body = {
        "network": {
            "name": "Ethereum Mainnet",
            "chain_id": 1,
            "native_currency": "ETH",
            "native_decimals": 18,
        },
        "explorer": {"base_url": "https://eth.blockscout.com"},
        "repositories": [
            {"url": "https://github.com/circlefin/stablecoin-evm", "branch": "master"},
            {"url": "https://github.com/Uniswap/universal-router", "branch": "main"},
        ],
        "llm": {
            "provider": None,
            "model": None,
            "thinking_level": "low",
            "input_price_per_million": None,
            "output_price_per_million": None,
        },
        "secrets": {},
    }
    for key, value in overrides.items():
        body[key] = value
    return body


def test_seed_copies_examples_verbatim_and_activates_app_config(tmp_path):
    store = make_store(tmp_path)
    assert store.ids() == ["ethereum-mainnet", "sepolia"]
    assert store.active() == "ethereum-mainnet"
    text = (tmp_path / "local/profiles/ethereum-mainnet.yaml").read_text(encoding="utf-8")
    assert "${RPC_URL:-}" in text and RPC_SECRET not in text
    # A second seed never overwrites local edits.
    (tmp_path / "local/profiles/sepolia.yaml").write_text("changed", encoding="utf-8")
    store.seed([SEPOLIA], SEPOLIA)
    assert (tmp_path / "local/profiles/sepolia.yaml").read_text(encoding="utf-8") == "changed"


def test_reads_never_expose_secret_values(tmp_path):
    with client(tmp_path) as api:
        assert api.get("/api/network").json()["id"] == "ethereum-mainnet"
        listing = api.get("/api/settings/profiles").json()
        assert [(p["id"], p["active"]) for p in listing] == [
            ("ethereum-mainnet", True),
            ("sepolia", False),
        ]
        response = api.get("/api/settings/profiles/ethereum-mainnet")
    view = response.json()
    assert RPC_SECRET not in response.text and "secret-rpc" not in response.text
    assert view["secrets"]["rpc_url"] == {"configured": True, "source": "env", "env_var": "RPC_URL"}
    assert view["secrets"]["llm_api_key"]["configured"] is False
    assert view["env_bound"]["llm.provider"] == "LLM_PROVIDER"
    assert view["explorer"]["base_url"] == "https://eth.blockscout.com"
    assert view["repositories"][0]["branch"] == "master"


def test_saving_keeps_env_placeholders_for_unchanged_values(tmp_path):
    network = {
        "name": "Mainnet (UI)",
        "chain_id": 1,
        "native_currency": "ETH",
        "native_decimals": 18,
    }
    with client(tmp_path) as api:
        response = api.put(
            "/api/settings/profiles/ethereum-mainnet",
            json=update_body(network=network),
        )
        assert response.status_code == 200, response.text
        # The active profile is applied without a restart.
        assert api.get("/api/network").json()["name"] == "Mainnet (UI)"
    text = (tmp_path / "local/profiles/ethereum-mainnet.yaml").read_text(encoding="utf-8")
    assert "Mainnet (UI)" in text
    assert "${RPC_URL:-}" in text and "${LLM_PROVIDER:-}" in text and "${LLM_MODEL:-}" in text


def test_secret_actions_set_clear_and_defer_to_the_environment(tmp_path):
    url = "/api/settings/profiles/ethereum-mainnet"
    path = tmp_path / "local/profiles/ethereum-mainnet.yaml"
    with client(tmp_path) as api:

        def save(**secrets):
            response = api.put(url, json=update_body(secrets=secrets))
            assert response.status_code == 200, response.text
            assert "sk-test-value" not in response.text and "other-node" not in response.text
            return response.json()["secrets"]

        secrets = save(
            rpc_url={"action": "set", "value": "https://other-node.example/key"},
            llm_api_key={"action": "set", "value": "sk-test-value"},
        )
        assert secrets["rpc_url"]["source"] == "file"
        assert secrets["llm_api_key"] == {"configured": True, "source": "file", "env_var": None}
        assert "sk-test-value" in path.read_text(encoding="utf-8")
        assert save(rpc_url={"action": "clear"})["rpc_url"]["configured"] is False
        secrets = save(rpc_url={"action": "env"}, llm_api_key={"action": "env"})
        assert secrets["rpc_url"] == {"configured": True, "source": "env", "env_var": "RPC_URL"}
        assert "sk-test-value" not in path.read_text(encoding="utf-8")
        # "keep" leaves secrets untouched.
        assert save(rpc_url={"action": "keep"})["rpc_url"]["source"] == "env"


def test_invalid_updates_name_fields_without_echoing_values(tmp_path):
    body = update_body(
        network={"name": "X", "chain_id": 0, "native_currency": "ETH", "native_decimals": 18},
        explorer={"base_url": "https://user:pass@explorer.example"},
        secrets={"rpc_url": {"action": "set", "value": "not a url secret-value"}},
    )
    with client(tmp_path) as api:
        response = api.put("/api/settings/profiles/ethereum-mainnet", json=body)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_config"
    assert {"network.chain_id", "explorer.base_url", "rpc.url"} <= set(error["fields"])
    assert "secret-value" not in response.text and "pass@" not in response.text
    # Nothing was written.
    text = (tmp_path / "local/profiles/ethereum-mainnet.yaml").read_text(encoding="utf-8")
    assert "chain_id: 1" in text


def test_create_copy_activate_and_delete_profiles(tmp_path):
    upstream = Upstream()
    with client(tmp_path, upstream) as api:
        body = update_body(
            network={
                "name": "Rede X",
                "chain_id": 777,
                "native_currency": "XC",
                "native_decimals": 6,
            },
            explorer={"base_url": "https://explorer.x.example"},
            repositories=[],
        )
        created = api.put("/api/settings/profiles/rede-x", json=body)
        assert created.status_code == 200, created.text
        copy = api.put(
            "/api/settings/profiles/rede-y",
            json={**body, "copy_from": "ethereum-mainnet"},
        )
        assert copy.status_code == 200
        assert copy.json()["secrets"]["rpc_url"]["env_var"] == "RPC_URL"
        again = api.put(
            "/api/settings/profiles/rede-x",
            json={**body, "copy_from": "sepolia"},
        )
        assert again.status_code == 409

        assert api.post("/api/settings/active", json={"id": "rede-x"}).status_code == 200
        network = api.get("/api/network").json()
        assert (network["id"], network["chain_id"], network["native_currency"]) == (
            "rede-x",
            777,
            "XC",
        )
        # New analyses use the new explorer.
        upstream.hosts.clear()
        api.post("/api/analyze", json={"tx_hash": TX})
        assert "explorer.x.example" in upstream.hosts and "eth.blockscout.com" not in upstream.hosts

        deleted = api.delete("/api/settings/profiles/rede-x")
        assert deleted.status_code == 409
        assert api.delete("/api/settings/profiles/rede-y").status_code == 200
        assert api.get("/api/settings/profiles/rede-y").status_code == 404
    # The active profile survives a restart.
    state = json.loads((tmp_path / "local/state.json").read_text(encoding="utf-8"))
    assert state == {"active": "rede-x"}


@pytest.mark.parametrize("profile_id", ["Bad_ID", "a..b", "-x"])
def test_profile_ids_cannot_escape_the_profiles_folder(tmp_path, profile_id):
    with client(tmp_path) as api:
        response = api.put(f"/api/settings/profiles/{profile_id}", json=update_body())
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_profile_id"


def test_connection_check_reports_each_dependency(tmp_path):
    with client(tmp_path, Upstream(chain_id=1)) as api:
        checks = {
            c["id"]: (c["ok"], c["code"])
            for c in api.post("/api/settings/profiles/ethereum-mainnet/test").json()["checks"]
        }
        assert checks["explorer"] == (True, "ok")
        assert checks["rpc"] == (True, "ok")
        assert checks["repository.circlefin/stablecoin-evm"] == (True, "ok")
        assert checks["llm"] == (None, "llm_not_configured")
        sepolia = api.post("/api/settings/profiles/sepolia/test").json()["checks"]
    rpc = next(c for c in sepolia if c["id"] == "rpc")
    assert (rpc["ok"], rpc["code"]) == (False, "chain_mismatch")


def test_injected_config_keeps_settings_read_only():
    from test_analysis import config

    with TestClient(create_app(config(), transport=httpx.MockTransport(Upstream()))) as api:
        assert api.get("/api/settings/status").json()["available"] is False
        assert api.get("/api/settings/profiles").status_code == 404
