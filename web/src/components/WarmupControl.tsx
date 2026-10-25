import { useEffect, useId, useState } from "react";

export default function WarmupControl({ step, onChange }: { step: number; onChange: (step: number) => void }) {
  const [draft, setDraft] = useState(String(step)), [error, setError] = useState("");
  const errorId = useId();
  useEffect(() => { setDraft(String(step)); setError(""); }, [step]);
  function commit() {
    const number = Number(draft);
    if (!draft.trim() || !Number.isSafeInteger(number)) { setError("Enter a whole step within the supported range."); return; }
    if (number !== step) onChange(number);
    setError("");
  }
  return <>
    <div className="setting-row"><span className="field-label">From step</span><div className="setting-value warmup-control" onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) commit(); }}>
      <input className="text-input" type="number" step={1} aria-label="Fit from step" aria-invalid={Boolean(error)} aria-describedby={error ? errorId : undefined} value={draft} onChange={(event) => { setDraft(event.target.value); setError(""); }} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); commit(); } }} />
    </div></div>
    {error && <p className="field-help data-error" role="alert" id={errorId}>{error}</p>}
  </>;
}
