import asyncio
import logging
import time
from collections.abc import Awaitable

from app.agent.runner import run_agent
from app.agent.tools import AgentTools
from app.blockchain.abi_resolver import AbiMiss, AbiPart, AbiResolver, ResolvedAbi
from app.blockchain.decoder import Decoded, DecodeMiss, decode_calldata, decode_log
from app.blockchain.normalize import (
    merge_transaction,
    normalize_call,
    normalize_logs,
    normalize_transfer,
)
from app.blockchain.rpc_client import RpcClient
from app.blockchain.values import hex_data
from app.config.models import AppConfig
from app.domain.analysis import AnalysisMode, AnalysisResponse, Evidence, Finding
from app.domain.transaction import DecodedCall, DecodedEvent, NestedCall
from app.explorer.blockscout_client import BlockscoutClient, Collection
from app.llm.base import LLMError, LLMProvider
from app.repositories.resolver import RepositoryService
from app.services.diagnostics import diagnose
from app.services.grounding import ground
from app.services.http import UpstreamError, request_id
from app.services.safety import flag_untrusted_text, prune_dangling_citations

logger = logging.getLogger("anychain.analysis")
REASONS = {
    "not_found": "não encontrado no provedor",
    "timeout": "tempo limite excedido",
    "connection_failed": "falha de conexão",
    "temporarily_unavailable": "limite de acesso ou indisponibilidade temporária",
    "payload_limit": "resposta excedeu o limite de tamanho",
    "chain_mismatch": "RPC aponta para outra rede e foi descartado",
    "rpc_error": "RPC recusou a consulta",
    "transaction_mismatch": "resposta não corresponde ao hash solicitado",
    "invalid_contract": "resposta de contrato em formato inválido",
    "abi_unavailable": "ABI não verificada ou não publicada no explorer",
    "invalid_abi": "ABI retornada em formato inválido",
    "repository_not_configured": "nenhum repositório configurado para esta rede",
    "repository_contract_unknown": "nome do contrato desconhecido para buscar no repositório",
    "repository_artifact_not_found": "nenhum artefato compilado com a ABI nos repositórios",
    "access_denied": "acesso negado ou limite de requisições (configure GITHUB_TOKEN)",
}
DECODE_REASONS = {
    "no_selector": "calldata sem seletor de 4 bytes",
    "selector_not_in_abi": "o seletor não consta na ABI obtida",
    "ambiguous_selector": "o seletor corresponde a mais de uma assinatura na ABI",
    "event_not_in_abi": "o tópico do evento não consta na ABI obtida",
    "anonymous_or_no_topics": "log sem tópicos (evento anônimo ou bruto)",
    "decode_failed": "dados incompatíveis com a ABI",
}
TX_FIELDS = {
    "hash",
    "transactionHash",
    "from",
    "to",
    "value",
    "input",
    "raw_input",
    "status",
    "result",
    "block_number",
    "blockNumber",
    "block_hash",
    "blockHash",
    "created_contract",
    "contractAddress",
    "gas",
    "gas_limit",
    "gas_used",
    "gasUsed",
    "gas_price",
    "gasPrice",
    "effectiveGasPrice",
    "transactionIndex",
    "revert_reason",
}


def compact_transaction(data: dict) -> dict:
    result = {key: value for key, value in data.items() if key in TX_FIELDS}
    for key in ("from", "to", "created_contract"):
        if isinstance(result.get(key), dict):
            result[key] = {"hash": result[key].get("hash")}
    return result


class AnalysisService:
    def __init__(
        self,
        config: AppConfig,
        explorer: BlockscoutClient,
        rpc: RpcClient | None,
        repositories: RepositoryService | None = None,
        llm: LLMProvider | None = None,
        llm_issue: str | None = "llm_not_configured",
    ):
        self.config = config
        self.explorer = explorer
        self.rpc = rpc
        self.repositories = repositories
        self.llm = llm
        self.llm_issue = None if llm else llm_issue

    async def analyze(
        self, tx_hash: str, request_id: str, mode: AnalysisMode = "developer"
    ) -> AnalysisResponse:
        started = time.monotonic()
        tx_hash = tx_hash.lower()
        results: dict[str, object] = {}
        errors: dict[str, str] = {}

        async def fetch(name: str, operation: Awaitable) -> None:
            try:
                results[name] = await operation
            except UpstreamError as exc:
                errors[name] = exc.code
            except asyncio.CancelledError:
                errors[name] = "timeout"
                raise

        async def rpc_evidence() -> None:
            assert self.rpc is not None
            try:
                chain_id = await self.rpc.get_chain_id()
                if chain_id != self.config.network.chain_id:
                    raise UpstreamError("chain_mismatch")
                results["rpc.chain"] = chain_id
            except UpstreamError as exc:
                errors["rpc.chain"] = exc.code
                return
            except asyncio.CancelledError:
                errors["rpc.chain"] = "timeout"
                raise
            async with asyncio.TaskGroup() as group:
                group.create_task(fetch("rpc.transaction", self.rpc.get_transaction(tx_hash)))
                group.create_task(fetch("rpc.receipt", self.rpc.get_transaction_receipt(tx_hash)))

        tasks = [
            asyncio.create_task(
                fetch("explorer.transaction", self.explorer.get_transaction(tx_hash))
            ),
            asyncio.create_task(
                fetch("explorer.logs", self.explorer.get_transaction_logs(tx_hash))
            ),
            asyncio.create_task(
                fetch("explorer.transfers", self.explorer.get_token_transfers(tx_hash))
            ),
            asyncio.create_task(
                fetch("explorer.calls", self.explorer.get_internal_transactions(tx_hash))
            ),
        ]
        if self.rpc:
            tasks.append(asyncio.create_task(rpc_evidence()))
        try:
            _, pending = await asyncio.wait(
                tasks, timeout=self.config.analysis.collection_timeout_seconds
            )
            for task in pending:
                task.cancel()
            # Retrieve exceptions from completed tasks, excluding deadline cancellations.
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            for outcome in outcomes:
                if isinstance(outcome, Exception):
                    raise outcome
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        response = self._build(tx_hash, request_id, results, errors)
        remaining = self.config.analysis.collection_timeout_seconds - (time.monotonic() - started)
        resolutions, skipped = await self._resolve_abis(response, remaining)
        self._decode(response, resolutions, skipped)
        remaining = self.config.analysis.collection_timeout_seconds - (time.monotonic() - started)
        try:
            if remaining <= 0:
                raise TimeoutError
            await asyncio.wait_for(
                ground(
                    response,
                    resolutions,
                    self.explorer,
                    self.repositories,
                    self.config.analysis.max_repository_excerpt_chars,
                ),
                timeout=remaining,
            )
        except TimeoutError:
            response.uncertainties.append(
                "Tempo limite da análise excedido antes de concluir a busca de código-fonte; "
                "o contexto de contrato pode estar incompleto."
            )
        if response.status == "failed":
            remaining = self.config.analysis.collection_timeout_seconds - (
                time.monotonic() - started
            )
            explorer_tx = results.get("explorer.transaction")
            try:
                async with asyncio.timeout(max(remaining, 1)):
                    await diagnose(
                        response,
                        explorer_tx if isinstance(explorer_tx, dict) else None,
                        resolutions,
                        self.rpc if "rpc.chain" in results else None,
                        self.explorer,
                        self.config.analysis.max_repository_excerpt_chars // 2,
                    )
            except TimeoutError:
                response.uncertainties.append(
                    "Tempo limite excedido durante o diagnóstico da falha; resultado parcial."
                )
        response.uncertainties = list(dict.fromkeys(response.uncertainties))
        response.mode = mode
        flagged = flag_untrusted_text(response)
        await self.explain(response, mode)
        dangling = prune_dangling_citations(response)
        usage = response.explanation.usage if response.explanation else None
        # Graceful-degradation reasons are safe application codes, never provider messages.
        degraded = [f"{name}:{code}" for name, code in sorted(errors.items())]
        if response.diagnosis.replay == "unavailable":
            degraded.append("rpc.replay:unavailable")
        logger.info(
            "request_id=%s network=%s tx_hash=%s mode=%s status=%s duration_ms=%d "
            "evidence_count=%d uncertainties=%d degraded=%s flagged_inputs=%d "
            "dangling_citations=%d explanation=%s llm_steps=%s llm_cost_usd=%s",
            request_id,
            self.config.network.id,
            tx_hash,
            mode,
            response.status,
            (time.monotonic() - started) * 1000,
            len(response.sources),
            len(response.uncertainties),
            ",".join(degraded) or "-",
            flagged,
            dangling,
            "ok" if response.explanation else response.explanation_issue,
            usage.steps if usage else "-",
            usage.estimated_cost_usd if usage and usage.estimated_cost_usd is not None else "-",
        )
        return response

    async def explain(self, response: AnalysisResponse, mode: AnalysisMode) -> None:
        """LLM interpretation on top of the evidence; failures never hide the evidence."""
        if self.llm is None:
            response.explanation_issue = self.llm_issue
            return
        if response.transaction.sender is None and response.status == "unknown":
            # Nothing reliable to explain; avoid spending tokens on an empty package.
            response.explanation_issue = "insufficient_evidence"
            return
        tools = AgentTools(
            response,
            self.explorer,
            self.rpc,
            self.repositories,
            timeout_seconds=self.config.analysis.tool_timeout_seconds,
            max_result_chars=self.config.analysis.max_tool_result_chars,
        )
        try:
            async with asyncio.timeout(self.config.analysis.agent_timeout_seconds):
                response.explanation = await run_agent(self.llm, tools, response, self.config, mode)
        except LLMError as exc:
            response.explanation_issue = exc.code
        except TimeoutError:
            response.explanation_issue = "llm_timeout"
        if response.explanation_issue:
            logger.info(
                "request_id=%s explanation_issue=%s",
                request_id.get(),
                response.explanation_issue,
            )

    def _build(
        self, tx_hash: str, request_id: str, results: dict, errors: dict
    ) -> AnalysisResponse:
        sources: list[Evidence] = []
        uncertainties = [
            f"{name}: {REASONS.get(code, 'resposta indisponível ou inválida')} ({code}). "
            "Confira a fonte/configuração e tente novamente."
            for name, code in sorted(errors.items())
        ]
        inputs: list[tuple[str, str, dict]] = []

        def add(source_id: str, payload: object, description: str, url: str | None = None) -> None:
            sources.append(
                Evidence(
                    id=source_id,
                    source_type="rpc" if source_id.startswith("rpc.") else "blockscout",
                    source_url=url,
                    description=description,
                    payload=payload,
                    confidence="observed",
                )
            )

        if "rpc.chain" in results:
            add(
                "rpc.chain",
                {"method": "eth_chainId", "result": results["rpc.chain"]},
                "RPC: rede verificada antes das leituras",
            )
        for source_id, kind, method in (
            ("explorer.transaction", "explorer", None),
            ("rpc.transaction", "transaction", "eth_getTransactionByHash"),
            ("rpc.receipt", "receipt", "eth_getTransactionReceipt"),
        ):
            if source_id not in results:
                continue
            raw = results[source_id]
            payload = compact_transaction(raw) if isinstance(raw, dict) else None
            if kind == "explorer":
                add(
                    source_id,
                    payload,
                    "Blockscout: dados indexados da transação",
                    self.explorer.transaction_api_url(tx_hash),
                )
            else:
                add(
                    source_id,
                    {"method": method, "params": [tx_hash], "result": payload},
                    f"RPC: {method} (bloco informado no resultado)",
                )
            if isinstance(raw, dict):
                inputs.append((source_id, kind, raw))
            else:
                uncertainties.append(
                    f"{source_id}: resultado nulo; não confirma pendência nem inexistência. "
                    "Confira o hash, a rede e a sincronização do provedor."
                )
        details, status, status_ids, conflicts = merge_transaction(
            inputs,
            self.config.network.native_currency,
            self.config.network.native_decimals,
        )
        uncertainties.extend(conflicts)
        response = AnalysisResponse(
            network=self.config.network.id,
            tx_hash=tx_hash,
            request_id=request_id,
            explorer_url=self.explorer.get_explorer_transaction_url(tx_hash),
            transaction=details,
            status=status,
            summary="",
            status_evidence_ids=status_ids,
        )
        receipt = results.get("rpc.receipt")
        log_items = None
        log_source = "explorer.logs"
        for name, suffix in (
            ("logs", "logs"),
            ("transfers", "token-transfers"),
            ("calls", "internal-transactions"),
        ):
            source_id = f"explorer.{name}"
            collection = results.get(source_id)
            if not isinstance(collection, Collection):
                continue
            if collection.issue:
                uncertainties.append(
                    f"{source_id}: coleta incompleta ({collection.issue}). "
                    "Consulte o explorer para os dados restantes."
                )
            if not collection.pages:
                continue
            items = [
                item
                for item in collection.items
                if item.get("transaction_hash") is None
                or hex_data(item["transaction_hash"], 32) == tx_hash
            ]
            if len(items) != len(collection.items):
                uncertainties.append(f"{source_id}: registros de outro hash descartados.")
            add(
                source_id,
                {"items": items, "pages": collection.pages, "complete": collection.complete},
                f"Blockscout: {suffix}",
                f"{self.explorer.transaction_api_url(tx_hash)}/{suffix}",
            )
            if name == "logs":
                log_items = items
            elif name == "transfers":
                response.transfers = [normalize_transfer(item, source_id) for item in items]
            else:
                response.calls = [normalize_call(item, source_id) for item in items]
        if isinstance(receipt, dict) and isinstance(receipt.get("logs"), list):
            raw_logs = receipt["logs"]
            limit = self.config.analysis.max_collection_items
            log_items = [item for item in raw_logs[:limit] if isinstance(item, dict)]
            log_source = "rpc.logs"
            complete = len(raw_logs) <= limit and len(log_items) == len(raw_logs)
            add(
                log_source,
                {
                    "method": "eth_getTransactionReceipt",
                    "params": [tx_hash],
                    "blockHash": receipt.get("blockHash"),
                    "logs": log_items,
                    "complete": complete,
                },
                "RPC: logs do recibo",
            )
            if not complete:
                uncertainties.append(
                    "Logs do recibo incompletos por limite ou formato inválido. "
                    "Consulte o explorer."
                )
        if log_items is not None:
            response.events, rejected = normalize_logs(log_items, log_source, tx_hash)
            if rejected:
                uncertainties.append(
                    f"{rejected} logs inválidos/removidos foram descartados; consulte a fonte."
                )
        if status == "failed" and (response.events or response.transfers):
            response.events = []
            response.transfers = []
            uncertainties.append(
                "O status indica falha: logs/transferências inconsistentes foram omitidos. "
                "Os dados originais permanecem nas fontes para conferência."
            )
        if not self.rpc:
            uncertainties.append(
                "RPC não configurado: recibo bruto e consultas de estado indisponíveis. "
                "Defina RPC_URL para habilitar essas fontes."
            )
        if details.sender is None or details.value_raw is None or details.calldata is None:
            uncertainties.append(
                "Dados básicos incompletos: origem, valor ou calldata não puderam ser obtidos. "
                "Confira hash/rede e disponibilidade dos provedores."
            )
        if status == "unknown":
            uncertainties.append(
                "Não foi possível confirmar o status. É necessário um recibo ou status "
                "válido e consistente do explorer."
            )
        if status == "failed":
            response.diagnosis.confirmed.append(
                Finding(
                    description="A transação falhou segundo as fontes consultadas.",
                    evidence_ids=status_ids,
                )
            )
            response.summary = (
                "A transação falhou. O valor informado não deve ser tratado "
                "como transferência concluída."
            )
        elif status == "success":
            response.summary = (
                "A transação foi executada com sucesso segundo as fontes consultadas."
            )
        elif status == "pending":
            response.summary = (
                "A transação foi observada como pendente; a execução ainda não está confirmada."
            )
        else:
            response.summary = (
                "As evidências disponíveis não permitem confirmar o resultado da transação."
            )
        response.sources = sources
        response.uncertainties = uncertainties
        return response

    async def _resolve_abis(
        self, response: AnalysisResponse, remaining: float
    ) -> tuple[dict[str, ResolvedAbi | AbiMiss | None], int]:
        """Resolve ABIs for the call target and log emitters within the remaining deadline."""
        tx = response.transaction
        targets = [tx.recipient] if tx.recipient and tx.selector else []
        targets.extend(event.address for event in response.events)
        if response.status == "failed":
            # A revert may come from a nested contract: its ABI may declare the error.
            targets.extend(call.recipient for call in response.calls if call.recipient)
        targets = list(dict.fromkeys(targets))
        limit = self.config.analysis.max_abi_contracts
        skipped = max(0, len(targets) - limit)
        if not targets:
            return {}, skipped
        resolver = AbiResolver(self.explorer, self.config.abi.strategies, self.repositories)
        tasks = {
            address: asyncio.create_task(resolver.resolve(address)) for address in targets[:limit]
        }
        # None marks a resolution interrupted by the analysis deadline.
        resolutions: dict[str, ResolvedAbi | AbiMiss | None] = {}
        try:
            await asyncio.wait(tasks.values(), timeout=max(remaining, 0))
            for address, task in tasks.items():
                done = task.done() and not task.cancelled()
                resolutions[address] = task.result() if done else None
        finally:
            for task in tasks.values():
                task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
            await resolver.aclose()
        return resolutions, skipped

    def _decode(
        self,
        response: AnalysisResponse,
        resolutions: dict[str, ResolvedAbi | AbiMiss | None],
        skipped: int,
    ) -> None:
        tx = response.transaction
        uncertainties = response.uncertainties
        cited: set[str] = set()

        def add_once(evidence: Evidence) -> None:
            if evidence.id not in cited:
                cited.add(evidence.id)
                response.sources.append(evidence)

        def cite(resolved: ResolvedAbi, part: AbiPart) -> list[str]:
            ids = []
            if part.role == "implementation" and resolved.metadata:
                ids.append(f"explorer.contract.{resolved.address}")
                add_once(
                    Evidence(
                        id=ids[-1],
                        source_type="blockscout",
                        source_url=self.explorer.contract_api_url(resolved.address),
                        description="Blockscout: metadados do contrato e implementação de proxy",
                        payload=resolved.metadata.model_dump(),
                        confidence="observed",
                    )
                )
                uncertainties.append(
                    f"O contrato {resolved.address} é um proxy. A implementação {part.address} "
                    "é a atual informada pelo explorer e pode diferir da vigente no bloco da "
                    "transação; confirme o slot de implementação por RPC histórico se necessário."
                )
            summary = {
                "address": part.address,
                "contract": part.name,
                "role": part.role,
                "abi_source": resolved.source,
                "functions": sum(1 for i in part.abi if i.get("type") == "function"),
                "events": sum(1 for i in part.abi if i.get("type") == "event"),
            }
            if part.artifact:
                artifact = part.artifact
                ids.append(f"repository.abi.{part.address}")
                add_once(
                    Evidence(
                        id=ids[-1],
                        source_type="repository",
                        source_url=artifact.repo.file_url(artifact.commit, artifact.path),
                        description="Repositório configurado: ABI de artefato compilado",
                        payload={
                            **summary,
                            "repository": artifact.repo.web_url,
                            "commit": artifact.commit,
                            "path": artifact.path,
                        },
                        confidence="observed",
                    )
                )
                uncertainties.append(
                    f"A ABI de {part.address} veio do artefato {artifact.path} "
                    f"({artifact.repo.label}@{artifact.commit[:12]}), associado pelo nome "
                    f"{part.name}. O explorer não confirmou que esse artefato corresponde ao "
                    "bytecode implantado."
                )
                return ids
            ids.append(f"explorer.abi.{part.address}")
            add_once(
                Evidence(
                    id=ids[-1],
                    source_type="blockscout",
                    source_url=self.explorer.contract_api_url(part.address),
                    description="Blockscout: ABI verificada do contrato",
                    payload=summary,
                    confidence="observed",
                )
            )
            return ids

        def miss_text(address: str) -> str:
            if address not in resolutions:
                return "ABI não consultada por limite de contratos"
            resolution = resolutions[address]
            if resolution is None:
                return "tempo limite da análise excedido antes de obter a ABI"
            assert isinstance(resolution, AbiMiss)
            return "; ".join(
                f"{strategy}: {REASONS.get(code, code)}"
                for strategy, code in resolution.reasons.items()
            )

        def decode_with(resolved: ResolvedAbi, operation) -> tuple[Decoded, AbiPart] | DecodeMiss:
            miss = DecodeMiss("decode_failed")
            # Parts follow EVM dispatch order: the proxy's own ABI, then implementations.
            for part in resolved.parts:
                outcome = operation(part.abi)
                if isinstance(outcome, Decoded):
                    return outcome, part
                miss = outcome
            return miss

        for resolution in resolutions.values():
            if isinstance(resolution, ResolvedAbi) and resolution.issues:
                uncertainties.append(
                    f"ABI de {resolution.address} parcialmente resolvida "
                    f"({', '.join(resolution.issues)}); a decodificação pode estar incompleta."
                )
        if skipped:
            uncertainties.append(
                f"{skipped} contrato(s) excederam max_abi_contracts e não tiveram ABI consultada."
            )

        if tx.recipient and tx.selector:
            resolution = resolutions.get(tx.recipient)
            outcome: tuple[Decoded, AbiPart] | DecodeMiss | None = None
            if isinstance(resolution, ResolvedAbi):
                outcome = decode_with(resolution, lambda abi: decode_calldata(tx.calldata, abi))
            if isinstance(resolution, ResolvedAbi) and isinstance(outcome, tuple):
                decoded, part = outcome
                abi_ids = cite(resolution, part)
                calldata_ids = tx.field_sources.get("calldata", [])
                nested = [
                    NestedCall(
                        argument=item.argument,
                        index=item.index,
                        selector=item.selector,
                        function=item.call.name if item.call else None,
                        signature=item.call.signature if item.call else None,
                        arguments=item.call.arguments if item.call else [],
                        reason=item.reason,
                    )
                    for item in decoded.nested
                ]
                response.sources.append(
                    Evidence(
                        id="decoder.input",
                        source_type="decoder",
                        description="Decodificação determinística da calldata pela ABI",
                        payload={
                            "selector": tx.selector,
                            "signature": decoded.signature,
                            "nested_signatures": [n.signature for n in nested if n.signature],
                            "abi_evidence_ids": abi_ids,
                            "calldata_evidence_ids": calldata_ids,
                        },
                        confidence="decoded",
                    )
                )
                evidence_ids = ["decoder.input", *abi_ids, *calldata_ids]
                tx.decoded_input = DecodedCall(
                    function=decoded.name,
                    signature=decoded.signature,
                    selector=tx.selector,
                    arguments=decoded.arguments,
                    abi_source=resolution.source,
                    contract=tx.recipient,
                    abi_address=part.address,
                    evidence_ids=evidence_ids,
                    nested_calls=nested,
                )
                tx.field_sources["decoded_input"] = evidence_ids
                response.summary += f" Função chamada, decodificada pela ABI: {decoded.signature}."
                if nested:
                    inner = ", ".join(
                        n.signature or f"{n.selector} (não decodificada)" for n in nested
                    )
                    response.summary += f" Chamadas agrupadas, na ordem: {inner}."
                    note = (
                        f"Os elementos de {nested[0].argument} foram lidos como chamadas ao "
                        "próprio contrato (padrão multicall) e decodificados pela mesma ABI, "
                        "em modo estrito."
                    )
                    if response.status == "failed":
                        note += " Sem trace, não é possível dizer qual delas reverteu."
                    uncertainties.append(note)
            else:
                reason = (
                    DECODE_REASONS.get(outcome.reason, outcome.reason)
                    if isinstance(outcome, DecodeMiss)
                    else miss_text(tx.recipient)
                )
                uncertainties.append(
                    f"Chamada principal não decodificada ({reason}). O seletor {tx.selector} "
                    "é apenas um prefixo da calldata e não confirma a identidade da função; "
                    "é necessária a ABI verificada do contrato de destino."
                )

        decoded_logs: list[dict] = []
        missing: dict[str, int] = {}
        for event in response.events:
            resolution = resolutions.get(event.address)
            if not isinstance(resolution, ResolvedAbi):
                reason = miss_text(event.address)
                missing[reason] = missing.get(reason, 0) + 1
                continue
            log_outcome = decode_with(
                resolution, lambda abi, event=event: decode_log(event.topics, event.data, abi)
            )
            if isinstance(log_outcome, DecodeMiss):
                reason = DECODE_REASONS.get(log_outcome.reason, log_outcome.reason)
                missing[reason] = missing.get(reason, 0) + 1
                continue
            decoded, part = log_outcome
            event.decoded = DecodedEvent(
                name=decoded.name,
                signature=decoded.signature,
                arguments=decoded.arguments,
                abi_source=resolution.source,
                abi_address=part.address,
                evidence_ids=["decoder.logs", *cite(resolution, part), *event.evidence_ids],
            )
            decoded_logs.append(
                {"index": event.index, "address": event.address, "signature": decoded.signature}
            )
        if decoded_logs:
            response.sources.append(
                Evidence(
                    id="decoder.logs",
                    source_type="decoder",
                    description="Decodificação determinística dos logs pela ABI do emissor",
                    payload={"decoded": decoded_logs},
                    confidence="decoded",
                )
            )
        for reason, count in missing.items():
            uncertainties.append(
                f"{count} log(s) sem decodificação: {reason}. Tópicos e dados brutos "
                "permanecem disponíveis; é necessária a ABI verificada do emissor."
            )
        response.uncertainties = list(dict.fromkeys(uncertainties))
