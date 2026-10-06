"use client";

import type { SecretChange, SecretStatus } from "@/lib/api";

type Props = {
  id: string;
  label: string;
  hint: string;
  status: SecretStatus | undefined;
  change: SecretChange;
  onChange: (change: SecretChange) => void;
  // Variable used by "Usar variável de ambiente" (e.g. RPC_URL); LLM keys use <PROVIDER>_API_KEY.
  envVar: string | null;
  invalid?: boolean;
  disabled?: boolean;
};

function statusText(status: SecretStatus | undefined): string {
  if (!status?.configured) return status?.env_var ? `Não definida (${status.env_var} vazia)` : "Não definida";
  if (status.source === "env") return `Via variável ${status.env_var}`;
  return "Definida no arquivo local";
}

/** Write-only secret: the current value is never shown, only where it comes from. */
export function SecretField({ id, label, hint, status, change, onChange, envVar, invalid, disabled }: Props) {
  const editing = change.action === "set";
  const pending = change.action === "clear" ? "Será removida ao salvar"
    : change.action === "env" ? `Passará a usar ${envVar ?? "a variável de ambiente"} ao salvar`
    : null;
  return <div>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <label htmlFor={id} className="text-sm font-medium">{label}</label>
      <span className={`rounded-full px-2.5 py-0.5 text-xs ${status?.configured ? "bg-accent-100 text-ink-900 ring-1 ring-accent-500" : "bg-ink-200 text-ink-800"}`}>
        {pending ?? statusText(status)}
      </span>
    </div>
    {editing
      ? <input id={id} type="password" autoComplete="off" spellCheck={false} disabled={disabled} autoFocus
          value={change.value ?? ""} onChange={(event) => onChange({ action: "set", value: event.target.value })}
          placeholder="Novo valor" aria-invalid={invalid || undefined}
          className={`mt-2 w-full rounded-xl border bg-white px-4 py-2.5 font-mono text-sm focus:border-brand-600 ${invalid ? "border-brand-600 ring-2 ring-brand-0" : "border-ink-400"}`} />
      : <p id={id} className="mt-1 text-xs text-ink-800">{hint}</p>}
    <div className="mt-2 flex flex-wrap gap-2 text-xs">
      {editing || change.action !== "keep"
        ? <button type="button" disabled={disabled} onClick={() => onChange({ action: "keep" })} className="rounded-full border border-ink-400 px-3 py-1 hover:border-ink-900">Cancelar alteração</button>
        : <>
          <button type="button" disabled={disabled} onClick={() => onChange({ action: "set", value: "" })} className="rounded-full border border-ink-900 px-3 py-1 font-medium hover:bg-ink-900 hover:text-white">
            {status?.source === "file" ? "Substituir" : "Definir valor"}
          </button>
          {status?.source === "file" && <button type="button" disabled={disabled} onClick={() => onChange({ action: "clear" })} className="rounded-full border border-ink-400 px-3 py-1 hover:border-ink-900">Remover</button>}
          {status?.source !== "env" && <button type="button" disabled={disabled} onClick={() => onChange({ action: "env" })} className="rounded-full border border-ink-400 px-3 py-1 hover:border-ink-900">
            Usar variável de ambiente{envVar ? ` (${envVar})` : ""}
          </button>}
        </>}
    </div>
  </div>;
}
