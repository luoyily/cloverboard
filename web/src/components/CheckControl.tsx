import type { ReactNode } from "react";
import Checkbox from "./Checkbox";

interface CheckControlProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  children: ReactNode;
  title?: string;
  label?: string;
  disabled?: boolean;
}

export default function CheckControl({ checked, onChange, children, title, label, disabled }: CheckControlProps) {
  return (
    <label className="check-control" title={title}>
      <Checkbox checked={checked} disabled={disabled} label={label ?? (typeof children === "string" ? children : undefined)} onChange={onChange} />
      <span className="check-label">{children}</span>
    </label>
  );
}
