import {
  ArrowRightIcon,
  BarChartIcon,
  BellIcon,
  CalendarIcon,
  ChatBubbleIcon,
  CheckboxIcon,
  CheckCircledIcon,
  CheckIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  ClockIcon,
  Cross2Icon,
  DashboardIcon,
  DesktopIcon,
  DoubleArrowLeftIcon,
  DoubleArrowRightIcon,
  ExitIcon,
  ExclamationTriangleIcon,
  ExternalLinkIcon,
  EyeOpenIcon,
  FileTextIcon,
  GearIcon,
  HomeIcon,
  InfoCircledIcon,
  LockClosedIcon,
  MagnifyingGlassIcon,
  MoonIcon,
  PieChartIcon,
  PersonIcon,
  PlusIcon,
  ReloadIcon,
  SunIcon,
  TrashIcon,
} from "@radix-ui/react-icons";

// The one icon set (@radix-ui/react-icons), curated by name. Add a name here before using it.
const ICONS = {
  arrowRight: ArrowRightIcon,
  bell: BellIcon,
  calendar: CalendarIcon,
  chat: ChatBubbleIcon,
  check: CheckIcon,
  checkCircle: CheckCircledIcon,
  chevronDown: ChevronDownIcon,
  chevronRight: ChevronRightIcon,
  clock: ClockIcon,
  cross: Cross2Icon,
  externalLink: ExternalLinkIcon,
  gear: GearIcon,
  info: InfoCircledIcon,
  moon: MoonIcon,
  padlock: LockClosedIcon,
  person: PersonIcon,
  plus: PlusIcon,
  reload: ReloadIcon,
  search: MagnifyingGlassIcon,
  sun: SunIcon,
  system: DesktopIcon,
  trash: TrashIcon,
  warning: ExclamationTriangleIcon,
} as const;

// The left navigation's icons. A second map, not part of IconName, because the specimen page lists
// every IconName with a use (Record<IconName, string>) and is not part of this change.
const NAV_ICONS = {
  barChart: BarChartIcon,
  checkbox: CheckboxIcon,
  dashboard: DashboardIcon,
  doubleArrowLeft: DoubleArrowLeftIcon,
  doubleArrowRight: DoubleArrowRightIcon,
  exit: ExitIcon,
  eyeOpen: EyeOpenIcon,
  fileText: FileTextIcon,
  home: HomeIcon,
  pieChart: PieChartIcon,
} as const;

export type IconName = keyof typeof ICONS;
export type NavIconName = keyof typeof NAV_ICONS;
export type IconSize = "sm" | "md" | "lg";

export type IconProps = {
  name: IconName | NavIconName;
  /** Accessible name. Without it the icon is decorative and hidden from assistive technology. */
  label?: string;
  size?: IconSize;
  className?: string;
};

export function Icon({ name, label, size = "md", className }: IconProps) {
  const Glyph = name in ICONS ? ICONS[name as IconName] : NAV_ICONS[name as NavIconName];
  const classes = ["ui-icon", `ui-icon--${size}`, className].filter(Boolean).join(" ");
  if (label) return <Glyph className={classes} role="img" aria-label={label} focusable="false" />;
  return <Glyph className={classes} aria-hidden="true" focusable="false" />;
}
