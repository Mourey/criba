import { useId, type ReactNode } from "react";

export type CardProps = {
  /** Panel header, in mono small caps. With a title the card is a region named by it. */
  title?: string;
  count?: number | string;
  /** Highlights the count in the live accent: it marks what needs the Recruiter. */
  live?: boolean;
  actions?: ReactNode;
  /** Heading level of the title. */
  level?: 2 | 3 | 4;
  /** Drops the body padding, for tables and rows that run edge to edge. */
  flush?: boolean;
  children: ReactNode;
};

export function Card({ title, count, live = false, actions, level = 2, flush = false, children }: CardProps) {
  const titleId = useId();
  if (!title) return <div className="ui-card ui-card--pad">{children}</div>;
  const Heading = `h${level}` as "h2" | "h3" | "h4";
  return (
    <section className={flush ? "ui-card ui-card--flush" : "ui-card"} aria-labelledby={titleId}>
      <header className="ui-card__head">
        <Heading className="ui-card__title">
          <span id={titleId}>{title}</span>
          {count !== undefined ? <span className={live ? "ui-card__count ui-card__count--live" : "ui-card__count"}>{count}</span> : null}
        </Heading>
        {actions ? <div className="ui-card__actions">{actions}</div> : null}
      </header>
      <div className="ui-card__body">{children}</div>
    </section>
  );
}
