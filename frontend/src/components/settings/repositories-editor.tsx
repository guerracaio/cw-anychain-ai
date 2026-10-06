"use client";

import type { RepositorySettings } from "@/lib/api";

type Props = {
  repositories: RepositorySettings[];
  onChange: (repositories: RepositorySettings[]) => void;
  invalidFields: Set<string>;
  disabled?: boolean;
};

const input = "w-full rounded-xl border bg-white px-3 py-2 text-sm focus:border-brand-600";

function border(invalid: boolean) {
  return invalid ? "border-brand-600 ring-2 ring-brand-0" : "border-ink-400";
}

/** Repositories scoped to the profile, with optional address → contract name mappings. */
export function RepositoriesEditor({ repositories, onChange, invalidFields, disabled }: Props) {
  function update(index: number, patch: Partial<RepositorySettings>) {
    onChange(repositories.map((repo, i) => (i === index ? { ...repo, ...patch } : repo)));
  }

  return <div className="space-y-4">
    {repositories.length === 0 && <p className="rounded-xl bg-ink-200/60 p-4 text-sm text-ink-800">
      Nenhum repositório. Sem eles, a análise usa só o código verificado no explorer.
    </p>}
    {repositories.map((repo, index) => {
      const prefix = `repositories.${index}`;
      return <fieldset key={index} disabled={disabled} className="rounded-2xl border border-ink-200 p-4">
        <legend className="sr-only">Repositório {index + 1}</legend>
        <div className="grid gap-3 sm:grid-cols-[1fr_10rem_auto] sm:items-end">
          <label className="block text-xs text-ink-800">URL (https://github.com/dono/repo)
            <input value={repo.url} onChange={(event) => update(index, { url: event.target.value })}
              placeholder="https://github.com/org/contracts" spellCheck={false}
              aria-invalid={invalidFields.has(`${prefix}.url`) || undefined}
              className={`mt-1 font-mono ${input} ${border(invalidFields.has(`${prefix}.url`))}`} />
          </label>
          <label className="block text-xs text-ink-800">Branch
            <input value={repo.branch} onChange={(event) => update(index, { branch: event.target.value })}
              placeholder="main" spellCheck={false}
              aria-invalid={invalidFields.has(`${prefix}.branch`) || undefined}
              className={`mt-1 font-mono ${input} ${border(invalidFields.has(`${prefix}.branch`))}`} />
          </label>
          <button type="button" onClick={() => onChange(repositories.filter((_, i) => i !== index))}
            className="rounded-full border border-ink-400 px-3 py-2 text-xs hover:border-ink-900">Remover</button>
        </div>
        <details className="mt-3" open={repo.contracts.length > 0}>
          <summary className="cursor-pointer text-xs text-brand-600">Mapear contratos sem nome no explorer ({repo.contracts.length})</summary>
          <div className="mt-3 space-y-2">
            {repo.contracts.map((contract, c) => {
              const field = `${prefix}.contracts.${c}`;
              return <div key={c} className="grid gap-2 sm:grid-cols-[1fr_12rem_auto]">
                <input value={contract.address} aria-label="Endereço do contrato" placeholder="0x…" spellCheck={false}
                  onChange={(event) => update(index, { contracts: repo.contracts.map((item, i) => i === c ? { ...item, address: event.target.value } : item) })}
                  className={`font-mono ${input} ${border(invalidFields.has(`${field}.address`))}`} />
                <input value={contract.name} aria-label="Nome do contrato no repositório" placeholder="NomeDoContrato" spellCheck={false}
                  onChange={(event) => update(index, { contracts: repo.contracts.map((item, i) => i === c ? { ...item, name: event.target.value } : item) })}
                  className={`font-mono ${input} ${border(invalidFields.has(`${field}.name`))}`} />
                <button type="button" onClick={() => update(index, { contracts: repo.contracts.filter((_, i) => i !== c) })}
                  className="rounded-full border border-ink-400 px-3 py-2 text-xs hover:border-ink-900">Remover</button>
              </div>;
            })}
            <button type="button" onClick={() => update(index, { contracts: [...repo.contracts, { address: "", name: "" }] })}
              className="text-xs font-medium text-brand-600 underline underline-offset-4">+ Adicionar mapeamento</button>
          </div>
        </details>
      </fieldset>;
    })}
    <button type="button" disabled={disabled || repositories.length >= 20}
      onClick={() => onChange([...repositories, { url: "", branch: "main", contracts: [] }])}
      className="rounded-full border border-ink-900 px-4 py-2 text-sm font-medium hover:bg-ink-900 hover:text-white disabled:opacity-40">
      + Adicionar repositório
    </button>
  </div>;
}
