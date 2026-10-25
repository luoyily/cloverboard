import type { ReactNode } from "react";

interface Segment<T extends string> { value: T; label: ReactNode; title?: string }

export default function SegmentedControl<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: Segment<T>[]; onChange: (value: T) => void }) {
  return <div className="segmented-control" role="group" aria-label={label}>
    {options.map((option) => <button type="button" key={option.value} aria-pressed={value === option.value} title={option.title} onClick={() => onChange(option.value)}>{option.label}</button>)}
  </div>;
}
