import type { ButtonHTMLAttributes } from "react";

export type ButtonVariant = "primary" | "secondary" | "quiet" | "danger";

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  /** Busy: sets aria-busy, disables the button and shows a spinner. The label stays. */
  loading?: boolean;
};

export function Button({ variant = "secondary", loading = false, type = "button", className, disabled, children, ...rest }: ButtonProps) {
  const classes = ["ui-btn", `ui-btn--${variant}`, className].filter(Boolean).join(" ");
  return (
    <button {...rest} type={type} className={classes} disabled={disabled || loading} aria-busy={loading || undefined}>
      {loading ? <span className="ui-btn__spinner" aria-hidden="true" /> : null}
      {children}
    </button>
  );
}
