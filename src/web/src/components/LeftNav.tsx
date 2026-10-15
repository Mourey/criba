import { Link, NavLink } from "react-router";
import type { Role } from "../api/types.ts";
import { canSeeEconomics } from "../auth/context.ts";
import { CLIENT_NAME } from "../lib/labels.ts";
// Primitives by file, not through ui/index.ts (see Shell.tsx).
import { Button } from "./ui/Button.tsx";
import { Icon, type IconName, type NavIconName } from "./ui/Icon.tsx";
import { ThemeToggle } from "./ui/ThemeToggle.tsx";

/** The id the collapse button's aria-controls points at: the navigation column. */
export const NAV_COLUMN_ID = "shell-nav-column";

const LABEL_BRAND = "Criba";
const LABEL_COLLAPSE = "Collapse navigation";
const LABEL_EXPAND = "Expand navigation";
const LABEL_PREVIEW = "Preview as Recruiter";
const LABEL_LOGOUT = "Log out";
const LABEL_THEME = "Theme";

interface NavItem {
  to: string;
  label: string;
  icon: IconName | NavIconName;
}

const TODAY: NavItem = { to: "/today", label: "Today", icon: "home" };
const OVERVIEW: NavItem = { to: "/overview", label: "Overview", icon: "dashboard" };
const APPLICATIONS: NavItem = { to: "/applications", label: "Applications", icon: "fileText" };
const REVIEW_QUEUE: NavItem = { to: "/queue", label: "Review queue", icon: "checkbox" };
const SLOTS: NavItem = { to: "/slots", label: "Interview slots", icon: "calendar" };
const RESULTS: NavItem = { to: "/results", label: "Results", icon: "barChart" };
const TEST_CHAT: NavItem = { to: "/test-chat", label: "Test chat", icon: "chat" };
/** The Operator's Test chat item opens the conversation list; it starts a new one from there. */
const TEST_CHAT_LIST: NavItem = { ...TEST_CHAT, to: "/conversations" };
const TEST_VOICE: NavItem = { to: "/voice", label: "Test voice", icon: "chat" };
const ECONOMICS: NavItem = { to: "/economics", label: "Economics", icon: "pieChart" };

/** The page links for the effective role: Today or Overview first, Economics (Operators only) last. */
function itemsFor(role: Role | null): NavItem[] {
  return [
    role === "recruiter" ? TODAY : OVERVIEW,
    APPLICATIONS,
    REVIEW_QUEUE,
    SLOTS,
    RESULTS,
    canSeeEconomics(role) ? TEST_CHAT_LIST : TEST_CHAT,
    TEST_VOICE,
    ...(canSeeEconomics(role) ? [ECONOMICS] : []),
  ];
}

export interface LeftNavProps {
  /** The role the UI behaves as (the preview role for an Operator previewing). */
  role: Role | null;
  realRole: Role | null;
  isPreviewingRecruiter: boolean;
  userName: string;
  open: boolean;
  onToggle: () => void;
  onPreview: () => void;
  onLogout: () => void;
}

/**
 * The left column: collapse button, brand, the "Main" navigation (page links only), then the user's
 * controls. Collapsed, the labels stay in the DOM, visually hidden (shell.css), and a `title` shows them
 * on hover.
 */
export function LeftNav({ role, realRole, isPreviewingRecruiter, userName, open, onToggle, onPreview, onLogout }: LeftNavProps) {
  const tip = (label: string) => (open ? undefined : label);
  const toggleName = open ? LABEL_COLLAPSE : LABEL_EXPAND;
  return (
    <div className="shell-column" id={NAV_COLUMN_ID}>
      <Button
        variant="quiet"
        className="shell-toggle"
        aria-label={toggleName}
        aria-expanded={open}
        aria-controls={NAV_COLUMN_ID}
        title={toggleName}
        onClick={onToggle}
      >
        <Icon name={open ? "doubleArrowLeft" : "doubleArrowRight"} />
      </Button>
      <div className="shell-brand">
        <Link to="/" className="brand">
          <span className="shell-brand-mark" aria-hidden="true">
            O
          </span>
          <span className="shell-label">{LABEL_BRAND}</span>
        </Link>
        {role === "recruiter" && <span className="shell-client shell-label">{CLIENT_NAME}</span>}
      </div>
      <nav className="shell-nav" aria-label="Main">
        {itemsFor(role).map((item) => (
          <NavLink key={item.to} to={item.to} title={tip(item.label)}>
            <Icon name={item.icon} />
            <span className="shell-label">{item.label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="shell-who">
        <span className="shell-label shell-user">{userName}</span>
        <span className="tag solid shell-label" data-testid="role-badge">
          {role === "operator" ? "Operator" : "Recruiter"}
        </span>
        <div className="shell-theme" title={tip(LABEL_THEME)}>
          <ThemeToggle />
        </div>
        {realRole === "operator" && !isPreviewingRecruiter && (
          <Button variant="quiet" className="shell-action" title={tip(LABEL_PREVIEW)} onClick={onPreview}>
            <Icon name="eyeOpen" />
            <span className="shell-label">{LABEL_PREVIEW}</span>
          </Button>
        )}
        <Button variant="quiet" className="shell-action" title={tip(LABEL_LOGOUT)} onClick={onLogout}>
          <Icon name="exit" />
          <span className="shell-label">{LABEL_LOGOUT}</span>
        </Button>
      </div>
    </div>
  );
}
