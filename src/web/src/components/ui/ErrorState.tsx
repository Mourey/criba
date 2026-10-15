import { Button } from "./Button.tsx";
import { Icon } from "./Icon.tsx";

// The message names the problem; Retry is the way out.
export function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="ui-error" role="alert">
      <Icon name="warning" size="lg" className="ui-error__icon" />
      <p className="ui-error__message">{message}</p>
      <Button onClick={onRetry}>Retry</Button>
    </div>
  );
}
