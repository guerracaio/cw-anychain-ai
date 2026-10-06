"""Failure diagnosis: revert reason, replay on the pre-block state, source sites, state reads.

Everything here is deterministic. Hypotheses go to `likely_causes` with their evidence;
only data reported by the explorer or the node goes to `confirmed`.
"""

from app.blockchain.abi_resolver import AbiMiss, ResolvedAbi
from app.blockchain.decoder import (
    ERROR_STRING,
    PANIC,
    DecodedRevert,
    decode_output,
    decode_revert,
    encode_call,
    signature,
)
from app.blockchain.rpc_client import RpcClient
from app.blockchain.values import hex_data, quantity
from app.domain.analysis import (
    AnalysisResponse,
    Evidence,
    Finding,
    RevertInfo,
    SourceExcerpt,
    StateRead,
)
from app.domain.transaction import DecodedArg
from app.explorer.blockscout_client import BlockscoutClient
from app.repositories.resolver import find_revert_sites
from app.services.http import UpstreamError

KIND_TEXT = {
    "error_string": "require/revert com mensagem",
    "panic": "verificação automática do compilador (Panic)",
    "custom_error": "erro customizado",
    "empty": "revert sem dados (require sem mensagem, revert() vazio, opcode inválido ou "
    "falta de gas)",
    "unknown": "dados de revert não decodificados",
    "out_of_gas": "falta de gas",
}


def _explorer_revert(raw_tx: dict) -> RevertInfo | None:
    """Blockscout either returns {'raw': '0x..'} or an ABI-decoded {method_call, ...}."""
    reason = raw_tx.get("revert_reason")
    result = raw_tx.get("result")
    if isinstance(reason, dict) and isinstance(reason.get("method_id"), str):
        method_id = "0x" + reason["method_id"].removeprefix("0x").lower()
        params = [p for p in reason.get("parameters") or [] if isinstance(p, dict) and "name" in p]
        arguments = [
            DecodedArg(name=str(p["name"]), type=str(p.get("type", "")), value=str(p.get("value")))
            for p in params
        ]
        call = reason.get("method_call") if isinstance(reason.get("method_call"), str) else None
        if method_id == ERROR_STRING and arguments:
            return RevertInfo(
                source="explorer",
                kind="error_string",
                signature="Error(string)",
                name="Error",
                message=str(arguments[0].value),
                evidence_ids=["explorer.transaction"],
            )
        if method_id == PANIC:
            return RevertInfo(
                source="explorer",
                kind="panic",
                signature="Panic(uint256)",
                name="Panic",
                message=str(arguments[0].value) if arguments else None,
                evidence_ids=["explorer.transaction"],
            )
        name = call.split("(", 1)[0] if call else None
        return RevertInfo(
            source="explorer",
            kind="custom_error",
            signature=call,
            name=name,
            arguments=arguments,
            raw=method_id,
            evidence_ids=["explorer.transaction"],
        )
    if isinstance(reason, dict) and hex_data(reason.get("raw")):
        return RevertInfo(
            source="explorer",
            kind="unknown",
            raw=hex_data(reason.get("raw")),
            evidence_ids=["explorer.transaction"],
        )
    if isinstance(result, str) and "out of gas" in result.lower():
        return RevertInfo(
            source="explorer", kind="out_of_gas", evidence_ids=["explorer.transaction"]
        )
    return None


def _from_decoded(decoded: DecodedRevert, raw: str | None, evidence: list[str]) -> RevertInfo:
    return RevertInfo(
        source="rpc_replay",
        kind=decoded.kind,  # type: ignore[arg-type]
        signature=decoded.signature,
        name=decoded.name,
        message=decoded.message,
        arguments=decoded.arguments or [],
        raw=raw,
        abi_address=decoded.abi_address,
        ambiguous=decoded.ambiguous,
        evidence_ids=evidence,
    )


def describe(revert: RevertInfo) -> str:
    detail = revert.signature or revert.raw or ""
    if revert.message:
        detail = f"{detail} — “{revert.message}”"
    origin = "repetição via RPC" if revert.source == "rpc_replay" else "explorer"
    text = f"Motivo da falha ({origin}): {KIND_TEXT[revert.kind]}"
    return f"{text}: {detail}." if detail else f"{text}."


async def diagnose(
    response: AnalysisResponse,
    explorer_tx: dict | None,
    resolutions: dict[str, ResolvedAbi | AbiMiss | None],
    rpc: RpcClient | None,
    explorer: BlockscoutClient,
    budget: int,
) -> None:
    tx = response.transaction
    diagnosis = response.diagnosis
    uncertainties = response.uncertainties
    parts = [p for r in resolutions.values() if isinstance(r, ResolvedAbi) for p in r.parts]
    abis = [(p.address, p.abi) for p in parts]
    explorer_revert = _explorer_revert(explorer_tx) if explorer_tx else None
    if explorer_revert and explorer_revert.kind == "unknown" and explorer_revert.raw:
        decoded = decode_revert(explorer_revert.raw, abis)
        if decoded.kind != "unknown":
            explorer_revert = _from_decoded(decoded, explorer_revert.raw, ["explorer.transaction"])
            explorer_revert.source = "explorer"

    replay_revert: RevertInfo | None = None
    diagnosis.replay = "not_attempted"
    can_replay = tx.sender and tx.recipient and tx.block_number and tx.calldata is not None
    if rpc and can_replay:
        call = {
            "from": tx.sender,
            "to": tx.recipient,
            "data": tx.calldata,
            "value": hex(int(tx.value_raw)) if tx.value_raw else None,
            "gas": hex(int(tx.gas_limit)) if tx.gas_limit else None,
        }
        block = hex(tx.block_number - 1)  # type: ignore[operator]
        try:
            outcome = await rpc.simulate(call, block)
        except UpstreamError as exc:
            diagnosis.replay = "unavailable"
            reason = (
                "o nó RPC não tem o estado histórico desse bloco (é necessário um nó de "
                "arquivo/archive)"
                if exc.code == "historical_state_unavailable"
                else f"o RPC recusou a consulta ({exc.code})"
            )
            uncertainties.append(
                f"Não foi possível repetir a chamada no estado anterior ao bloco: {reason}."
            )
        else:
            diagnosis.replay = "reverted" if outcome.reverted else "succeeded"
            response.sources.append(
                Evidence(
                    id="rpc.replay",
                    source_type="rpc",
                    description=f"RPC: eth_call repetindo a transação no bloco {block}",
                    payload={
                        "method": "eth_call",
                        "params": [call, block],
                        "reverted": outcome.reverted,
                        "data": outcome.data,
                    },
                    confidence="observed",
                )
            )
            if outcome.reverted:
                decoded = decode_revert(outcome.data, abis)
                replay_revert = _from_decoded(decoded, outcome.data, ["rpc.replay"])

    # Prefer the replay (raw bytes decoded here); fall back to the explorer when the replay
    # carries no decodable data.
    informative = replay_revert and replay_revert.kind not in ("empty", "unknown")
    revert = replay_revert if informative else (explorer_revert or replay_revert)
    if (
        replay_revert
        and explorer_revert
        and replay_revert.signature
        and explorer_revert.signature
        and (replay_revert.signature, replay_revert.message)
        != (explorer_revert.signature, explorer_revert.message)
    ):
        uncertainties.append(
            "O motivo obtido na repetição difere do informado pelo explorer; o estado "
            "anterior ao bloco pode não reproduzir exatamente a execução original."
        )
    if revert:
        ids = list(revert.evidence_ids)
        if revert.abi_address and f"explorer.abi.{revert.abi_address}" in {
            s.id for s in response.sources
        }:
            ids.append(f"explorer.abi.{revert.abi_address}")
        revert.evidence_ids = ids
        diagnosis.revert = revert
        diagnosis.confirmed.append(Finding(description=describe(revert), evidence_ids=ids))
        if revert.ambiguous:
            uncertainties.append(
                "O seletor do erro corresponde a mais de uma declaração nas ABIs conhecidas; "
                "o nome do erro pode não ser o correto."
            )
        if revert.kind == "unknown":
            uncertainties.append(
                "Os dados de revert não correspondem a Error(string), Panic nem a erros das "
                "ABIs obtidas; é necessária a ABI do contrato que reverteu."
            )
    elif diagnosis.replay != "succeeded":
        uncertainties.append(
            "Motivo da falha indisponível: o explorer não o informou e a repetição via RPC "
            "não foi possível. É necessário um RPC com estado histórico ou um trace."
        )

    status_ids = response.status_evidence_ids
    gas_exhausted = (
        tx.gas_used is not None and tx.gas_limit is not None and tx.gas_used == tx.gas_limit
    )
    if gas_exhausted:
        diagnosis.confirmed.append(
            Finding(
                description=f"Todo o gas disponível foi consumido ({tx.gas_used} de "
                f"{tx.gas_limit}).",
                evidence_ids=tx.field_sources.get("gas_used", []),
            )
        )
    if diagnosis.replay == "succeeded":
        if gas_exhausted:
            diagnosis.likely_causes.append(
                Finding(
                    description="Falta de gas: a mesma chamada executa sem erro no estado "
                    "anterior ao bloco, e a transação original esgotou o limite de gas.",
                    evidence_ids=["rpc.replay", *tx.field_sources.get("gas_used", [])],
                )
            )
        else:
            diagnosis.likely_causes.append(
                Finding(
                    description="O estado mudou dentro do próprio bloco: a mesma chamada "
                    "executa sem erro no estado anterior ao bloco, então uma transação anterior "
                    "no mesmo bloco provavelmente alterou a condição verificada (ex.: ordem já "
                    "preenchida, preço ou saldo alterado).",
                    evidence_ids=["rpc.replay", *status_ids],
                )
            )

    if revert and revert.kind in ("error_string", "custom_error"):
        sources: dict[str, dict[str, str]] = {}
        for part in parts:
            if part.metadata and part.metadata.sources:
                sources[part.address] = part.metadata.sources
        for address, files in sources.items():
            excerpts = find_revert_sites(
                files,
                error=revert.name if revert.kind == "custom_error" else None,
                message=revert.message if revert.kind == "error_string" else None,
                budget=budget,
            )
            if not excerpts:
                continue
            evidence_id = f"explorer.source.{address}"
            if evidence_id not in {s.id for s in response.sources}:
                response.sources.append(
                    Evidence(
                        id=evidence_id,
                        source_type="blockscout",
                        source_url=explorer.contract_api_url(address),
                        description=f"Blockscout: código-fonte verificado de {address}",
                        payload={"address": address, "files": sorted({e.path for e in excerpts})},
                        confidence="observed",
                    )
                )
            response.contract_context.extend(
                SourceExcerpt(
                    origin="explorer",
                    contract=e.contract,
                    address=address,
                    symbol=e.symbol,
                    kind=e.kind,
                    reason=e.reason,
                    path=e.path,
                    start_line=e.start_line,
                    end_line=e.end_line,
                    code=e.code,
                    truncated=e.truncated,
                    url=explorer.get_explorer_contract_url(address),
                    evidence_ids=[evidence_id],
                )
                for e in excerpts
            )
            budget -= sum(len(e.code) for e in excerpts)
            if budget <= 0:
                break

    if rpc and diagnosis.replay in ("reverted", "succeeded"):
        await _token_state(response, resolutions, rpc)

    steps = []
    if revert and revert.kind == "error_string":
        steps.append(
            "Localize a mensagem do require no código do contrato e verifique a condição "
            "com os parâmetros da chamada."
        )
    if revert and revert.kind == "custom_error":
        steps.append(
            "Verifique quando o contrato emite esse erro (trechos abaixo) e ajuste a chamada."
        )
    if diagnosis.replay == "unavailable" or not revert:
        steps.append(
            "Configure um RPC com estado histórico (archive) para repetir a chamada e ler o "
            "estado no bloco anterior."
        )
    if diagnosis.replay == "succeeded":
        steps.append(
            "Compare com as transações anteriores no mesmo bloco para identificar o que mudou "
            "o estado; se a falha foi por gas, reenvie com limite maior."
        )
    steps.append("Corrija a causa antes de reenviar: a mesma chamada tende a falhar de novo.")
    diagnosis.next_steps = steps


async def _token_state(
    response: AnalysisResponse,
    resolutions: dict[str, ResolvedAbi | AbiMiss | None],
    rpc: RpcClient,
) -> None:
    """Common ERC-20 preconditions at the pre-block state: balance and allowance."""
    tx = response.transaction
    decoded = tx.decoded_input
    if not decoded or tx.block_number is None or tx.sender is None:
        return
    values = [a.value for a in decoded.arguments]
    if decoded.signature == "transfer(address,uint256)":
        owner, amount, spender = tx.sender, values[1], None
    elif decoded.signature == "transferFrom(address,address,uint256)":
        owner, amount, spender = values[0], values[2], tx.sender
    else:
        return
    resolution = resolutions.get(decoded.contract)
    if not isinstance(resolution, ResolvedAbi):
        return
    items = {}
    for part in resolution.parts:
        for item in part.abi:
            if item.get("type") == "function":
                try:
                    items.setdefault(signature(item), item)
                except Exception:
                    continue
    block = hex(tx.block_number - 1)
    reads = [("balanceOf(address)", [owner])]
    if spender:
        reads.append(("allowance(address,address)", [owner, spender]))
    found: dict[str, int] = {}
    for full, arguments in reads:
        item = items.get(full)
        if item is None:
            continue
        try:
            data = encode_call(item, [str(a) for a in arguments])
            outcome = await rpc.simulate({"to": decoded.contract, "data": data}, block)
        except (ValueError, UpstreamError):
            continue
        if outcome.reverted or outcome.data is None:
            continue
        try:
            outputs = decode_output(item, outcome.data)
        except Exception:
            continue
        evidence_id = f"rpc.state.{len(response.diagnosis.state_reads) + 1}"
        response.sources.append(
            Evidence(
                id=evidence_id,
                source_type="rpc",
                description=f"RPC: eth_call {full} em {decoded.contract} (bloco {block})",
                payload={
                    "method": "eth_call",
                    "to": decoded.contract,
                    "data": data,
                    "block": block,
                    "result": outcome.data,
                },
                confidence="observed",
            )
        )
        response.diagnosis.state_reads.append(
            StateRead(
                contract=decoded.contract,
                signature=full,
                arguments=[str(a) for a in arguments],
                block=block,
                outputs=outputs,
                evidence_ids=[evidence_id],
            )
        )
        number = quantity(outputs[0].value) if outputs else None
        if number is not None:
            found[full] = number
    required = quantity(amount)
    if required is None:
        return
    labels = {
        "balanceOf(address)": "Saldo",
        "allowance(address,address)": "Autorização (allowance)",
    }
    for full, value in found.items():
        if value < required:
            read = next(r for r in response.diagnosis.state_reads if r.signature == full)
            response.diagnosis.likely_causes.append(
                Finding(
                    description=f"{labels[full]} de {owner} no estado anterior ao bloco "
                    f"({value}) era menor que o valor da chamada ({required}).",
                    evidence_ids=[*read.evidence_ids, "decoder.input"],
                )
            )
