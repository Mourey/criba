import { createContext, useContext } from "react";
import type { Me, Role } from "../api/types.ts";

export interface AuthValue {
  /** The signed-in user and Location scope, or null when logged out. */
  me: Me | null;
  /** True until the first answer from GET /api/auth/me. A background refetch never sets it. */
  loading: boolean;
  /** A non-401 failure of GET /api/auth/me with no user known yet (a 401 means logged out). */
  error: Error | null;
  /** Asks GET /api/auth/me again, for the error state's Retry. */
  refetch: () => void;
  /** The role the server gave the session. */
  realRole: Role | null;
  /**
   * The role the UI behaves as. Operators can preview the Recruiter view with the role switch;
   * the server still applies the real role, so this only changes what the UI shows.
   */
  role: Role | null;
  isPreviewingRecruiter: boolean;
  setPreviewRecruiter: (on: boolean) => void;
  login: (email: string, password: string) => Promise<Me>;
  logout: () => Promise<void>;
}

export const AuthContext = createContext<AuthValue | null>(null);

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}

/** Economics views and cost fields exist for Operators only (ADR 0019). */
export function canSeeEconomics(role: Role | null): boolean {
  return role === "operator";
}

/** Where `/` sends a signed-in user, by the real role (preview never changes it). */
export function landingPath(realRole: Role): "/overview" | "/today" {
  return realRole === "operator" ? "/overview" : "/today";
}
