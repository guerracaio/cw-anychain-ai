import httpx
import pytest
from fastapi.testclient import TestClient

from app.config.models import AppConfig
from app.main import create_app

TX_HASH = "0x" + "ab" * 32


@pytest.fixture
def client():
    config = AppConfig.model_validate(
        {
            "network": {
                "id": "custom-chain",
                "name": "Custom Chain",
                "chain_id": 321,
                "native_currency": "COIN",
            },
            "explorer": {"base_url": "https://explorer.example"},
            "rpc": {"url": "https://rpc.example/private-key"},
        }
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    with TestClient(create_app(config, transport=transport)) as test_client:
        yield test_client


def test_health_and_public_network(client: TestClient) -> None:
    assert client.get("/health").json() == {
        "status": "ok",
        "network": "custom-chain",
        "analysis_available": True,
        "explanation_available": False,
    }
    response = client.get("/api/network")
    assert response.status_code == 200
    assert response.json() == {
        "id": "custom-chain",
        "name": "Custom Chain",
        "chain_id": 321,
        "native_currency": "COIN",
        "native_decimals": 18,
    }
    assert "private-key" not in response.text


@pytest.mark.parametrize("mode", ["developer", "support", "auditor"])
def test_valid_request_returns_partial_result_when_sources_fail(
    client: TestClient, mode: str
) -> None:
    response = client.post("/api/analyze", json={"tx_hash": TX_HASH, "mode": mode})
    assert response.status_code == 200
    assert response.json()["status"] == "unknown"
    assert response.json()["sources"] == []
    assert response.json()["uncertainties"]
    assert response.headers["X-Request-ID"] == response.json()["request_id"]
    assert "private-key" not in response.text


@pytest.mark.parametrize("value", ["", "0x123", "ab" * 32, "0x" + "gg" * 32, TX_HASH + "\n"])
def test_invalid_hash_is_rejected(client: TestClient, value: str) -> None:
    assert client.post("/api/analyze", json={"tx_hash": value}).status_code == 422


def test_client_cannot_select_network(client: TestClient) -> None:
    response = client.post("/api/analyze", json={"tx_hash": TX_HASH, "network": "another-chain"})
    assert response.status_code == 422


def test_unknown_mode_is_rejected(client: TestClient) -> None:
    assert (
        client.post("/api/analyze", json={"tx_hash": TX_HASH, "mode": "invalid"}).status_code == 422
    )


def test_openapi_documents_request_response(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    endpoint = schema["paths"]["/api/analyze"]["post"]
    assert "200" in endpoint["responses"]
    assert "AnalysisResponse" in schema["components"]["schemas"]
