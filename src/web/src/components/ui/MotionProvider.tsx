import { MotionConfig } from "motion/react";
import type { ReactNode } from "react";

// Mount once near the root: every `motion` animation below it follows the user's reduced-motion setting.
export function MotionProvider({ children }: { children: ReactNode }) {
  return <MotionConfig reducedMotion="user">{children}</MotionConfig>;
}
