import { createElement, useLayoutEffect, useRef, type ComponentPropsWithoutRef } from "react";

// A single native select with shared markup for truncating the selected label.
// Unsupported browsers ignore the custom button and retain the native picker.
export default function Select({ children, className = "", ...props }: Omit<ComponentPropsWithoutRef<"select">, "multiple" | "size">) {
  const ref = useRef<HTMLSelectElement>(null);
  useLayoutEffect(() => {
    const selected = ref.current?.selectedOptions[0], content = ref.current?.querySelector("selectedcontent");
    // Keep renamed options current even when their selected value is unchanged.
    if (selected && content) content.textContent = selected.label;
  }, [children, props.value]);
  return <select ref={ref} {...props} className={`text-input ${className}`.trim()}>
    <button type="button" className="select-trigger">{createElement("selectedcontent")}</button>
    {children}
  </select>;
}
