import * as RadixDialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";
import { Icon } from "./Icon.tsx";

export type DialogProps = {
  /** The element that opens the dialog. Focus returns to it on close. Omit it to control `open` yourself. */
  trigger?: ReactNode;
  /** Both are required: the dialog's accessible name and description. */
  title: string;
  description: string;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  children?: ReactNode;
};

export function Dialog({ trigger, title, description, open, onOpenChange, children }: DialogProps) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      {trigger ? <RadixDialog.Trigger asChild>{trigger}</RadixDialog.Trigger> : null}
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="ui-dialog__overlay" />
        <RadixDialog.Content className="ui-dialog">
          <div className="ui-dialog__head">
            <RadixDialog.Title className="ui-dialog__title">{title}</RadixDialog.Title>
            <RadixDialog.Close className="ui-iconbtn" aria-label="Close">
              <Icon name="cross" />
            </RadixDialog.Close>
          </div>
          <RadixDialog.Description className="ui-dialog__description">{description}</RadixDialog.Description>
          {children ? <div className="ui-dialog__body">{children}</div> : null}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
