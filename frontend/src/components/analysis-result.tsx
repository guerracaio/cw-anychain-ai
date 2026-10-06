import type { MouseEvent } from "react";
import type { AnalysisResponse, DecodedArg, JsonValue } from "@/lib/api";
import { LlmLogo } from "@/components/llm-logo";
import { RichText } from "@/components/rich-text";

function revealSource(event: MouseEvent<HTMLAnchorElement>, id: string) {
  const target = document.getElementById(`source-${id}`);
  if (!target) return;
  event.preventDefault();
  for (let node = target.parentElement; node; node = node.parentElement) {
    if (node instanceof HTMLDetailsElement) node.open = true;
  }
  target.scrollIntoView({ behavior: "smooth", block: "start" });
  history.replaceState(null, "", `#source-${id}`);
}

function References({ ids }: { ids: string[] }) {
  return <span className="ml-2 inline-flex flex-wrap gap-2 text-xs text-brand-600">
    {ids.map((id) => <a key={id} href={`#source-${id}`} onClick={(event) => revealSource(event, id)} className="underline underline-offset-4">{id}</a>)}
  </span>;
}

function RawData({ data }: { data: JsonValue }) {
  return <pre className="mt-3 max-h-72 overflow-auto rounded-lg bg-ink-200/70 p-3 text-xs leading-6 whitespace-pre-wrap break-all text-ink-900">{JSON.stringify(data, null, 2)}</pre>;
}

function Arguments({ args }: { args: DecodedArg[] }) {
  if (!args.length) return <p className="mt-3 text-sm text-ink-800">Sem argumentos.</p>;
  return <dl className="mt-3 space-y-3">
    {args.map((arg, i) => <div key={i}>
      <dt className="font-mono text-xs text-ink-800">{arg.name} <span className="text-ink-800/80">{arg.type}</span></dt>
      <dd className="mt-1 font-mono text-sm break-all">
        {typeof arg.value === "string" ? arg.value : JSON.stringify(arg.value)}
        {arg.hashed && <span className="ml-2 text-xs text-brand-600">(hash keccak do valor indexado)</span>}
        {arg.truncated && <span className="ml-2 text-xs text-brand-600">(truncado)</span>}
      </dd>
    </div>)}
  </dl>;
}

const decodeReasons: Record<string, string> = {
  no_selector: "sem seletor de 4 bytes",
  selector_not_in_abi: "seletor fora da ABI",
  ambiguous_selector: "seletor ambíguo na ABI",
  decode_failed: "dados incompatíveis com a ABI",
};

const replayLabels: Record<string, string> = {
  reverted: "reverteu novamente (motivo obtido do nó RPC)",
  succeeded: "executou sem erro — o estado mudou no próprio bloco ou faltou gas",
  unavailable: "indisponível (o nó RPC não tem o estado histórico)",
  not_attempted: "não realizada (RPC não configurado ou dados insuficientes)",
};

const findingLabels: Record<string, string> = {
  observed: "observado",
  decoded: "decodificado",
  state: "estado",
  source: "código",
  inference: "inferência",
};

const issueLabels: Record<string, string> = {
  llm_not_configured: "nenhum provedor configurado (LLM_PROVIDER)",
  llm_model_not_configured: "modelo não configurado (LLM_MODEL)",
  llm_api_key_missing: "chave de API ausente",
  llm_provider_not_supported: "provedor ainda não suportado",
  llm_access_denied: "chave recusada pelo provedor",
  llm_model_not_found: "modelo não encontrado no provedor",
  llm_rate_limited: "limite de uso do provedor atingido",
  llm_quota_exceeded: "sem créditos ou cota na conta do provedor",
  llm_unavailable: "provedor temporariamente indisponível",
  llm_timeout: "tempo limite excedido",
  agent_step_limit: "limite de passos do agente atingido",
  insufficient_evidence: "evidências insuficientes para uma explicação",
};

const reasonLabels: Record<string, string> = {
  contract_header: "declaração do contrato",
  called_function: "função chamada",
  related_modifier: "modificador aplicado",
  related_error: "erro referenciado",
  related_function: "função interna chamada",
  requested_symbol: "consultado pela IA",
  revert_site: "onde o erro é lançado",
  error_declaration: "declaração do erro",
};

const statusLabels = { success: "Sucesso", failed: "Falhou", pending: "Pendente", unknown: "Não confirmado" };
const statusStyles = {
  success: "bg-accent-500 text-ink-900",
  failed: "bg-ink-900 text-white",
  pending: "border border-brand-600 text-brand-600",
  unknown: "border border-ink-400 text-ink-800",
};
const emptyMessage = "Nenhum registro disponível nesta coleta. Consulte as limitações abaixo.";

export function AnalysisResult({ result }: { result: AnalysisResponse }) {
  const tx = result.transaction;
  const fields: [string, string, string | number | null][] = [
    ["sender", "Remetente", tx.sender],
    ["recipient", "Destinatário", tx.recipient],
    ["created_contract", "Contrato criado", tx.created_contract],
    ["block_number", "Bloco", tx.block_number],
    ["value_display", "Valor nativo informado", tx.value_display === null ? null : `${tx.value_display} ${tx.native_currency}`],
    ["value_raw", "Valor em unidades mínimas", tx.value_raw],
    ["gas_used", "Gas utilizado", tx.gas_used],
    ["gas_limit", "Limite de gas", tx.gas_limit],
    ["gas_price", "Preço do gas em unidades mínimas", tx.gas_price],
    ["selector", tx.decoded_input ? "Seletor" : "Seletor (sem identidade confirmada)", tx.selector],
  ];
  const call = tx.decoded_input;

  const ai = result.explanation;
  return <section className="mt-6 min-w-0 space-y-7 [overflow-wrap:anywhere]" aria-labelledby="result-title">
    {ai && <section className="rounded-3xl border border-brand-200 bg-brand-0/30 p-6 sm:p-8" aria-labelledby="ai-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="ai-title" className="text-xl font-bold">Explicação</h2>
        <span className="inline-flex items-center gap-1.5 rounded-full bg-brand-600 px-3 py-1 text-xs font-medium text-white">
          <LlmLogo provider={ai.usage.provider} />
          Gerada por IA · {ai.usage.model}
        </span>
      </div>
      <div>
      <p className="mt-4 text-[17px] leading-8 whitespace-pre-line"><RichText text={ai.summary} /></p>
      {ai.findings.length > 0 && <details className="group mt-5 rounded-2xl bg-white/70 ring-1 ring-brand-0">
        <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-medium text-brand-600 [&::-webkit-details-marker]:hidden">
          <span aria-hidden="true" className="transition-transform group-open:rotate-90">›</span>
          Como a IA chegou a essa conclusão ({ai.findings.length} {ai.findings.length === 1 ? "evidência analisada" : "evidências analisadas"})
        </summary>
        <ul className="space-y-3 px-4 pb-4 text-sm leading-6">
          {ai.findings.map((finding, i) => <li key={i}>
            <span className="mr-2 rounded-full bg-white px-2 py-0.5 text-xs text-brand-600 ring-1 ring-brand-200">{findingLabels[finding.kind]}</span>
            <RichText text={finding.statement} /><References ids={finding.evidence_ids} />
          </li>)}
        </ul>
      </details>}
      {ai.likely_causes.length > 0 && <>
        <h3 className="mt-6 text-sm font-bold">Causas prováveis (hipóteses)</h3>
        <ul className="mt-2 list-disc space-y-2 pl-5 text-sm leading-6">{ai.likely_causes.map((cause, i) => <li key={i}><RichText text={cause.description} /><References ids={cause.evidence_ids} /></li>)}</ul>
      </>}
      {ai.next_steps.length > 0 && <>
        <h3 className="mt-6 text-sm font-bold">Próximos passos sugeridos</h3>
        <ul className="mt-2 list-disc space-y-2 pl-5 text-sm leading-6 text-ink-900">{ai.next_steps.map((step, i) => <li key={i}><RichText text={step} /></li>)}</ul>
      </>}
      {ai.security_notes.length > 0 && <>
        <h3 className="mt-6 text-sm font-bold">Pontos para revisão de segurança</h3>
        <ul className="mt-2 list-disc space-y-2 pl-5 text-sm leading-6 text-ink-900">{ai.security_notes.map((note, i) => <li key={i}><RichText text={note.description} /><References ids={note.evidence_ids} /></li>)}</ul>
      </>}
      {ai.uncertainties.length > 0 && <>
        <h3 className="mt-6 text-sm font-bold">O que a IA não pôde confirmar</h3>
        <ul className="mt-2 list-disc space-y-2 pl-5 text-sm leading-6 text-ink-900">{ai.uncertainties.map((item, i) => <li key={i}><RichText text={item} /></li>)}</ul>
      </>}
      {ai.unverified_identifiers.length > 0 && <p className="mt-6 rounded-xl border border-brand-600 bg-white p-3 text-sm leading-6">
        O texto acima menciona identificadores que não aparecem em nenhuma evidência e podem estar incorretos: <span className="font-mono break-all">{ai.unverified_identifiers.join(", ")}</span>
      </p>}
      <p className="mt-6 text-xs leading-5 text-ink-800/80">
        Interpretação do modelo sobre as evidências abaixo; confira as fontes citadas.
        {ai.discarded_items > 0 && ` ${ai.discarded_items} afirmação(ões) sem evidência válida ou com identificadores desconhecidos foram descartadas.`}
        {` ${ai.usage.steps} passo(s), ${ai.usage.tool_calls.length} ferramenta(s), ${ai.usage.input_tokens + ai.usage.output_tokens + ai.usage.thinking_tokens} tokens`}
        {ai.usage.estimated_cost_usd !== null && `, custo estimado US$ ${ai.usage.estimated_cost_usd.toFixed(4)}`}.
      </p>
      </div>
    </section>}
    {result.security_notes.length > 0 && <section className="rounded-2xl border border-brand-200 bg-brand-0/20 p-6" aria-labelledby="security-title">
      <h2 id="security-title" className="text-lg font-bold">Pontos de atenção</h2>
      <ul className="mt-3 list-disc space-y-2 pl-5 text-sm leading-6 text-ink-900">{result.security_notes.map((note, i) => <li key={i}>{note.description}<References ids={note.evidence_ids} /></li>)}</ul>
    </section>}
    {!ai && result.explanation_issue && <p className="rounded-2xl border border-ink-200 bg-ink-200/60 p-4 text-sm leading-6 text-ink-800">
      Explicação por IA indisponível: {issueLabels[result.explanation_issue] ?? result.explanation_issue}. As evidências abaixo continuam válidas.
    </p>}
    <details className="group/details rounded-3xl border border-ink-200 bg-white" open={!ai}>
      <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-3 p-5 sm:px-8 [&::-webkit-details-marker]:hidden">
        <span className="flex items-center gap-3">
          <span aria-hidden="true" className="text-lg text-ink-800 transition-transform group-open/details:rotate-90">›</span>
          <span>
            <span className="block font-display font-bold">Detalhes técnicos e evidências</span>
            <span className="block text-xs text-ink-800">Transação, diagnóstico, eventos, código, limitações e {result.sources.length} fontes</span>
          </span>
        </span>
        <span className={`rounded-full px-3 py-1 text-sm font-medium ${statusStyles[result.status]}`}>{statusLabels[result.status]}</span>
      </summary>
      <div className="space-y-7 border-t border-ink-200 p-4 sm:p-6">
      <div className="rounded-2xl border border-ink-200 bg-white p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 id="result-title" className="text-xl font-bold">Resultado da consulta</h2>
          <span className={`rounded-full px-3 py-1 text-sm font-medium ${statusStyles[result.status]}`}>{statusLabels[result.status]}</span>
        </div>
        <p className="mt-4 leading-7">{result.summary}<References ids={result.status_evidence_ids} /></p>
        <p className="mt-4 text-xs text-ink-800">Rede: {result.network}</p>
        <a href={result.explorer_url} target="_blank" rel="noreferrer" className="mt-2 block font-mono text-xs break-all text-brand-600 underline underline-offset-4">{result.tx_hash} ↗</a>
        <dl className="mt-6 divide-y divide-ink-200">
          {fields.map(([key, label, value]) => <div key={key} className="py-3">
            <dt className="text-xs text-ink-800">{label}</dt>
            <dd className="mt-1 text-sm break-all">{value === null ? "Não disponível" : <span className="font-mono">{value}</span>}<References ids={tx.field_sources[key] ?? []} /></dd>
          </div>)}
        </dl>
        <p className="mt-3 text-xs leading-6 text-ink-800">O valor informado pertence à chamada principal. Em caso de falha ou pendência, não representa uma transferência concluída.</p>
        {call && <div className="mt-6 rounded-2xl border border-brand-200 bg-brand-0/20 p-4">
          <h3 className="text-sm font-bold">Função chamada<References ids={call.evidence_ids} /></h3>
          <p className="mt-2 font-mono text-sm break-all text-brand-600">{call.signature}</p>
          <p className="mt-1 text-xs text-ink-800">ABI do {call.abi_source === "explorer" ? "explorer" : "repositório"}{call.abi_address !== call.contract && <> · implementação de proxy <span className="font-mono break-all">{call.abi_address}</span></>}</p>
          <Arguments args={call.arguments} />
          {call.nested_calls.length > 0 && <>
            <h4 className="mt-5 text-sm font-bold">Chamadas agrupadas em <span className="font-mono">{call.nested_calls[0].argument}</span></h4>
            <p className="mt-1 text-xs leading-5 text-ink-800">Cada elemento foi decodificado pela mesma ABI (o contrato executa as chamadas em si mesmo, em ordem).</p>
            <ol className="mt-3 space-y-2">
              {call.nested_calls.map((inner) => <li key={`${inner.argument}.${inner.index}`}>
                <details className="rounded-xl bg-white/70 px-3 py-2 ring-1 ring-brand-0">
                  <summary className="cursor-pointer font-mono text-sm break-all">
                    <span className="mr-2 text-xs text-ink-800">#{inner.index}</span>
                    {inner.signature ?? <>{inner.selector ?? "sem seletor"} <span className="font-sans text-xs text-brand-600">(não decodificada: {decodeReasons[inner.reason ?? ""] ?? inner.reason})</span></>}
                  </summary>
                  {inner.signature && <Arguments args={inner.arguments} />}
                </details>
              </li>)}
            </ol>
          </>}
        </div>}
        <details className="mt-5"><summary className="cursor-pointer text-sm">Calldata bruta<References ids={tx.field_sources.calldata ?? []} /></summary>
          <pre className="mt-3 max-h-56 overflow-auto text-xs whitespace-pre-wrap break-all text-ink-800">{tx.calldata ?? "Não disponível"}</pre>
        </details>
      </div>

      {result.diagnosis.confirmed.length > 0 && <section className="rounded-2xl border-2 border-ink-900 p-6">
        <h3 className="font-bold">Diagnóstico da falha</h3>
        <h4 className="mt-4 text-sm font-medium">Confirmado</h4>
        {result.diagnosis.confirmed.map((finding, i) => <p key={i} className="mt-2 text-sm leading-6">{finding.description}<References ids={finding.evidence_ids} /></p>)}
        {result.diagnosis.revert && result.diagnosis.revert.arguments.length > 0 && <Arguments args={result.diagnosis.revert.arguments} />}
        {result.diagnosis.replay && <p className="mt-3 text-xs leading-5 text-ink-800">Repetição no estado anterior ao bloco: {replayLabels[result.diagnosis.replay]}</p>}
        {result.diagnosis.state_reads.length > 0 && <>
          <h4 className="mt-5 text-sm font-medium">Estado lido antes do bloco</h4>
          <ul className="mt-2 space-y-2 text-sm leading-6">{result.diagnosis.state_reads.map((read, i) => <li key={i} className="font-mono text-xs break-all">
            {read.signature}({read.arguments.join(", ")}) = {read.outputs.map((o) => typeof o.value === "string" ? o.value : JSON.stringify(o.value)).join(", ")} <span className="text-ink-800/80">· bloco {read.block}</span><References ids={read.evidence_ids} />
          </li>)}</ul>
        </>}
        {result.diagnosis.likely_causes.length > 0 && <>
          <h4 className="mt-5 text-sm font-medium">Causa provável (hipótese)</h4>
          <ul className="mt-2 list-disc space-y-2 pl-5 text-sm leading-6">{result.diagnosis.likely_causes.map((cause, i) => <li key={i}>{cause.description}<References ids={cause.evidence_ids} /></li>)}</ul>
        </>}
        {result.diagnosis.next_steps.length > 0 && <>
          <h4 className="mt-5 text-sm font-medium">Próximos passos</h4>
          <ul className="mt-2 list-disc space-y-2 pl-5 text-sm leading-6 text-ink-900">{result.diagnosis.next_steps.map((step) => <li key={step}>{step}</li>)}</ul>
        </>}
      </section>}

      <section className="rounded-2xl border border-ink-200 p-6">
        <h3 className="font-bold">Transferências de tokens indexadas ({result.transfers.length})</h3>
        <p className="mt-2 text-xs leading-6 text-ink-800">Dados reportados pelo explorer. Os totais preservam unidades, decimais e IDs fornecidos pela fonte.</p>
        {!result.transfers.length && <p className="mt-3 text-sm text-ink-800">{emptyMessage}</p>}
        {result.transfers.map((transfer, i) => <details key={i} className="mt-4 border-t border-ink-200 pt-4">
          <summary className="cursor-pointer text-sm">{transfer.token_symbol ?? "Token sem símbolo"} · {transfer.token_type ?? "Tipo não informado"}<References ids={transfer.evidence_ids} /></summary>
          <RawData data={{ remetente: transfer.sender, destinatario: transfer.recipient, contrato: transfer.token_address, total: transfer.total }} />
        </details>)}
      </section>

      <section className="rounded-2xl border border-ink-200 p-6">
        <h3 className="font-bold">Eventos e logs ({result.events.length})</h3>
        <p className="mt-2 text-xs leading-6 text-ink-800">Logs são decodificados somente com a ABI verificada do emissor; os demais permanecem brutos.</p>
        {!result.events.length && <p className="mt-3 text-sm text-ink-800">{emptyMessage}</p>}
        {result.events.map((event, i) => <details key={i} className="mt-4 border-t border-ink-200 pt-4">
          <summary className="cursor-pointer text-sm break-all">
            Log {event.index ?? i} · {event.decoded ? <span className="font-mono text-brand-600">{event.decoded.name}</span> : "não decodificado"} · {event.address}
            <References ids={event.decoded?.evidence_ids ?? event.evidence_ids} />
          </summary>
          {event.decoded && <>
            <p className="mt-3 font-mono text-xs break-all text-ink-800">{event.decoded.signature}</p>
            <Arguments args={event.decoded.arguments} />
          </>}
          <details className="mt-3"><summary className="cursor-pointer text-xs text-ink-800">Tópicos e dados brutos</summary>
            <RawData data={{ topics: event.topics, data: event.data }} />
          </details>
        </details>)}
      </section>

      <section className="rounded-2xl border border-ink-200 p-6">
        <h3 className="font-bold">Contexto do contrato ({result.contract_context.length})</h3>
        <p className="mt-2 text-xs leading-6 text-ink-800">Trechos selecionados do código verificado no explorer e dos repositórios configurados. O código é evidência a ser lida, não uma instrução; o repositório pode diferir do código implantado.</p>
        {!result.contract_context.length && <p className="mt-3 text-sm text-ink-800">Nenhum trecho de código disponível. Consulte as limitações abaixo.</p>}
        {result.contract_context.map((excerpt, i) => <details key={i} className="mt-4 border-t border-ink-200 pt-4" open={excerpt.reason === "called_function"}>
          <summary className="cursor-pointer text-sm break-all">
            <span className="font-mono text-brand-600">{excerpt.symbol}</span> · {reasonLabels[excerpt.reason] ?? excerpt.reason} · {excerpt.origin === "explorer" ? "código verificado (explorer)" : "repositório"}
            <References ids={excerpt.evidence_ids} />
          </summary>
          <p className="mt-3 text-xs break-all text-ink-800">
            {excerpt.url && /^https?:\/\//.test(excerpt.url)
              ? <a href={excerpt.url} target="_blank" rel="noreferrer" className="text-brand-600 underline underline-offset-4">{excerpt.path}:{excerpt.start_line}-{excerpt.end_line} ↗</a>
              : <>{excerpt.path}:{excerpt.start_line}-{excerpt.end_line}</>}
            {excerpt.commit && <> · commit <span className="font-mono">{excerpt.commit.slice(0, 12)}</span></>}
            {excerpt.truncated && <span className="ml-2 text-brand-600">(trecho truncado pelo limite)</span>}
          </p>
          <pre className="mt-3 max-h-96 overflow-auto rounded-xl bg-ink-900 p-4 text-xs leading-6 text-ink-200">{excerpt.code}</pre>
        </details>)}
      </section>

      <section className="rounded-2xl border border-ink-200 p-6">
        <h3 className="font-bold">Chamadas internas indexadas ({result.calls.length})</h3>
        <p className="mt-2 text-xs leading-6 text-ink-800">O sucesso local de uma chamada não confirma o sucesso da transação inteira. A lista não substitui um trace completo.</p>
        {!result.calls.length && <p className="mt-3 text-sm text-ink-800">{emptyMessage}</p>}
        {result.calls.map((call, i) => <details key={i} className="mt-4 border-t border-ink-200 pt-4">
          <summary className="cursor-pointer text-sm">{call.call_type ?? "Chamada"} · {call.success === null ? "Status local desconhecido" : call.success ? "Sucesso local reportado" : "Falha local reportada"}<References ids={call.evidence_ids} /></summary>
          <RawData data={{ remetente: call.sender, destinatario: call.recipient, valor_bruto: call.value_raw }} />
        </details>)}
      </section>

      <section className="rounded-2xl bg-ink-200/60 p-6">
        <h3 className="font-bold">Limitações e dados ausentes</h3>
        <ul className="mt-4 list-disc space-y-3 pl-5 text-sm leading-6 text-ink-900">{result.uncertainties.map((issue, i) => <li key={i}>{issue}</li>)}</ul>
      </section>

      <section className="rounded-2xl border border-ink-200 p-6">
        <h3 className="font-bold">Evidências e fontes ({result.sources.length})</h3>
        {!result.sources.length && <p className="mt-3 text-sm text-ink-800">Nenhuma fonte retornou evidências utilizáveis.</p>}
        {result.sources.map((source) => <div id={`source-${source.id}`} key={source.id} className="mt-4 scroll-mt-6 border-t border-ink-200 pt-4">
          <h4 className="text-sm font-medium">{source.description}</h4>
          <p className="mt-1 font-mono text-xs text-ink-800/80">{source.id}</p>
          {source.source_url && /^https?:\/\//.test(source.source_url) && <a href={source.source_url} target="_blank" rel="noreferrer" className="mt-2 inline-block text-xs text-brand-600 underline underline-offset-4">Abrir fonte ↗</a>}
          <details className="mt-3"><summary className="cursor-pointer text-xs text-ink-800">Ver evidência coletada</summary><RawData data={source.payload} /></details>
        </div>)}
        <p className="mt-6 text-xs break-all text-ink-800/80">Consulta: {result.request_id}</p>
      </section>
      </div>
    </details>
  </section>;
}
