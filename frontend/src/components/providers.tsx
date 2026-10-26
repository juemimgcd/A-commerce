"use client";

import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useState, useSyncExternalStore, type ReactNode } from "react";
import { api, ApiError } from "@/lib/api";
import type { Profile, Scope, Store } from "@/lib/types";

type AuthContextValue = {
  customer?: Profile;
  admin?: Profile;
  customerLoading: boolean;
  adminLoading: boolean;
  authenticated: (scope: Scope, preserveDraft?: boolean) => Promise<void>;
  forget: (scope: Scope) => void;
  logout: (scope: Scope) => Promise<void>;
};
const AuthContext = createContext<AuthContextValue | null>(null);

function AuthProvider({
  children,
  hints,
}: {
  children: ReactNode;
  hints: { customer: boolean; admin: boolean };
}) {
  const [active, setActive] = useState(hints);
  const client = useQueryClient();
  const customer = useQuery({
    queryKey: ["customer", "identity"],
    queryFn: () => api<Profile>("customer/me"),
    enabled: active.customer,
    retry: false,
    staleTime: 60000,
  });
  const admin = useQuery({
    queryKey: ["admin", "identity"],
    queryFn: () => api<Profile>("admin/profile"),
    enabled: active.admin,
    retry: false,
    staleTime: 60000,
  });
  async function authenticated(scope: Scope, preserveDraft = false) {
    if (!preserveDraft) client.removeQueries({ queryKey: [scope] });
    setActive((previous) => ({ ...previous, [scope]: true }));
    await client.fetchQuery({
      queryKey: [scope, "identity"],
      queryFn: () => api<Profile>(`${scope}/${scope === "admin" ? "profile" : "me"}`),
      staleTime: 0,
    });
  }
  function forget(scope: Scope) {
    setActive((previous) => ({ ...previous, [scope]: false }));
    client.removeQueries({ queryKey: [scope] });
  }
  async function logout(scope: Scope) {
    try {
      await api(`${scope}/auth/logout`, { method: "POST" });
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 401)) throw error;
    }
    forget(scope);
  }
  return (
    <AuthContext.Provider
      value={{
        customer: active.customer && !customer.error ? customer.data : undefined,
        admin: active.admin && !admin.error ? admin.data : undefined,
        customerLoading: active.customer && customer.isPending,
        adminLoading: active.admin && admin.isPending,
        authenticated,
        forget,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function Providers({
  children,
  hints,
}: {
  children: ReactNode;
  hints: { customer: boolean; admin: boolean };
}) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 15000,
            retry: (count, error) => count < 1 && error instanceof ApiError && error.status >= 500,
            retryDelay: (count, error) =>
              error instanceof ApiError && error.retryAfter > 0
                ? error.retryAfter * 1000
                : Math.min(1000 * 2 ** count, 10000),
            refetchOnWindowFocus: false,
          },
          mutations: { retry: false, networkMode: "always" },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <AuthProvider hints={hints}>{children}</AuthProvider>
    </QueryClientProvider>
  );
}
export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("Auth provider missing");
  return value;
}
export function useStore() {
  return useQuery({
    queryKey: ["store"],
    queryFn: () => api<Store>("customer/store"),
    staleTime: 60000,
  });
}

const subscribeVisibility = (callback: () => void) => {
  document.addEventListener("visibilitychange", callback);
  window.addEventListener("online", callback);
  window.addEventListener("offline", callback);
  return () => {
    document.removeEventListener("visibilitychange", callback);
    window.removeEventListener("online", callback);
    window.removeEventListener("offline", callback);
  };
};
export function useVisible() {
  return useSyncExternalStore(
    subscribeVisibility,
    () => document.visibilityState === "visible" && navigator.onLine,
    () => true,
  );
}
