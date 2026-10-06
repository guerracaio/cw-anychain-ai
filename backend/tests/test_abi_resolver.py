import asyncio

import httpx
from github_mock import FakeGitHub, artifact

from app.blockchain.abi_resolver import AbiMiss, AbiResolver, ResolvedAbi
from app.config.models import RepositoryConfig
from app.explorer.blockscout_client import BlockscoutClient
from app.repositories.github_client import GitHubClient
from app.repositories.resolver import RepositoryService
from app.services.http import JsonHttpClient

PROXY = "0x" + "aa" * 20
IMPLEMENTATION = "0x" + "bb" * 20
ABI = [{"type": "function", "name": "transfer", "inputs": []}]


def explorer(contracts: dict, calls: list | None = None):
    def handler(request):
        if calls is not None:
            calls.append(str(request.url))
        account = request.url.path.rsplit("/", 1)[1]
        if account not in contracts:
            return httpx.Response(404)
        return httpx.Response(200, json=contracts[account])

    return handler


REPOSITORY = {"url": "https://github.com/org/contracts", "branch": "main"}


def resolve(handler, *addresses, github: FakeGitHub | None = None, contracts=()):
    def route(request):
        return github(request) if github and github.handles(request) else handler(request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
            http = JsonHttpClient(client, 1, 4096)
            blockscout = BlockscoutClient(http, "https://x.example", 1, 1)
            repositories = RepositoryService(
                GitHubClient(http, None, 4096),
                [RepositoryConfig.model_validate({**REPOSITORY, "contracts": list(contracts)})]
                if github
                else [],
            )
            resolver = AbiResolver(blockscout, ("explorer", "repository"), repositories)
            try:
                return await asyncio.gather(*(resolver.resolve(a) for a in addresses))
            finally:
                await resolver.aclose()

    return asyncio.run(run())


def test_explorer_abi_has_priority():
    (result,) = resolve(explorer({PROXY: {"is_verified": True, "abi": ABI}}), PROXY)
    assert isinstance(result, ResolvedAbi)
    assert result.source == "explorer"
    assert [(p.address, p.role) for p in result.parts] == [(PROXY, "contract")]


def test_proxy_parts_follow_dispatch_order():
    contracts = {
        PROXY: {"abi": ABI, "implementations": [{"address_hash": IMPLEMENTATION}]},
        IMPLEMENTATION: {"abi": ABI},
    }
    (result,) = resolve(explorer(contracts), PROXY)
    assert isinstance(result, ResolvedAbi)
    assert [(p.address, p.role) for p in result.parts] == [
        (PROXY, "contract"),
        (IMPLEMENTATION, "implementation"),
    ]


def test_unverified_proxy_still_uses_verified_implementation():
    contracts = {
        PROXY: {"is_verified": False, "implementations": [{"address_hash": IMPLEMENTATION}]},
        IMPLEMENTATION: {"abi": ABI},
    }
    (result,) = resolve(explorer(contracts), PROXY)
    assert isinstance(result, ResolvedAbi)
    assert [p.role for p in result.parts] == ["implementation"]
    assert result.metadata and result.metadata.implementations == [IMPLEMENTATION]


def test_missing_implementation_abi_is_reported_as_issue():
    contracts = {PROXY: {"abi": ABI, "implementations": [{"address_hash": IMPLEMENTATION}]}}
    (result,) = resolve(explorer(contracts), PROXY)
    assert isinstance(result, ResolvedAbi)
    assert result.issues == ["implementation_abi:not_found"]


def test_missing_abi_reports_each_strategy():
    (result,) = resolve(explorer({PROXY: {"is_verified": False}}), PROXY)
    assert isinstance(result, AbiMiss)
    assert result.reasons == {
        "explorer": "abi_unavailable",
        "repository": "repository_not_configured",
    }
    (configured,) = resolve(explorer({}), PROXY, github=FakeGitHub({}))
    assert configured.reasons == {
        "explorer": "not_found",
        "repository": "repository_contract_unknown",
    }


def test_repository_artifact_is_fallback_for_named_unverified_contract():
    github = FakeGitHub(
        {
            "test/out/Token.sol/Token.json": artifact([{"type": "event", "name": "Mock"}]),
            "out/Token.sol/Token.json": artifact(ABI),
            "src/Token.sol": "contract Token {}",
        }
    )
    (result,) = resolve(explorer({PROXY: {"name": "Token"}}), PROXY, github=github)
    assert isinstance(result, ResolvedAbi) and result.source == "repository"
    part = result.parts[0]
    assert part.abi == ABI and part.name == "Token"
    assert part.artifact and part.artifact.path == "out/Token.sol/Token.json"


def test_configured_mapping_names_contracts_unknown_to_explorer():
    github = FakeGitHub({"artifacts/contracts/Vault.sol/Vault.json": artifact(ABI)})
    mapping = [{"address": PROXY.upper().replace("0X", "0x"), "name": "Vault"}]
    (result,) = resolve(explorer({}), PROXY, github=github, contracts=mapping)
    assert isinstance(result, ResolvedAbi)
    assert result.parts[0].name == "Vault"


def test_repository_without_artifact_reports_reason():
    github = FakeGitHub({"src/Token.sol": "contract Token {}"})
    (result,) = resolve(explorer({PROXY: {"name": "Token"}}), PROXY, github=github)
    assert isinstance(result, AbiMiss)
    assert result.reasons["repository"] == "repository_artifact_not_found"


def test_repeated_addresses_are_fetched_once():
    calls: list = []
    contracts = {
        PROXY: {"abi": ABI, "implementations": [{"address_hash": IMPLEMENTATION}]},
        IMPLEMENTATION: {"abi": ABI},
    }
    resolve(explorer(contracts, calls), PROXY, PROXY, IMPLEMENTATION)
    assert len(calls) == 2
