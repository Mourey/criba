import * as RadixToast from "@radix-ui/react-toast";
import { Icon, type IconName } from "./Icon.tsx";

export type ToastTone = "neutral" | "success" | "warn";

const TONE_ICON: Record<ToastTone, IconName> = { neutral: "info", success: "checkCircle", warn: "warning" };

export type ToastProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  tone?: ToastTone;
  /** Milliseconds before it dismisses itself. */
  duration?: number;
};

// Brings its own provider and viewport, so one Toast works anywhere in the tree.
export function Toast({ open, onOpenChange, title, description, tone = "neutral", duration = 5000 }: ToastProps) {
  return (
    <RadixToast.Provider swipeDirection="right" duration={duration}>
      <RadixToast.Root className={`ui-toast ui-toast--${tone}`} open={open} onOpenChange={onOpenChange}>
        <Icon name={TONE_ICON[tone]} className="ui-toast__icon" />
        <div className="ui-toast__text">
          <RadixToast.Title className="ui-toast__title">{title}</RadixToast.Title>
          {description ? <RadixToast.Description className="ui-toast__description">{description}</RadixToast.Description> : null}
        </div>
        <RadixToast.Close className="ui-iconbtn" aria-label="Dismiss">
          <Icon name="cross" />
        </RadixToast.Close>
      </RadixToast.Root>
      <RadixToast.Viewport className="ui-toast__viewport" />
    </RadixToast.Provider>
  );
}
