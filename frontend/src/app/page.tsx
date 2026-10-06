"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { getNetwork, isTransactionHash, submitAnalysis, type AnalysisMode, type Network, type AnalysisResponse } from "@/lib/api";
import { AnalysisResult } from "@/components/analysis-result";
import { AnalysisContext } from "@/components/analysis-context";
import { AnalysisLoader } from "@/components/analysis-loader";
import { NewAnalysis } from "@/components/new-analysis";
import { SiteHeader } from "@/components/site-header";

const modes: { value: AnalysisMode; label: string; hint: string }[] = [
  { value: "developer", label: "Desenvolvedor", hint: "Calldata, código e depuração" },
  { value: "support", label: "Suporte", hint: "Linguagem simples e impacto para o cliente" },
  { value: "auditor", label: "Auditoria", hint: "Controle de acesso, confiança e riscos" },
];

const pillars = [
  { title: "O que aconteceu", text: "Dados da transação e eventos via Blockscout." },
  { title: "Qual era o estado", text: "Consultas de leitura à rede via RPC." },
  { title: "Por que aconteceu", text: "Contexto do código e raciocínio apoiado em evidências." },
];

export default function Home() {
  const [network, setNetwork] = useState<Network | null>(null);
  const [networkError, setNetworkError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [txHash, setTxHash] = useState("");
  const [mode, setMode] = useState<AnalysisMode>("developer");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<AnalysisResponse | null>(null);
  // Set when an analysis starts: switches the page to the response state.
  const [submitted, setSubmitted] = useState<{ hash: string; mode: AnalysisMode } | null>(null);
  const [failure, setFailure] = useState("");
  const [returning, setReturning] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const responseRef = useRef<HTMLDivElement>(null);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10_000);
    let active = true;
    getNetwork(controller.signal)
      .then((value) => { if (active) setNetwork(value); })
      .catch(() => { if (active) setNetworkError(true); })
      .finally(() => clearTimeout(timeout));
    return () => { active = false; clearTimeout(timeout); controller.abort(); };
  }, [attempt]);

  // Moving between states: focus follows the visible content (keyboard and screen readers).
  useEffect(() => {
    if (submitted) {
      window.scrollTo({ top: 0 });
      responseRef.current?.focus({ preventScroll: true });
    } else if (returning) {
      window.scrollTo({ top: 0 });
      inputRef.current?.focus({ preventScroll: true });
    }
  }, [submitted, returning]);

  async function run(hash: string, selectedMode: AnalysisMode) {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setSubmitted({ hash, mode: selectedMode });
    setResult(null);
    setFailure("");
    setBusy(true);
    try {
      const response = await submitAnalysis({ tx_hash: hash, mode: selectedMode }, controller.signal);
      if (!controller.signal.aborted) setResult(response);
    } catch (error) {
      if (controller.signal.aborted) return;
      setFailure(error instanceof Error && error.name !== "TypeError" && error.name !== "TimeoutError"
        ? error.message
        : "Não foi possível conectar ao serviço. Verifique o backend e tente novamente.");
    } finally {
      if (controllerRef.current === controller) setBusy(false);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("");
    const hash = txHash.trim();
    if (!isTransactionHash(hash)) {
      setMessage("Use um hash com 0x seguido de 64 caracteres hexadecimais.");
      return;
    }
    setReturning(false);
    void run(hash, mode);
  }

  function startNewAnalysis() {
    // Cancels a running analysis; after a failure the hash is kept so it can be fixed.
    controllerRef.current?.abort();
    controllerRef.current = null;
    setBusy(false);
    if (!failure) setTxHash("");
    setResult(null);
    setFailure("");
    setMessage("");
    setSubmitted(null);
    setReturning(true);
  }

  const modeLabel = modes.find((option) => option.value === submitted?.mode)?.label ?? "";

  return (
    <div className="min-h-screen">
      <SiteHeader />

      {submitted ? <>
        <AnalysisContext network={network} modeLabel={modeLabel} txHash={submitted.hash} onReset={startNewAnalysis} />
        <main className="mx-auto max-w-3xl px-6 pb-16">
          <div ref={responseRef} tabIndex={-1} className="outline-none">
            <h1 className="sr-only">Resultado da análise</h1>
            {busy && <AnalysisLoader />}
            {failure && <section role="alert" className="mt-8 animate-[fade-in_0.3s_ease-out] rounded-3xl border-2 border-ink-900 p-6 sm:p-8">
              <h2 className="text-lg font-bold">Não foi possível concluir a análise</h2>
              <p className="mt-2 text-sm leading-6 text-ink-800">{failure}</p>
              <div className="mt-5 flex flex-wrap gap-3">
                <button type="button" onClick={() => void run(submitted.hash, submitted.mode)}
                  className="rounded-full bg-accent-500 px-6 py-2.5 text-sm font-bold text-ink-900 hover:bg-accent-300">Tentar novamente</button>
                <button type="button" onClick={startNewAnalysis}
                  className="rounded-full border border-ink-900 px-6 py-2.5 text-sm font-bold hover:bg-ink-900 hover:text-white">Editar hash</button>
              </div>
            </section>}
            {result && <div className="animate-[fade-in_0.4s_ease-out]">
              <AnalysisResult result={result} />
              <NewAnalysis onReset={startNewAnalysis} />
            </div>}
          </div>
          <footer className="mt-14 border-t border-ink-200 pt-6 text-xs leading-5 text-ink-800">Somente leitura. Nenhuma carteira ou chave privada é necessária.</footer>
        </main>
      </> : <main className="mx-auto max-w-3xl animate-[fade-in_0.3s_ease-out] px-6 pb-16 pt-14 sm:pt-20">
        <p className="mb-5 text-sm font-medium text-brand-600">Assistente de transações EVM</p>
        <h1 className="max-w-xl text-4xl leading-tight font-bold tracking-tight sm:text-5xl">Entenda o que aconteceu<br className="hidden sm:block" /> na sua <span className="text-brand-600">transação</span>.</h1>
        <p className="mt-5 max-w-xl text-lg leading-7 text-ink-800">Uma explicação baseada em dados da blockchain e no código dos contratos, com fontes e limites claros.</p>

        <section className="mt-10 rounded-3xl border border-ink-200 bg-white p-6 shadow-[0_8px_30px_rgba(18,18,18,0.06)] sm:p-8" aria-label="Consultar transação">
          <div className="mb-7 flex flex-wrap items-center justify-between gap-3 border-b border-ink-200 pb-6">
            <div>
              <p className="mb-1 text-xs text-ink-800">Rede configurada</p>
              <p className="font-medium" aria-live="polite">{network?.name ?? (networkError ? "Rede indisponível" : "Consultando rede…")}</p>
            </div>
            {network && <span className="rounded-full bg-ink-200 px-3 py-1.5 font-mono text-xs text-ink-800">Chain ID {network.chain_id}</span>}
          </div>

          {networkError && (
            <div role="alert" className="mb-5 rounded-2xl border border-brand-200 bg-brand-0/40 p-4 text-sm leading-6">
              Não foi possível acessar o backend. Verifique se ele está em execução.
              <button type="button" onClick={() => { setNetworkError(false); setAttempt(attempt + 1); }} className="mt-2 block font-medium text-brand-600 underline underline-offset-4">Tentar novamente</button>
            </div>
          )}

          <form onSubmit={handleSubmit} noValidate>
            <fieldset className="mb-7">
              <legend className="mb-3 text-sm font-medium">Foco da explicação</legend>
              <div className="flex rounded-full bg-ink-200 p-1">
                {modes.map((option) => {
                  const selected = mode === option.value;
                  return <label key={option.value} className={`group relative flex-1 cursor-pointer rounded-full px-2 py-2 text-center text-sm transition-colors has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-brand-600 ${selected ? "bg-brand-600 font-bold text-white shadow-sm" : "font-medium text-ink-800 hover:text-ink-900"}`}>
                    <input type="radio" name="mode" value={option.value} checked={selected} onChange={() => setMode(option.value)} aria-describedby={`mode-hint-${option.value}`} className="sr-only" />
                    {option.label}
                    <span id={`mode-hint-${option.value}`} role="tooltip"
                      className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-2 w-max -translate-x-1/2 whitespace-nowrap rounded-lg bg-ink-900 px-3 py-1.5 text-xs font-normal text-white opacity-0 shadow-lg transition-opacity group-hover:opacity-100 group-has-[:focus-visible]:opacity-100">
                      {option.hint}
                      <span aria-hidden="true" className="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-ink-900" />
                    </span>
                  </label>;
                })}
              </div>
            </fieldset>
            <label htmlFor="tx-hash" className="mb-3 block text-sm font-medium">Hash da transação</label>
            <input ref={inputRef} id="tx-hash" name="tx-hash" type="text" value={txHash}
              onChange={(event) => { setTxHash(event.target.value); setMessage(""); }}
              placeholder="0x…" autoComplete="off" spellCheck={false} required
              aria-describedby="hash-hint ai-notice analysis-message"
              className="w-full rounded-xl border border-ink-400 bg-white px-4 py-3.5 font-mono text-sm placeholder:text-ink-400 focus:border-brand-600" />
            <p id="hash-hint" className="mt-3 text-xs leading-5 text-ink-800">O identificador começa com 0x e contém 64 caracteres após o prefixo.</p>
            <button type="submit" disabled={!network}
              className="mt-6 w-full rounded-full bg-accent-500 px-7 py-3 text-sm font-bold text-ink-900 transition-colors hover:bg-accent-300 disabled:opacity-40 sm:w-auto">
              Analisar transação
            </button>
            <p id="analysis-message" role="status" aria-live="polite" className={message ? "mt-5 text-sm leading-6 text-brand-600" : "sr-only"}>{message}</p>
          </form>
          <p id="ai-notice" className="mt-6 border-t border-ink-200 pt-5 text-xs leading-6 text-ink-800">A explicação por IA é gerada a partir das evidências coletadas e cita as fontes; fatos, decodificação e código continuam disponíveis mesmo sem IA configurada.</p>
        </section>

        <div className="mt-12 grid gap-4 text-sm sm:grid-cols-3">
          {pillars.map((pillar) => <div key={pillar.title} className="rounded-2xl bg-ink-200/60 p-5">
            <h2 className="mb-2 font-bold">{pillar.title}</h2>
            <p className="leading-6 text-ink-800">{pillar.text}</p>
          </div>)}
        </div>
        <footer className="mt-14 border-t border-ink-200 pt-6 text-xs leading-5 text-ink-800">Somente leitura. Nenhuma carteira ou chave privada é necessária.</footer>
      </main>}
    </div>
  );
}
