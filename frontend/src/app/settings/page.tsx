"use client";

import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import Link from "next/link";
import { SiteHeader } from "@/components/site-header";
import { RepositoriesEditor } from "@/components/settings/repositories-editor";
import { SecretField } from "@/components/settings/secret-field";
import {
  getAdminToken, setAdminToken, settingsApi, SettingsRequestError,
  type ConnectionCheck, type ProfileSettings, type ProfileSummary, type ProfileUpdate,
  type RepositorySettings, type SecretChange, type SecretName, type SettingsStatus,
} from "@/lib/api";

type Form = {
  id: string;
  name: string;
  chainId: string;
  currency: string;
  decimals: string;
  explorerUrl: string;
  repositories: RepositorySettings[];
  provider: string;
  model: string;
  thinking: string;
  inputPrice: string;
  outputPrice: string;
  secrets: Record<SecretName, SecretChange>;
};

const KEEP: Record<SecretName, SecretChange> = {
  rpc_url: { action: "keep" }, github_token: { action: "keep" }, llm_api_key: { action: "keep" },
};
const EMPTY: Form = {
  id: "", name: "", chainId: "", currency: "ETH", decimals: "18", explorerUrl: "", repositories: [],
  provider: "", model: "", thinking: "low", inputPrice: "", outputPrice: "", secrets: KEEP,
};
const PROVIDERS = [
  { value: "", label: "Nenhum" },
  { value: "openai", label: "OpenAI" },
  { value: "gemini", label: "Gemini" },
];
const MODELS: Record<string, string[]> = {
  openai: ["gpt-5-mini", "gpt-5", "gpt-4.1-mini"],
  gemini: ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"],
};
const THINKING = [
  { value: "minimal", label: "Mínimo" },
  { value: "low", label: "Baixo" },
  { value: "medium", label: "Médio" },
  { value: "high", label: "Alto" },
  { value: "", label: "Sem raciocínio" },
];
const CHECK_CODES: Record<string, string> = {
  ok: "conectado",
  configured: "provedor, modelo e chave configurados (o modelo não é chamado no teste)",
  not_configured: "não configurado",
  chain_mismatch: "o RPC responde por outra rede (chain ID diferente)",
  timeout: "tempo limite excedido",
  connection_failed: "falha de conexão",
  not_found: "não encontrado (confira URL e branch)",
  access_denied: "acesso negado ou limite do GitHub (configure o token)",
  temporarily_unavailable: "indisponível ou limite de requisições",
  rpc_error: "o RPC recusou a consulta",
  invalid_explorer_response: "resposta inesperada: confirme que é um Blockscout",
  http_error: "erro HTTP",
  llm_not_configured: "nenhum provedor escolhido",
  llm_model_not_configured: "modelo não informado",
  llm_api_key_missing: "chave de API ausente",
  llm_provider_not_supported: "provedor não suportado",
};

type Loaded = { status: SettingsStatus; list: ProfileSummary[] } | { error: string };

async function loadSettings(): Promise<Loaded> {
  try {
    const [status, list] = await Promise.all([settingsApi.status(), settingsApi.profiles()]);
    return { status, list };
  } catch (error) {
    return { error: error instanceof SettingsRequestError ? error.code : "unreachable" };
  }
}

function toForm(view: ProfileSettings): Form {
  const text = (value: string | number | null) => (value === null ? "" : String(value));
  return {
    id: view.id,
    name: text(view.network.name),
    chainId: text(view.network.chain_id),
    currency: text(view.network.native_currency),
    decimals: text(view.network.native_decimals),
    explorerUrl: text(view.explorer.base_url),
    repositories: view.repositories.map((repo) => ({ ...repo, contracts: repo.contracts.map((c) => ({ ...c })) })),
    provider: text(view.llm.provider),
    model: text(view.llm.model),
    thinking: text(view.llm.thinking_level),
    inputPrice: text(view.llm.input_price_per_million),
    outputPrice: text(view.llm.output_price_per_million),
    secrets: KEEP,
  };
}

function toUpdate(form: Form, copyFrom: string | null): ProfileUpdate {
  const integer = (value: string) => (value.trim() === "" ? null : Number(value));
  const decimal = (value: string) => (value.trim() === "" ? null : Number(value.replace(",", ".")));
  const optional = (value: string) => (value.trim() === "" ? null : value.trim());
  const secrets = Object.fromEntries(
    Object.entries(form.secrets).filter(([, change]) => change.action !== "keep"),
  ) as ProfileUpdate["secrets"];
  return {
    network: {
      name: optional(form.name), chain_id: integer(form.chainId),
      native_currency: optional(form.currency), native_decimals: integer(form.decimals),
    },
    explorer: { base_url: optional(form.explorerUrl) },
    repositories: form.repositories.map((repo) => ({
      url: repo.url.trim(), branch: repo.branch.trim() || "main",
      contracts: repo.contracts.map((c) => ({ address: c.address.trim(), name: c.name.trim() })),
    })),
    llm: {
      provider: optional(form.provider), model: optional(form.model), thinking_level: form.thinking || "none",
      input_price_per_million: decimal(form.inputPrice), output_price_per_million: decimal(form.outputPrice),
    },
    secrets,
    copy_from: copyFrom,
  };
}

function Section({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return <section className="border-t border-ink-200 pt-6 first:border-t-0 first:pt-0">
    <h2 className="text-lg font-bold">{title}</h2>
    <p className="mt-1 mb-4 text-sm text-ink-800">{description}</p>
    <div className="space-y-4">{children}</div>
  </section>;
}

function Field({ label, hint, envVar, invalid, children }: { label: string; hint?: string; envVar?: string; invalid?: boolean; children: ReactNode }) {
  return <label className="block">
    <span className="flex flex-wrap items-center gap-2 text-sm font-medium">
      {label}
      {envVar && <span className="rounded-full bg-ink-200 px-2 py-0.5 font-mono text-[11px] font-normal text-ink-800" title="Valor vindo do .env enquanto não for alterado aqui">via {envVar}</span>}
      {invalid && <span className="text-xs font-normal text-brand-600">valor inválido</span>}
    </span>
    {children}
    {hint && <span className="mt-1 block text-xs text-ink-800">{hint}</span>}
  </label>;
}

function inputClass(invalid?: boolean, mono = false) {
  return `mt-2 w-full rounded-xl border bg-white px-4 py-2.5 text-sm focus:border-brand-600 ${mono ? "font-mono" : ""} ${invalid ? "border-brand-600 ring-2 ring-brand-0" : "border-ink-400"}`;
}

function Pills({ name, options, value, onChange }: { name: string; options: { value: string; label: string }[]; value: string; onChange: (value: string) => void }) {
  return <div className="mt-2 flex flex-wrap rounded-full bg-ink-200 p-1" role="radiogroup">
    {options.map((option) => {
      const selected = value === option.value;
      return <label key={option.value || "none"} className={`flex-1 cursor-pointer rounded-full px-3 py-1.5 text-center text-sm whitespace-nowrap transition-colors has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-brand-600 ${selected ? "bg-brand-600 font-bold text-white" : "text-ink-800 hover:text-ink-900"}`}>
        <input type="radio" name={name} value={option.value} checked={selected} onChange={() => onChange(option.value)} className="sr-only" />
        {option.label}
      </label>;
    })}
  </div>;
}

export default function SettingsPage() {
  const [status, setStatus] = useState<SettingsStatus | null>(null);
  const [profiles, setProfiles] = useState<ProfileSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [view, setView] = useState<ProfileSettings | null>(null);
  const [form, setForm] = useState<Form>(EMPTY);
  const [creating, setCreating] = useState(false);
  const [copyFrom, setCopyFrom] = useState("");
  const [invalid, setInvalid] = useState<Set<string>>(new Set());
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [checks, setChecks] = useState<ConnectionCheck[] | null>(null);
  const [busy, setBusy] = useState<"save" | "test" | "activate" | "delete" | null>(null);
  const [token, setToken] = useState("");
  const [loadError, setLoadError] = useState(false);

  const apply = useCallback((result: Loaded, focus?: string) => {
    if ("error" in result) {
      if (result.error === "settings_unavailable") {
        setStatus({ available: false, writable: false, admin_required: false, active: null });
      } else {
        setLoadError(true);
      }
      return;
    }
    // sessionStorage exists only in the browser: read it after the first render.
    setToken((value) => value || getAdminToken());
    setStatus(result.status);
    setProfiles(result.list);
    setLoadError(false);
    setSelected(focus ?? result.list.find((p) => p.active)?.id ?? result.list[0]?.id ?? null);
  }, []);

  const refresh = useCallback(async (focus?: string) => apply(await loadSettings(), focus), [apply]);

  useEffect(() => {
    let active = true;
    loadSettings().then((result) => { if (active) apply(result); });
    return () => { active = false; };
  }, [apply]);

  useEffect(() => {
    if (!selected || creating) return;
    let active = true;
    settingsApi.profile(selected)
      .then((data) => { if (active) { setView(data); setForm(toForm(data)); setInvalid(new Set()); setChecks(null); } })
      .catch(() => { if (active) setNotice({ kind: "error", text: "Não foi possível carregar o perfil." }); });
    return () => { active = false; };
  }, [selected, creating]);

  function patch(values: Partial<Form>) {
    setForm((current) => ({ ...current, ...values }));
  }

  function setSecret(name: SecretName, change: SecretChange) {
    setForm((current) => ({ ...current, secrets: { ...current.secrets, [name]: change } }));
  }

  function fail(error: unknown) {
    if (error instanceof SettingsRequestError) {
      setInvalid(new Set(error.code === "invalid_profile_id" ? ["network.id"] : error.fields));
      setNotice({ kind: "error", text: error.message });
    } else {
      setNotice({ kind: "error", text: "Não foi possível falar com o backend." });
    }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const id = creating ? form.id.trim() : selected;
    if (!id) return;
    setBusy("save");
    setNotice(null);
    setInvalid(new Set());
    try {
      const saved = await settingsApi.save(id, toUpdate(form, creating && copyFrom ? copyFrom : null));
      const wasActive = profiles.find((p) => p.id === id)?.active;
      setCreating(false);
      setView(saved);
      setForm(toForm(saved));
      await refresh(id);
      setNotice({ kind: "ok", text: wasActive ? "Configurações salvas e aplicadas às próximas análises." : "Perfil salvo." });
    } catch (error) {
      fail(error);
    } finally {
      setBusy(null);
    }
  }

  async function run(kind: "test" | "activate" | "delete") {
    if (!selected) return;
    setBusy(kind);
    setNotice(null);
    try {
      if (kind === "test") {
        setChecks((await settingsApi.test(selected)).checks);
      } else if (kind === "activate") {
        await settingsApi.activate(selected);
        await refresh(selected);
        setNotice({ kind: "ok", text: "Perfil ativado. As próximas análises usam esta rede." });
      } else if (window.confirm("Excluir este perfil? O arquivo local será apagado.")) {
        await settingsApi.remove(selected);
        await refresh();
        setNotice({ kind: "ok", text: "Perfil excluído." });
      }
    } catch (error) {
      fail(error);
    } finally {
      setBusy(null);
    }
  }

  function startCreate() {
    setCreating(true);
    setSelected(null);
    setView(null);
    setForm(EMPTY);
    setCopyFrom("");
    setInvalid(new Set());
    setChecks(null);
    setNotice(null);
  }

  const readOnly = !status?.writable || (status.admin_required && !token);
  const current = profiles.find((p) => p.id === selected);
  const secret = (name: SecretName) => view?.secrets[name];
  const bound = (field: string) => (creating ? undefined : view?.env_bound[field]);

  return <div className="min-h-screen">
    <SiteHeader current="settings" width="wide" />
    <main className="mx-auto max-w-5xl px-6 pb-24 pt-10">
      <Link href="/" className="text-sm text-brand-600 underline underline-offset-4">← Voltar para a análise</Link>
      <h1 className="mt-4 text-3xl font-bold tracking-tight">Configurações</h1>
      <p className="mt-2 max-w-2xl text-ink-800">Perfis de rede com explorer, RPC, repositórios e LLM. Ficam em <code className="font-mono text-sm">config/local/</code> (fora do Git) e o perfil ativo vale para as próximas análises, sem reiniciar o backend.</p>

      {loadError && <p role="alert" className="mt-6 rounded-2xl border-2 border-ink-900 p-4 text-sm">Não foi possível acessar o backend. Verifique se ele está em execução.</p>}
      {status && !status.available && <p className="mt-6 rounded-2xl bg-ink-200/60 p-4 text-sm">As configurações não podem ser editadas nesta execução do backend.</p>}
      {status?.available && !status.writable && <p className="mt-6 rounded-2xl bg-ink-200/60 p-4 text-sm">Somente leitura: sem <code className="font-mono">ADMIN_TOKEN</code>, as configurações só podem ser alteradas a partir da máquina do backend.</p>}
      {status?.admin_required && <form className="mt-6 flex flex-wrap items-end gap-3 rounded-2xl border border-brand-200 bg-brand-0/30 p-4"
        onSubmit={(event) => { event.preventDefault(); setAdminToken(token); setNotice({ kind: "ok", text: "Token guardado nesta aba do navegador." }); }}>
        <label className="min-w-64 flex-1 text-sm font-medium">Token de administrador
          <input type="password" value={token} onChange={(event) => setToken(event.target.value)} autoComplete="off" className={inputClass()} />
        </label>
        <button type="submit" className="rounded-full bg-ink-900 px-5 py-2.5 text-sm font-bold text-white">Usar token</button>
      </form>}

      {status?.available && <div className="mt-8 grid gap-6 md:grid-cols-[15rem_1fr]">
        <aside aria-label="Perfis" className="space-y-2">
          {profiles.map((profile) => <button key={profile.id} type="button"
            onClick={() => { setCreating(false); setSelected(profile.id); setNotice(null); }}
            aria-current={profile.id === selected && !creating ? "true" : undefined}
            className={`w-full rounded-2xl border px-4 py-3 text-left transition-colors ${profile.id === selected && !creating ? "border-brand-600 bg-brand-0/30" : "border-ink-200 hover:border-brand-200"}`}>
            <span className="flex items-center justify-between gap-2">
              <span className="truncate font-medium">{profile.name}</span>
              {profile.active && <span className="shrink-0 rounded-full bg-accent-500 px-2 py-0.5 text-[11px] font-bold">Ativo</span>}
            </span>
            <span className="mt-0.5 block font-mono text-xs text-ink-800">{profile.id}{profile.chain_id !== null ? ` · ${profile.chain_id}` : ""}</span>
            {!profile.valid && <span className="mt-1 block text-xs text-brand-600">Configuração inválida</span>}
          </button>)}
          <button type="button" onClick={startCreate} disabled={readOnly}
            className={`w-full rounded-2xl border border-dashed px-4 py-3 text-sm font-medium transition-colors disabled:opacity-40 ${creating ? "border-brand-600 text-brand-600" : "border-ink-400 hover:border-ink-900"}`}>
            + Novo perfil
          </button>
        </aside>

        {(view || creating) && <form onSubmit={save} noValidate className="min-w-0 rounded-3xl border border-ink-200 bg-white shadow-[0_8px_30px_rgba(18,18,18,0.06)]">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-200 p-6">
            <div>
              <p className="text-xs text-ink-800">{creating ? "Novo perfil" : "Perfil"}</p>
              <p className="font-display text-xl font-bold">{creating ? (form.name || "Sem nome") : current?.name}</p>
            </div>
            {!creating && <div className="flex flex-wrap gap-2">
              {current?.active
                ? <span className="rounded-full bg-accent-500 px-3 py-1.5 text-sm font-bold">Perfil ativo</span>
                : <button type="button" disabled={readOnly || busy !== null} onClick={() => void run("activate")}
                    className="rounded-full bg-ink-900 px-4 py-1.5 text-sm font-bold text-white disabled:opacity-40">{busy === "activate" ? "Ativando…" : "Ativar"}</button>}
              <button type="button" disabled={readOnly || busy !== null || current?.active} onClick={() => void run("delete")}
                title={current?.active ? "Ative outro perfil antes de excluir este" : undefined}
                className="rounded-full border border-ink-400 px-4 py-1.5 text-sm hover:border-ink-900 disabled:opacity-40">Excluir</button>
            </div>}
          </div>

          <fieldset disabled={readOnly} className="space-y-8 p-6">
            <Section title="Rede" description="Identificação da rede EVM. O chain ID também confere se o RPC aponta para a rede certa.">
              {creating && <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Identificador" hint="Letras minúsculas, números e hífen. Não pode ser alterado depois." invalid={invalid.has("network.id")}>
                  <input value={form.id} onChange={(event) => patch({ id: event.target.value.toLowerCase() })} placeholder="minha-rede" className={inputClass(invalid.has("network.id"), true)} />
                </Field>
                <Field label="Copiar de" hint="Começa com limites e segredos de outro perfil.">
                  <select value={copyFrom} onChange={(event) => setCopyFrom(event.target.value)} className={inputClass()}>
                    <option value="">Perfil vazio</option>
                    {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </select>
                </Field>
              </div>}
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Nome" invalid={invalid.has("network.name")}>
                  <input value={form.name} onChange={(event) => patch({ name: event.target.value })} placeholder="Ethereum Mainnet" className={inputClass(invalid.has("network.name"))} />
                </Field>
                <Field label="Chain ID" invalid={invalid.has("network.chain_id")}>
                  <input inputMode="numeric" value={form.chainId} onChange={(event) => patch({ chainId: event.target.value.replace(/\D/g, "") })} placeholder="1" className={inputClass(invalid.has("network.chain_id"), true)} />
                </Field>
                <Field label="Moeda nativa" invalid={invalid.has("network.native_currency")}>
                  <input value={form.currency} onChange={(event) => patch({ currency: event.target.value })} placeholder="ETH" className={inputClass(invalid.has("network.native_currency"))} />
                </Field>
                <Field label="Decimais da moeda" invalid={invalid.has("network.native_decimals")}>
                  <input inputMode="numeric" value={form.decimals} onChange={(event) => patch({ decimals: event.target.value.replace(/\D/g, "") })} className={inputClass(invalid.has("network.native_decimals"), true)} />
                </Field>
              </div>
            </Section>

            <Section title="Explorer e RPC" description="Blockscout mostra o que aconteceu; o RPC lê o estado da rede (somente leitura).">
              <Field label="URL do Blockscout" hint="Ex.: https://eth.blockscout.com — sem credenciais." invalid={invalid.has("explorer.base_url")}>
                <input value={form.explorerUrl} onChange={(event) => patch({ explorerUrl: event.target.value })} placeholder="https://…" spellCheck={false} className={inputClass(invalid.has("explorer.base_url"), true)} />
              </Field>
              <SecretField id="rpc-url" label="URL do RPC" envVar="RPC_URL" status={secret("rpc_url")} change={form.secrets.rpc_url}
                onChange={(change) => setSecret("rpc_url", change)} invalid={invalid.has("rpc.url")} disabled={readOnly}
                hint="Pode conter a chave do provedor; nunca é exibida nem enviada ao navegador. RPCs públicos guardam só estado recente." />
            </Section>

            <Section title="Repositórios de contratos" description="Código-fonte e regras de negócio usados como contexto. Só os repositórios listados aqui são consultados.">
              <RepositoriesEditor repositories={form.repositories} onChange={(repositories) => patch({ repositories })} invalidFields={invalid} disabled={readOnly} />
              <SecretField id="github-token" label="Token do GitHub" envVar="GITHUB_TOKEN" status={secret("github_token")} change={form.secrets.github_token}
                onChange={(change) => setSecret("github_token", change)} disabled={readOnly}
                hint="Opcional; leitura de repositórios públicos basta. Sem ele, o GitHub limita a 60 requisições por hora." />
            </Section>

            <Section title="LLM" description="Modelo que escreve a explicação a partir das evidências. Sem provedor, a análise mostra só os dados.">
              <div>
                <span className="text-sm font-medium">Provedor{bound("llm.provider") && <span className="ml-2 rounded-full bg-ink-200 px-2 py-0.5 font-mono text-[11px] font-normal text-ink-800">via {bound("llm.provider")}</span>}</span>
                <Pills name="provider" options={PROVIDERS} value={form.provider} onChange={(provider) => patch({ provider, model: MODELS[provider]?.includes(form.model) ? form.model : MODELS[provider]?.[0] ?? "" })} />
              </div>
              {form.provider && <>
                <Field label="Modelo" envVar={bound("llm.model")} invalid={invalid.has("llm.model")} hint="Confira os ids e preços atuais no site do provedor.">
                  <input list="model-options" value={form.model} onChange={(event) => patch({ model: event.target.value })} spellCheck={false} className={inputClass(invalid.has("llm.model"), true)} />
                  <datalist id="model-options">{(MODELS[form.provider] ?? []).map((model) => <option key={model} value={model} />)}</datalist>
                </Field>
                <SecretField id="llm-key" label="Chave de API" envVar={`${form.provider.toUpperCase()}_API_KEY`} status={secret("llm_api_key")} change={form.secrets.llm_api_key}
                  onChange={(change) => setSecret("llm_api_key", change)} invalid={invalid.has("llm.api_key")} disabled={readOnly}
                  hint={`Sem chave no arquivo, o backend usa ${form.provider.toUpperCase()}_API_KEY do .env.`} />
                <div>
                  <span className="text-sm font-medium">Raciocínio{bound("llm.thinking_level") && <span className="ml-2 rounded-full bg-ink-200 px-2 py-0.5 font-mono text-[11px] font-normal text-ink-800">via {bound("llm.thinking_level")}</span>}</span>
                  <Pills name="thinking" options={THINKING} value={form.thinking} onChange={(thinking) => patch({ thinking })} />
                  <span className="mt-1 block text-xs text-ink-800">Menos raciocínio é mais rápido e barato. Use “Sem raciocínio” para modelos que não suportam.</span>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Preço de entrada (US$ / 1M tokens)" envVar={bound("llm.input_price_per_million")} invalid={invalid.has("llm.input_price_per_million")} hint="Opcional; só para estimar custo.">
                    <input inputMode="decimal" value={form.inputPrice} onChange={(event) => patch({ inputPrice: event.target.value })} placeholder="0.25" className={inputClass(invalid.has("llm.input_price_per_million"), true)} />
                  </Field>
                  <Field label="Preço de saída (US$ / 1M tokens)" envVar={bound("llm.output_price_per_million")} invalid={invalid.has("llm.output_price_per_million")}>
                    <input inputMode="decimal" value={form.outputPrice} onChange={(event) => patch({ outputPrice: event.target.value })} placeholder="2.00" className={inputClass(invalid.has("llm.output_price_per_million"), true)} />
                  </Field>
                </div>
              </>}
            </Section>
          </fieldset>

          {checks && <div className="mx-6 mb-6 rounded-2xl bg-ink-200/60 p-4" aria-live="polite">
            <h2 className="text-sm font-bold">Teste de conexão (perfil salvo)</h2>
            <ul className="mt-3 space-y-2 text-sm">
              {checks.map((check) => <li key={check.id} className="flex items-start gap-3">
                <span aria-hidden="true" className={`mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${check.ok === true ? "bg-accent-500" : check.ok === false ? "bg-ink-900 text-white" : "bg-white text-ink-800 ring-1 ring-ink-400"}`}>
                  {check.ok === true ? "✓" : check.ok === false ? "✕" : "–"}
                </span>
                <span><span className="font-medium">{check.label}</span>: {CHECK_CODES[check.code] ?? check.code}</span>
              </li>)}
            </ul>
          </div>}

          <div className="sticky bottom-0 flex flex-wrap items-center justify-between gap-3 rounded-b-3xl border-t border-ink-200 bg-white/95 p-4 backdrop-blur sm:px-6">
            <p role="status" aria-live="polite" className={`text-sm ${notice?.kind === "error" ? "text-brand-600" : "text-ink-800"}`}>{notice?.text}</p>
            <div className="flex gap-2">
              {!creating && <button type="button" disabled={readOnly || busy !== null} onClick={() => void run("test")}
                className="rounded-full border border-ink-900 px-5 py-2.5 text-sm font-bold hover:bg-ink-900 hover:text-white disabled:opacity-40">{busy === "test" ? "Testando…" : "Testar conexão"}</button>}
              <button type="submit" disabled={readOnly || busy !== null}
                className="rounded-full bg-accent-500 px-6 py-2.5 text-sm font-bold text-ink-900 hover:bg-accent-300 disabled:opacity-40">{busy === "save" ? "Salvando…" : creating ? "Criar perfil" : "Salvar"}</button>
            </div>
          </div>
        </form>}
      </div>}
    </main>
  </div>;
}
