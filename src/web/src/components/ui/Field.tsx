import { useId, type InputHTMLAttributes } from "react";
import { Icon } from "./Icon.tsx";

export type FieldProps = Omit<InputHTMLAttributes<HTMLInputElement>, "aria-invalid"> & {
  label: string;
  hint?: string;
  /** An error names the problem and the way out. It sets aria-invalid and is announced. */
  error?: string;
};

export function Field({ label, hint, error, id, className, "aria-describedby": describedBy, ...rest }: FieldProps) {
  const generated = useId();
  const inputId = id ?? generated;
  const hintId = `${inputId}-hint`;
  const errorId = `${inputId}-error`;
  const described = [hint ? hintId : undefined, error ? errorId : undefined, describedBy].filter(Boolean).join(" ");
  return (
    <div className={["ui-field", className].filter(Boolean).join(" ")}>
      <label className="ui-field__label" htmlFor={inputId}>
        {label}
      </label>
      <input
        {...rest}
        id={inputId}
        className="ui-field__input"
        aria-invalid={error ? true : undefined}
        aria-describedby={described || undefined}
      />
      {hint ? (
        <p id={hintId} className="ui-field__hint">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="ui-field__error" role="alert">
          <Icon name="warning" size="sm" />
          {error}
        </p>
      ) : null}
    </div>
  );
}
