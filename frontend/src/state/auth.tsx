import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../services/api";
import type { Session } from "../types/app";

interface AuthState {
  /** Undefined while the first session check is running. Null when signed out. */
  session: Session | null | undefined;
  signIn: (email: string, password: string) => Promise<Session>;
  createAccount: (email: string, password: string) => Promise<Session>;
  signOut: () => Promise<void>;
  /** The workspace name changes when the company is saved. */
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null | undefined>(undefined);

  useEffect(() => {
    let current = true;
    api.getSession().then(
      (value) => current && setSession(value),
      () => current && setSession(null),
    );
    return () => {
      current = false;
    };
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    const next = await api.signIn(email, password);
    setSession(next);
    return next;
  }, []);

  const createAccount = useCallback(async (email: string, password: string) => {
    const next = await api.createAccount(email, password);
    setSession(next);
    return next;
  }, []);

  const signOut = useCallback(async () => {
    await api.signOut();
    setSession(null);
  }, []);

  const refresh = useCallback(async () => {
    setSession(await api.getSession());
  }, []);

  const value = useMemo(
    () => ({ session, signIn, createAccount, signOut, refresh }),
    [session, signIn, createAccount, signOut, refresh],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>.");
  return value;
}
