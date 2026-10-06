/** Closing call to action of the response state. */
export function NewAnalysis({ onReset }: { onReset: () => void }) {
  return <div className="mt-8 flex flex-col items-center gap-3 rounded-3xl bg-ink-900 px-6 py-8 text-center text-white">
    <p className="font-display text-lg font-bold">Quer entender outra transação?</p>
    <p className="max-w-md text-sm leading-6 text-ink-200">Volte ao formulário e informe um novo hash. O foco da explicação escolhido é mantido.</p>
    <button type="button" onClick={onReset}
      className="mt-2 rounded-full bg-accent-500 px-7 py-3 text-sm font-bold text-ink-900 transition-colors hover:bg-accent-300">
      Nova análise
    </button>
  </div>;
}
