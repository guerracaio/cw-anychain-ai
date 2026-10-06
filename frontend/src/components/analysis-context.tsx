import Link from "next/link";
import type { Network } from "@/lib/api";

type Props = {
  network: Network | null;
  modeLabel: string;
  txHash: string;
  onReset: () => void;
};

/** Compact header of the response state: only what identifies the analysis. */
export function AnalysisContext({ network, modeLabel, txHash, onReset }: Props) {
  return <div className="sticky top-0 z-20 border-b border-ink-200 bg-white/90 backdrop-blur">
    <div className="mx-auto flex max-w-3xl flex-wrap items-center gap-x-6 gap-y-3 px-6 py-3">
      <dl className="flex min-w-0 flex-1 flex-wrap items-center gap-x-6 gap-y-2 text-sm">
        <div className="min-w-0">
          <dt className="text-xs text-ink-800">Rede</dt>
          <dd className="font-medium">
            <Link href="/settings" title="Alterar rede nas configurações" className="underline decoration-ink-400 underline-offset-4 hover:decoration-brand-600">
              {network ? <>{network.name} <span className="text-xs text-ink-800">· Chain ID {network.chain_id}</span></> : "—"}
            </Link>
          </dd>
        </div>
        <div>
          <dt className="text-xs text-ink-800">Modo</dt>
          <dd><span className="rounded-full bg-brand-600 px-2.5 py-0.5 text-xs font-bold text-white">{modeLabel}</span></dd>
        </div>
        <div className="min-w-0 flex-1 basis-56">
          <dt className="text-xs text-ink-800">Transação</dt>
          <dd className="truncate font-mono text-xs" title={txHash}>{txHash}</dd>
        </div>
      </dl>
      <button type="button" onClick={onReset}
        className="shrink-0 rounded-full bg-accent-500 px-5 py-2 text-sm font-bold text-ink-900 transition-colors hover:bg-accent-300">
        Nova análise
      </button>
    </div>
  </div>;
}
