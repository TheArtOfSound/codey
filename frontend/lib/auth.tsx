"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  type ReactNode,
} from "react";
import { useRouter, usePathname } from "next/navigation";
import { api, type User } from "./api";

// ── Context ────────────────────────────────────────────────────────────────

interface AuthState {
  user: User | null;
  token: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<User>;
  signup: (email: string, password: string, name?: string) => Promise<User>;
  loginWithGitHub: (code: string, state: string) => Promise<User>;
  loginWithGoogle: (code: string, state: string) => Promise<User>;
  logout: () => void;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

// ── Provider ───────────────────────────────────────────────────────────────

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const pathname = usePathname();

  const refreshUser = useCallback(async () => {
    try {
      const me = await api.getMe();
      setUser(me);
    } catch {
      setUser(null);
      setToken(null);
      api.setToken(null);
    }
  }, []);

  // Hydrate auth state from the session cookie only where auth matters.
  useEffect(() => {
    api.loadStoredToken();
    if (isAuthOptionalPath(pathname) && api.getToken() === null) {
      setLoading(false);
      return;
    }
    refreshUser().finally(() => setLoading(false));
  }, [pathname, refreshUser]);

  const login = useCallback(async (email: string, password: string) => {
    const result = await api.login(email, password);
    setToken(result.token);
    setUser(result.user);
    return result.user;
  }, []);

  const signup = useCallback(async (email: string, password: string, name?: string) => {
    const result = await api.signup(email, password, name);
    setToken(result.token);
    setUser(result.user);
    return result.user;
  }, []);

  const loginWithGitHub = useCallback(async (code: string, state: string) => {
    const result = await api.loginWithGitHub(code, state);
    setToken(result.token);
    setUser(result.user);
    return result.user;
  }, []);

  const loginWithGoogle = useCallback(async (code: string, state: string) => {
    const result = await api.loginWithGoogle(code, state);
    setToken(result.token);
    setUser(result.user);
    return result.user;
  }, []);

  const logout = useCallback(() => {
    setUser(null);
    setToken(null);
    api.logout();
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        loading,
        login,
        signup,
        loginWithGitHub,
        loginWithGoogle,
        logout,
        refreshUser,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

// ── Hook ───────────────────────────────────────────────────────────────────

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}

// ── Protected Route ────────────────────────────────────────────────────────

const AUTH_OPTIONAL_PATHS = [
  "/",
  "/auth/login",
  "/auth/signup",
  "/auth/callback",
  "/auth/forgot-password",
  "/auth/reset-password",
  "/pricing",
  "/privacy",
  "/terms",
  "/changelog",
];

function isAuthOptionalPath(pathname: string): boolean {
  if (pathname === "/") {
    return true;
  }
  return AUTH_OPTIONAL_PATHS.some((path) => path !== "/" && pathname.startsWith(path));
}

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!loading && !user && !isAuthOptionalPath(pathname)) {
      router.replace(`/auth/login?redirect=${encodeURIComponent(pathname)}`);
    }
  }, [user, loading, pathname, router]);

  if (loading) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-codey-bg">
        <div className="flex flex-col items-center gap-4">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-codey-green border-t-transparent" />
          <span className="text-sm text-codey-text-dim">Loading...</span>
        </div>
      </div>
    );
  }

  if (!user && !isAuthOptionalPath(pathname)) {
    return null;
  }

  return <>{children}</>;
}
