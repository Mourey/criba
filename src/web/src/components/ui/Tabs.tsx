import * as RadixTabs from "@radix-ui/react-tabs";
import type { ReactNode } from "react";

export type TabItem = { value: string; label: string; content: ReactNode };

export type TabsProps = {
  /** Accessible name of the tab list. */
  label: string;
  tabs: TabItem[];
  defaultValue?: string;
};

export function Tabs({ label, tabs, defaultValue }: TabsProps) {
  return (
    <RadixTabs.Root className="ui-tabs" defaultValue={defaultValue ?? tabs[0]?.value}>
      <RadixTabs.List className="ui-tabs__list" aria-label={label}>
        {tabs.map((tab) => (
          <RadixTabs.Trigger key={tab.value} className="ui-tabs__trigger" value={tab.value}>
            {tab.label}
          </RadixTabs.Trigger>
        ))}
      </RadixTabs.List>
      {tabs.map((tab) => (
        <RadixTabs.Content key={tab.value} className="ui-tabs__panel" value={tab.value}>
          {tab.content}
        </RadixTabs.Content>
      ))}
    </RadixTabs.Root>
  );
}
