"""Selective source retrieval: locate a contract, then excerpt only the relevant symbols.

Sources are untrusted evidence. Nothing here compiles or executes repository content.
"""

import asyncio
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Literal, Protocol

from app.config.models import RepositoryConfig
from app.repositories.github_client import GitHubClient, RepositoryRef, Tree
from app.repositories.solidity import Declaration, excerpt_range, mask, parse
from app.services.http import UpstreamError, parse_json

MAX_FILES_PER_CONTRACT = 16
PARALLEL_FILES = 4
MAX_OVERLOADS = 2
COMMIT_TTL_SECONDS = 300
FILE_CACHE_SIZE = 256
DEPRIORITIZED = ("test", "tests", "mock", "mocks", "script", "scripts", "lib", "node_modules")
ARTIFACT_DIRS = {"out", "artifacts", "abi", "abis", "build", "deployments"}


@dataclass
class SourceFile:
    path: str
    text: str
    declarations: list[Declaration]


@dataclass
class Excerpt:
    contract: str
    symbol: str
    kind: str
    path: str
    start_line: int
    end_line: int
    code: str
    truncated: bool
    reason: str


@dataclass
class LocateResult:
    excerpts: list[Excerpt] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


class SourceProvider(Protocol):
    origin: Literal["explorer", "repository"]

    async def find(self, contract: str) -> SourceFile | None: ...


class ExplorerSources:
    """Verified source files already returned by the explorer: no extra requests."""

    origin: Literal["explorer", "repository"] = "explorer"

    def __init__(self, sources: dict[str, str]):
        self.files = [SourceFile(path, text, parse(text)) for path, text in sources.items()]

    async def find(self, contract: str) -> SourceFile | None:
        return next(
            (
                file
                for file in self.files
                if any(d.kind == "contract" and d.name == contract for d in file.declarations)
            ),
            None,
        )


def _rank(path: str) -> tuple[int, int]:
    parts = path.lower().split("/")
    return (int(any(part in DEPRIORITIZED for part in parts[:-1])), len(parts))


class RepositorySources:
    origin: Literal["explorer", "repository"] = "repository"

    def __init__(self, service: "RepositoryService", repo: RepositoryRef, commit: str, tree: Tree):
        self.service = service
        self.repo = repo
        self.commit = commit
        self.tree = tree
        self.fetched = 0
        self.blocked = False
        self.issues: list[str] = []

    def candidates(self, contract: str) -> list[str]:
        # Conventional layout: one main contract per file named after it.
        return sorted(
            (e.path for e in self.tree.entries if e.path.rsplit("/", 1)[-1] == f"{contract}.sol"),
            key=_rank,
        )

    async def find(self, contract: str) -> SourceFile | None:
        for path in self.candidates(contract)[:2]:
            if self.blocked:
                return None
            if self.fetched >= MAX_FILES_PER_CONTRACT:
                self.issues.append("file_limit")
                return None
            self.fetched += 1
            try:
                text = await self.service.file(self.repo, self.commit, path)
            except UpstreamError as exc:
                self.issues.append(f"{path}:{exc.code}")
                # Denied/rate-limited: further requests would fail the same way.
                self.blocked = exc.code in ("access_denied", "temporarily_unavailable")
                continue
            file = SourceFile(path, text, parse(text))
            if any(d.kind == "contract" and d.name == contract for d in file.declarations):
                return file
        return None


async def locate(
    provider: SourceProvider,
    contract: str,
    function: str | None,
    budget: int,
    kinds: tuple[str, ...] = ("function",),
    reason: str = "called_function",
    header: bool = True,
) -> LocateResult:
    """Excerpt `function` (or the contract header) following inheritance, most derived first.

    `kinds` widens the target lookup (e.g. modifiers or errors requested by the agent).
    """
    result = LocateResult()
    hierarchy: list[tuple[SourceFile, Declaration]] = []
    queue = [contract]
    seen: set[str] = set()

    async def extend() -> bool:
        """Load the next breadth-first batch of bases; False once the hierarchy is exhausted."""
        batch: list[str] = []
        while queue and len(batch) < PARALLEL_FILES:
            name = queue.pop(0)
            if name not in seen:
                seen.add(name)
                batch.append(name)
        if not batch:
            return False
        files = await asyncio.gather(*(provider.find(name) for name in batch))
        for name, file in zip(batch, files, strict=True):
            if file is None:
                continue
            declaration = next(
                d for d in file.declarations if d.kind == "contract" and d.name == name
            )
            hierarchy.append((file, declaration))
            if file.path not in result.files:
                result.files.append(file.path)
            # Breadth-first from the most derived contract approximates C3 linearization;
            # the right-most base is the most derived one.
            queue.extend(reversed(declaration.bases))
        return True

    def matches(kinds: tuple[str, ...], name: str, internal_only: bool = False):
        for file, owner in hierarchy:
            found = [
                d
                for d in file.declarations
                if d.kind in kinds
                and d.name == name
                and (
                    d.container == owner.name
                    # Errors and events may be declared at file level.
                    or (d.kind in ("error", "event") and d.container is None)
                )
                and (not internal_only or d.internal)
            ]
            implemented = [d for d in found if d.body_start is not None] or found
            if implemented:
                return file, owner, implemented[:MAX_OVERLOADS]
        return None

    async def search(kinds: tuple[str, ...], name: str, fetch: bool, internal_only=False):
        while True:
            found = matches(kinds, name, internal_only)
            if found or not fetch or not await extend():
                return found

    remaining = budget

    def add(file: SourceFile, owner: Declaration, declaration: Declaration, reason: str) -> None:
        nonlocal remaining
        start, end = excerpt_range(file.text, declaration)
        code = "\n".join(file.text.splitlines()[start - 1 : end])
        if remaining <= 0 or any(
            e.path == file.path and e.start_line == start for e in result.excerpts
        ):
            return
        truncated = len(code) > remaining
        code = code[:remaining]
        remaining -= len(code)
        symbol = (
            owner.name if declaration.kind == "contract" else f"{owner.name}.{declaration.name}"
        )
        result.excerpts.append(
            Excerpt(
                owner.name,
                symbol,
                declaration.kind,
                file.path,
                start,
                end,
                code,
                truncated,
                reason,
            )
        )

    await extend()
    if not hierarchy:
        result.missing.append(contract)
        return result
    file, target = hierarchy[0]
    if header or function is None:
        add(file, target, target, "contract_header")
    if function is None:
        return result
    found = await search(kinds, function, fetch=True)
    if not found:
        result.missing.append(f"{contract}.{function}")
        return result
    file, owner, declarations = found
    for declaration in declarations:
        add(file, owner, declaration, reason)
    related: list[tuple[str, str, bool, bool]] = []
    for declaration in declarations:
        related.extend(("modifier", name, True, False) for name in declaration.modifiers)
        related.extend(("error", name, False, False) for name in declaration.errors)
        related.extend(("function", name, False, True) for name in declaration.calls)
    for kind, name, fetch, internal_only in dict.fromkeys(related):
        found = await search((kind,), name, fetch, internal_only)
        if found:
            file, owner, items = found
            for item in items:
                add(file, owner, item, f"related_{kind}")
        elif kind == "modifier":
            result.missing.append(f"modifier {name}")
    return result


@dataclass
class ArtifactAbi:
    repo: RepositoryRef
    commit: str
    path: str
    abi: list[dict]


class RepositoryService:
    """Configured repositories only; caches commits briefly and immutable content by commit."""

    def __init__(self, client: GitHubClient | None, repositories: list[RepositoryConfig]):
        self.client = client
        self.repositories = [
            (
                RepositoryRef(
                    # Canonical web URL: no trailing slash or ".git", so permalinks resolve.
                    f"https://{config.url.host}/{'/'.join(config.owner_repo)}",
                    *config.owner_repo,
                    config.branch,
                ),
                config,
            )
            for config in repositories
        ]
        self._commits: dict[RepositoryRef, tuple[float, str]] = {}
        self._trees: dict[tuple[RepositoryRef, str], Tree] = {}
        self._files: OrderedDict[tuple[RepositoryRef, str, str], str] = OrderedDict()

    @property
    def configured(self) -> bool:
        return bool(self.repositories) and self.client is not None

    def mapped(self, address: str) -> tuple[RepositoryRef, str] | None:
        for repo, config in self.repositories:
            for contract in config.contracts:
                if contract.address == address:
                    return repo, contract.name
        return None

    async def snapshot(self, repo: RepositoryRef) -> tuple[str, Tree]:
        assert self.client is not None
        cached = self._commits.get(repo)
        if cached and time.monotonic() - cached[0] < COMMIT_TTL_SECONDS:
            commit = cached[1]
        else:
            commit = await self.client.get_branch_commit(repo)
            self._commits[repo] = (time.monotonic(), commit)
        if (repo, commit) not in self._trees:
            self._trees[(repo, commit)] = await self.client.get_tree(repo, commit)
        return commit, self._trees[(repo, commit)]

    async def file(self, repo: RepositoryRef, commit: str, path: str) -> str:
        assert self.client is not None
        key = (repo, commit, path)
        if key in self._files:
            self._files.move_to_end(key)
            return self._files[key]
        text = await self.client.get_file(repo, commit, path)
        self._files[key] = text
        if len(self._files) > FILE_CACHE_SIZE:
            self._files.popitem(last=False)
        return text

    def _ordered(self, preferred: RepositoryRef | None) -> list[RepositoryRef]:
        repos = [repo for repo, _ in self.repositories]
        return sorted(repos, key=lambda repo: repo != preferred)

    async def sources_for(
        self, contract: str, preferred: RepositoryRef | None, issues: list[str]
    ) -> RepositorySources | None:
        """First configured repository with a file named after the contract."""
        for repo in self._ordered(preferred):
            try:
                commit, tree = await self.snapshot(repo)
            except UpstreamError as exc:
                issues.append(f"{repo.label}:{exc.code}")
                continue
            sources = RepositorySources(self, repo, commit, tree)
            if tree.truncated:
                issues.append(f"{repo.label}:tree_truncated")
            if sources.candidates(contract):
                return sources
        return None

    async def artifact_abi(
        self, contract: str, preferred: RepositoryRef | None
    ) -> ArtifactAbi | str:
        """Compiled ABI committed to a repository (Foundry/Hardhat layouts), if any."""
        reason = "repository_artifact_not_found"
        for repo in self._ordered(preferred):
            try:
                commit, tree = await self.snapshot(repo)
            except UpstreamError as exc:
                reason = exc.code
                continue
            paths = sorted(
                (
                    entry.path
                    for entry in tree.entries
                    if entry.path.rsplit("/", 1)[-1] == f"{contract}.json"
                    and ARTIFACT_DIRS & set(entry.path.lower().split("/")[:-1])
                ),
                key=_rank,
            )
            for path in paths[:2]:
                try:
                    data = parse_json(bytearray((await self.file(repo, commit, path)).encode()))
                except (UpstreamError, ValueError):
                    reason = "invalid_abi"
                    continue
                abi = data.get("abi") if isinstance(data, dict) else data
                if isinstance(abi, list) and abi and all(isinstance(i, dict) for i in abi):
                    return ArtifactAbi(repo, commit, path, abi)
                reason = "invalid_abi"
        return reason


MAX_REVERT_SITES = 3


def find_revert_sites(
    sources: dict[str, str], *, error: str | None, message: str | None, budget: int
) -> list[Excerpt]:
    """Excerpt the declaration of a custom error and the functions that can raise it.

    `message` (from Error(string)) is matched as a literal string in the raw text; custom
    errors are matched in code with comments and strings masked out.
    """
    needles: list[re.Pattern[str]] = []
    if error:
        name = re.escape(error)
        needles.append(re.compile(rf"\brevert\s+{name}\s*\(|,\s*{name}\s*\("))
    excerpts: list[Excerpt] = []
    remaining = budget

    def add(file: SourceFile, declaration: Declaration, reason: str) -> None:
        nonlocal remaining
        start, end = excerpt_range(file.text, declaration)
        if remaining <= 0 or any(e.path == file.path and e.start_line == start for e in excerpts):
            return
        code = "\n".join(file.text.splitlines()[start - 1 : end])
        truncated = len(code) > remaining
        code = code[:remaining]
        remaining -= len(code)
        owner = declaration.container or file.path.rsplit("/", 1)[-1]
        symbol = owner if declaration.kind == "contract" else f"{owner}.{declaration.name}"
        excerpts.append(
            Excerpt(owner, symbol, declaration.kind, file.path, start, end, code, truncated, reason)
        )

    sites = 0
    for path, text in sources.items():
        file = SourceFile(path, text, parse(text))
        masked = mask(text)
        offsets: list[int] = []
        for pattern in needles:
            offsets.extend(found.start() for found in pattern.finditer(masked))
        if message:
            literal = re.compile(r"([\"'])" + re.escape(message) + r"\1")
            # Comments are blanked but string literals kept, so only code matches.
            offsets.extend(found.start() for found in literal.finditer(mask(text, strings=False)))
        if error:
            for declaration in file.declarations:
                if declaration.kind == "error" and declaration.name == error:
                    add(file, declaration, "error_declaration")
        for offset in sorted(offsets):
            if sites >= MAX_REVERT_SITES:
                break
            enclosing = [
                d
                for d in file.declarations
                if d.kind in ("function", "modifier") and d.start <= offset < d.end
            ]
            if enclosing:
                add(file, min(enclosing, key=lambda d: d.end - d.start), "revert_site")
                sites += 1
    return excerpts
