// Motion presets for the `motion` library, in seconds. They mirror --motion-* and --ease-* in
// styles/tokens.css (a test keeps them equal): short, ease-out, nothing longer than a third of a second.
export const DURATION_FAST = 0.12;
export const DURATION_BASE = 0.2;
export const DURATION_SLOW = 0.32;

export const EASE_OUT = [0.22, 1, 0.36, 1] as const;
export const EASE_IN_OUT = [0.65, 0, 0.35, 1] as const;
