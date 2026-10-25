import { CheckIcon, MinusIcon } from "@phosphor-icons/react";

interface CheckboxProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  indeterminate?: boolean;
  disabled?: boolean;
  id?: string;
  label?: string;
}

export default function Checkbox({ checked, onChange, indeterminate = false, disabled, id, label }: CheckboxProps) {
  const Icon = indeterminate ? MinusIcon : CheckIcon;
  return <span className="run-toggle">
    <input type="checkbox" id={id} aria-label={label} aria-checked={indeterminate ? "mixed" : undefined}
      checked={checked} disabled={disabled} ref={(element) => { if (element) element.indeterminate = indeterminate; }}
      onChange={(event) => onChange(event.target.checked)} />
    <Icon size={12} weight="regular" aria-hidden="true" />
  </span>;
}
