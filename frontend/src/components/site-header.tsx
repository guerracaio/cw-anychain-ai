import Link from "next/link";

type Props = {
  current?: "settings";
  // Matches the page's content column so brand and actions line up with it.
  width?: "narrow" | "wide";
};

/** Light top bar: brand on the left, settings entry on the right. */
export function SiteHeader({ current, width = "narrow" }: Props) {
  return <header className="border-b border-ink-200 bg-white">
    <div className={`mx-auto flex items-center justify-between px-6 py-4 ${width === "wide" ? "max-w-5xl" : "max-w-3xl"}`}>
      <Link href="/" className="flex items-center gap-2.5 font-display text-lg font-medium tracking-tight text-ink-900" aria-label="Anychain, início">
        <span aria-hidden="true" className="flex size-7 items-center justify-center rounded-full bg-brand-600 text-xs font-bold text-white">A</span>
        <span>any<span className="font-bold">chain</span></span>
      </Link>
      <Link href="/settings" aria-current={current === "settings" ? "page" : undefined}
        className={`flex items-center gap-2 rounded-full px-3 py-1.5 text-sm transition-colors ${current === "settings" ? "bg-brand-0/60 font-medium text-brand-600" : "text-ink-800 hover:bg-ink-200/60 hover:text-ink-900"}`}>
        <svg aria-hidden="true" viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h0a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h0a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
        Configurações
      </Link>
    </div>
  </header>;
}
