import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, ApiError, setUnauthorizedHandler } from "../api/client.ts";
import type { Me } from "../api/types.ts";
import { AuthContext, type AuthValue } from "./context.ts";

const PREVIEW_KEY = "criba.previewRecruiter";

function readPreview(): boolean {
  try {
    return window.sessionStorage.getItem(PREVIEW_KEY) === "1";
  } catch {
    return false;
  }
}

function writePreview(on: boolean): void {
  try {
    if (on) window.sessionStorage.setItem(PREVIEW_KEY, "1");
    else window.sessionStorage.removeItem(PREVIEW_KEY);
  } catch {
    // Storage can be blocked; the switch then lasts for this page load only.
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [preview, setPreview] = useState(readPreview);

  const meQuery = useQuery({
    queryKey: ["me"],
    queryFn: async (): Promise<Me | null> => {
      try {
        return await api.me();
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    retry: false,
    staleTime: 60_000,
  });

  useEffect(() => {
    setUnauthorizedHandler(() => {
      queryClient.setQueryData(["me"], null);
    });
    return () => setUnauthorizedHandler(undefined);
  }, [queryClient]);

  const login = useCallback(
    async (email: string, password: string) => {
      const me = await api.login(email, password);
      queryClient.setQueryData(["me"], me);
      return me;
    },
    [queryClient],
  );

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      writePreview(false);
      setPreview(false);
      queryClient.clear();
      queryClient.setQueryData(["me"], null);
    }
  }, [queryClient]);

  const setPreviewRecruiter = useCallback((on: boolean) => {
    writePreview(on);
    setPreview(on);
  }, []);

  const { refetch: refetchMe } = meQuery;
  const refetch = useCallback(() => {
    void refetchMe();
  }, [refetchMe]);

  const value = useMemo<AuthValue>(() => {
    const me = meQuery.data ?? null;
    const realRole = me?.user.role ?? null;
    const previewing = realRole === "operator" && preview;
    return {
      me,
      loading: meQuery.isPending,
      error: me ? null : meQuery.error,
      refetch,
      realRole,
      role: previewing ? "recruiter" : realRole,
      isPreviewingRecruiter: previewing,
      setPreviewRecruiter,
      login,
      logout,
    };
  }, [meQuery.data, meQuery.isPending, meQuery.error, refetch, preview, setPreviewRecruiter, login, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
