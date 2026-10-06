import type { ReactNode } from "react";

// Solidity-like types that make "name(...)" a signature rather than a Portuguese plural "(s)".
const TYPE = /^(address|bool|string|bytes\d*|u?int\d*|tuple|\(.*\))(\[\d*\])?$/;
// Heuristic tokens, used when the model did not mark code with backticks:
// signatures/calls, hex data, camelCase / snake_case identifiers.
const TOKEN = /\b[A-Za-z_$][\w$]*\([^()]*\)|0x[0-9a-fA-F]{6,}|\b[a-z][a-z0-9]*[A-Z][A-Za-z0-9]*\b|\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b/g;

function isCode(token: string): boolean {
  const call = token.match(/^([A-Za-z_$][\w$]*)\(([^()]*)\)$/);
  if (!call) return true;
  const [, name, inner] = call;
  if (inner.trim() === "") return true;
  if (/[A-Z_]/.test(name.slice(1))) return true;
  return inner.split(",").every((part) => TYPE.test(part.trim().split(/\s+/)[0]));
}

function Code({ children }: { children: string }) {
  return <code className="rounded-md bg-white px-1.5 py-0.5 font-mono text-[0.85em] text-brand-600 ring-1 ring-brand-0 [overflow-wrap:anywhere]">{children}</code>;
}

function highlight(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(TOKEN)) {
    const token = match[0];
    const index = match.index ?? 0;
    if (!isCode(token)) continue;
    if (index > last) nodes.push(text.slice(last, index));
    nodes.push(<Code key={`${keyPrefix}-${index}`}>{token}</Code>);
    last = index + token.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

/** Model-written text: `backticks` become inline code; obvious code tokens are detected too. */
export function RichText({ text }: { text: string }) {
  const parts = text.split(/(`[^`\n]+`)/g);
  return <>{parts.map((part, i) => part.startsWith("`") && part.endsWith("`") && part.length > 2
    ? <Code key={i}>{part.slice(1, -1)}</Code>
    : <span key={i}>{highlight(part, String(i))}</span>)}</>;
}
