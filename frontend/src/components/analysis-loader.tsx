"use client";

import { useEffect, useState } from "react";

// Indicative stages of the backend pipeline. The API does not stream progress, so the
// highlighted stage follows elapsed time and the text says so.
const stages = [
  { after: 0, label: "Coletando a transação no explorer e no RPC" },
  { after: 4, label: "Resolvendo ABIs e decodificando chamada e eventos" },
  { after: 8, label: "Buscando o código-fonte dos contratos" },
  { after: 12, label: "Diagnosticando e lendo o estado relevante" },
  { after: 16, label: "Gerando a explicação com IA a partir das evidências" },
];

export function AnalysisLoader() {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const started = Date.now();
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, []);

  const current = stages.reduce((index, stage, i) => (elapsed >= stage.after ? i : index), 0);

  return <section className="mt-8 animate-[fade-in_0.3s_ease-out] rounded-3xl border border-ink-200 bg-white p-6 shadow-[0_8px_30px_rgba(18,18,18,0.06)] sm:p-8" aria-labelledby="loader-title" aria-busy="true">
    <div className="flex items-center gap-4">
      <span aria-hidden="true" className="relative flex size-12 shrink-0 items-center justify-center">
        <span className="absolute inset-0 rounded-full border-4 border-ink-200" />
        <span className="absolute inset-0 rounded-full border-4 border-transparent border-t-brand-600 border-r-accent-500 motion-safe:animate-spin" />
      </span>
      <div>
        <h2 id="loader-title" className="text-lg font-bold">Analisando a transação</h2>
        <p className="text-sm text-ink-800" aria-live="polite">{stages[current].label}…</p>
      </div>
    </div>

    <ol className="mt-6 space-y-3 text-sm">
      {stages.map((stage, i) => <li key={stage.label} className="flex items-center gap-3">
        <span aria-hidden="true" className={`flex size-5 shrink-0 items-center justify-center rounded-full text-[10px] font-bold ${
          i < current ? "bg-accent-500 text-ink-900" : i === current ? "bg-brand-600 text-white motion-safe:animate-pulse" : "bg-ink-200 text-ink-800"}`}>
          {i < current ? "✓" : i + 1}
        </span>
        <span className={i <= current ? "text-ink-900" : "text-ink-800/60"}>{stage.label}</span>
      </li>)}
    </ol>

    <div aria-hidden="true" className="mt-8 space-y-3">
      <div className="h-4 w-3/4 rounded-full bg-ink-200 motion-safe:animate-pulse" />
      <div className="h-4 w-full rounded-full bg-ink-200 motion-safe:animate-pulse" />
      <div className="h-4 w-5/6 rounded-full bg-ink-200 motion-safe:animate-pulse" />
    </div>

    <p className="mt-6 text-xs leading-5 text-ink-800">
      {elapsed}s · As etapas são indicativas. Análises com explicação por IA costumam levar de 20 s a 1 min.
    </p>
  </section>;
}
