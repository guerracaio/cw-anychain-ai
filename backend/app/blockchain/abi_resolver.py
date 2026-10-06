import asyncio
from dataclasses import dataclass, field
from typing import Literal

from app.explorer.blockscout_client import BlockscoutClient, ContractMetadata
from app.repositories.resolver import ArtifactAbi, RepositoryService
from app.services.http import UpstreamError

MAX_IMPLEMENTATIONS = 2
MAX_CONCURRENT_REQUESTS = 4


@dataclass
class AbiPart:
    """One ABI document and the contract it belongs to (proxy or implementation)."""

    address: str
    abi: list[dict]
    role: Literal["contract", "implementation"]
    # Contract name and verified source files, when the explorer provides them.
    metadata: ContractMetadata | None = None
    # Set when the ABI came from a compiled artifact in a configured repository.
    artifact: ArtifactAbi | None = None

    @property
    def name(self) -> str | None:
        return self.metadata.name if self.metadata else None


@dataclass
class ResolvedAbi:
    address: str
    source: Literal["explorer", "repository"]
    # Ordered by EVM dispatch: a proxy's own functions shadow its implementation.
    parts: list[AbiPart]
    metadata: ContractMetadata | None = None
    issues: list[str] = field(default_factory=list)


@dataclass
class AbiMiss:
    address: str
    # Strategy name -> application-owned reason code.
    reasons: dict[str, str]


class AbiResolver:
    """Resolves ABIs by the configured priority. One instance serves one analysis request."""

    def __init__(
        self,
        explorer: BlockscoutClient,
        strategies: tuple[str, ...],
        repositories: RepositoryService | None,
    ):
        self.explorer = explorer
        self.strategies = strategies
        self.repositories = repositories
        self._cache: dict[str, asyncio.Future[ResolvedAbi | AbiMiss]] = {}
        self._contracts: dict[str, asyncio.Future[ContractMetadata]] = {}
        self._limit = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    async def resolve(self, address: str) -> ResolvedAbi | AbiMiss:
        if address not in self._cache:
            self._cache[address] = asyncio.ensure_future(self._resolve(address))
        return await asyncio.shield(self._cache[address])

    async def aclose(self) -> None:
        tasks = [*self._cache.values(), *self._contracts.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _resolve(self, address: str) -> ResolvedAbi | AbiMiss:
        reasons: dict[str, str] = {}
        for strategy in self.strategies:
            if strategy == "explorer":
                result = await self._from_explorer(address)
                if isinstance(result, ResolvedAbi):
                    return result
                reasons[strategy] = result
            elif strategy == "repository":
                result = await self._from_repository(address)
                if isinstance(result, ResolvedAbi):
                    return result
                reasons[strategy] = result
        return AbiMiss(address, reasons)

    async def _from_repository(self, address: str) -> ResolvedAbi | str:
        if self.repositories is None or not self.repositories.configured:
            return "repository_not_configured"
        mapped = self.repositories.mapped(address)
        preferred, name = mapped if mapped else (None, None)
        metadata = None
        if address in self._contracts:
            try:
                metadata = await asyncio.shield(self._contracts[address])
            except UpstreamError:
                pass
        name = name or (metadata.name if metadata else None)
        if not name:
            # Without a contract name there is no reliable way to pick a file.
            return "repository_contract_unknown"
        artifact = await self.repositories.artifact_abi(name, preferred)
        if isinstance(artifact, str):
            return artifact
        metadata = (metadata or ContractMetadata(address=address)).model_copy(update={"name": name})
        part = AbiPart(address, artifact.abi, "contract", metadata, artifact)
        return ResolvedAbi(address, "repository", [part], metadata)

    async def _contract(self, address: str) -> ContractMetadata:
        if address not in self._contracts:

            async def fetch() -> ContractMetadata:
                async with self._limit:
                    return await self.explorer.get_contract(address)

            self._contracts[address] = asyncio.ensure_future(fetch())
        return await asyncio.shield(self._contracts[address])

    async def _from_explorer(self, address: str) -> ResolvedAbi | str:
        try:
            metadata = await self._contract(address)
        except UpstreamError as exc:
            return exc.code
        parts = [AbiPart(address, metadata.abi, "contract", metadata)] if metadata.abi else []
        issues = []
        if len(metadata.implementations) > MAX_IMPLEMENTATIONS:
            issues.append("implementation_limit")
        for implementation in metadata.implementations[:MAX_IMPLEMENTATIONS]:
            try:
                target = await self._contract(implementation)
            except UpstreamError as exc:
                issues.append(f"implementation_abi:{exc.code}")
                continue
            if target.abi:
                parts.append(AbiPart(implementation, target.abi, "implementation", target))
            else:
                issues.append("implementation_abi:abi_unavailable")
        if not parts:
            return "abi_unavailable"
        return ResolvedAbi(address, "explorer", parts, metadata, issues)
