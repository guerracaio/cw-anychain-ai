"""Attach selective contract source excerpts (verified explorer source, configured repositories)."""

from app.blockchain.abi_resolver import AbiMiss, ResolvedAbi
from app.domain.analysis import AnalysisResponse, Evidence, SourceExcerpt
from app.explorer.blockscout_client import BlockscoutClient
from app.repositories.github_client import RepositoryRef
from app.repositories.resolver import (
    ExplorerSources,
    LocateResult,
    RepositoryService,
    locate,
)

ISSUES = {
    "access_denied": "acesso negado ou limite de requisições do GitHub; configure GITHUB_TOKEN",
    "not_found": "repositório, branch ou arquivo não encontrado",
    "file_limit": "limite de arquivos por contrato atingido ao seguir a herança",
    "tree_truncated": "árvore do repositório truncada pelo GitHub",
    "temporarily_unavailable": "GitHub temporariamente indisponível",
    "timeout": "tempo limite excedido",
    "payload_limit": "arquivo excedeu max_contract_payload_bytes",
}


def _issue_texts(issues: list[str]) -> list[str]:
    """Group "<where>:<code>" issues by code so one outage yields one message."""
    grouped: dict[str, list[str]] = {}
    for issue in dict.fromkeys(issues):
        where, _, code = issue.rpartition(":")
        grouped.setdefault(code, [])
        if where:
            grouped[code].append(where)
    texts = []
    for code, places in grouped.items():
        text = ISSUES.get(code, code)
        if places:
            shown = ", ".join(places[:3]) + (
                f" e mais {len(places) - 3}" if len(places) > 3 else ""
            )
            text = f"{text} ({shown})"
        texts.append(text)
    return texts


async def ground(
    response: AnalysisResponse,
    resolutions: dict[str, ResolvedAbi | AbiMiss | None],
    explorer: BlockscoutClient,
    repositories: RepositoryService | None,
    budget: int,
) -> None:
    """Explorer-verified source first (matches deployed bytecode), then configured repositories."""
    tx = response.transaction
    uncertainties = response.uncertainties
    decoded = tx.decoded_input
    name: str | None = None
    address: str | None = None
    function: str | None = None
    sources: dict[str, str] = {}
    if decoded:
        resolution = resolutions.get(decoded.contract)
        part = next(
            (
                p
                for p in (resolution.parts if isinstance(resolution, ResolvedAbi) else [])
                if p.address == decoded.abi_address
            ),
            None,
        )
        address, function = decoded.abi_address, decoded.function
        if part and part.metadata:
            name, sources = part.metadata.name, part.metadata.sources
    elif tx.recipient and tx.selector:
        address = tx.recipient
    if address is None:
        return
    preferred: RepositoryRef | None = None
    mapped = repositories.mapped(address) if repositories else None
    if mapped is None and repositories and decoded:
        mapped = repositories.mapped(decoded.contract)
    if mapped:
        preferred, mapped_name = mapped
        name = name or mapped_name
    if not name:
        uncertainties.append(
            f"Nome do contrato {address} desconhecido: o código-fonte não pôde ser localizado. "
            "É necessário código verificado no explorer ou um mapeamento em "
            "repositories[].contracts."
        )
        return
    use_repository = repositories is not None and repositories.configured
    if not sources and not use_repository:
        uncertainties.append(
            f"Sem código-fonte verificado de {name} no explorer e sem repositório configurado: "
            "regras de negócio e verificações da função não foram consultadas."
        )
        return
    share = budget // 2 if sources and use_repository else budget
    label = f"{name}.{function}" if function else name

    def report_missing(result: LocateResult, origin: str) -> None:
        for missing in result.missing:
            uncertainties.append(f"{missing} não localizado {origin}.")

    if sources:
        result = await locate(ExplorerSources(sources), name, function, share)
        evidence_id = f"explorer.source.{address}"
        if result.excerpts:
            response.sources.append(
                Evidence(
                    id=evidence_id,
                    source_type="blockscout",
                    source_url=explorer.contract_api_url(address),
                    description=f"Blockscout: código-fonte verificado de {name}",
                    payload={"address": address, "contract": name, "files": result.files},
                    confidence="observed",
                )
            )
        response.contract_context.extend(
            SourceExcerpt(
                origin="explorer",
                contract=excerpt.contract,
                address=address,
                symbol=excerpt.symbol,
                kind=excerpt.kind,
                reason=excerpt.reason,
                path=excerpt.path,
                start_line=excerpt.start_line,
                end_line=excerpt.end_line,
                code=excerpt.code,
                truncated=excerpt.truncated,
                url=explorer.get_explorer_contract_url(address),
                evidence_ids=[evidence_id],
            )
            for excerpt in result.excerpts
        )
        report_missing(result, "no código verificado do explorer")

    if not use_repository:
        return
    assert repositories is not None
    issues: list[str] = []
    provider = await repositories.sources_for(name, preferred, issues)
    if provider is None:
        uncertainties.append(
            f"{name} não encontrado nos repositórios configurados (arquivo {name}.sol)."
        )
    else:
        result = await locate(provider, name, function, share)
        issues.extend(provider.issues)
        repo, commit = provider.repo, provider.commit
        cited: set[str] = set()
        for excerpt in result.excerpts:
            evidence_id = f"repository.{repo.label}:{excerpt.path}"
            if evidence_id not in cited:
                cited.add(evidence_id)
                response.sources.append(
                    Evidence(
                        id=evidence_id,
                        source_type="repository",
                        source_url=repo.file_url(commit, excerpt.path),
                        description=f"Repositório {repo.label}: {excerpt.path}",
                        payload={
                            "repository": repo.web_url,
                            "branch": repo.branch,
                            "commit": commit,
                            "path": excerpt.path,
                        },
                        confidence="observed",
                    )
                )
            response.contract_context.append(
                SourceExcerpt(
                    origin="repository",
                    contract=excerpt.contract,
                    address=address,
                    symbol=excerpt.symbol,
                    kind=excerpt.kind,
                    reason=excerpt.reason,
                    path=excerpt.path,
                    start_line=excerpt.start_line,
                    end_line=excerpt.end_line,
                    code=excerpt.code,
                    truncated=excerpt.truncated,
                    repository=repo.web_url,
                    commit=commit,
                    url=repo.blob_url(commit, excerpt.path, excerpt.start_line, excerpt.end_line),
                    evidence_ids=[evidence_id],
                )
            )
        if result.excerpts:
            uncertainties.append(
                f"Trechos de {repo.label} vêm do commit {commit[:12]} da branch {repo.branch}, "
                f"associados por nome a {address}. O repositório pode diferir do código "
                "implantado; o código verificado do explorer prevalece quando divergirem."
            )
        report_missing(result, f"em {repo.label}")
    for text in _issue_texts(issues):
        uncertainties.append(f"Repositórios, consulta de {label}: {text}.")
