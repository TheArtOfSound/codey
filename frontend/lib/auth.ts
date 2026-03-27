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
  signup: (email: string, password: string) => Promise<User>;
  loginWithGitHub: (code: string) => Promise<User>;
  logout: () => void;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

// ── Provider ───────────────────────────────────────────────────────────────

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

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

  // Hydrate auth state from localStorage on mount
  useEffect(() => {
    const stored = api.getToken();
    if (stored) {
      setToken(stored);
      api.getMe()
        .then((me) => {
          setUser(me);
        })
        .catch(() => {
          api.setToken(null);
          setToken(null);
          setUser(null);
        })
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const result = await api.login(email, password);
    setToken(result.token);
    setUser(result.user);
    return result.user;
  }, []);

  const signup = useCallback(async (email: string, password: string) => {
    const result = await api.signup(email, password);
    setToken(result.token);
    setUser(result.user);
    return result.user;
  }, []);

  const loginWithGitHub = useCallback(async (code: string) => {
    const result = await api.loginWithGitHub(code);
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

const PUBLIC_PATHS = ["/auth/login", "/auth/signup", "/auth/github/callback", "/pricing"];

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!loading && !user && !PUBLIC_PATHS.some((p) => pathname.startsWith(p))) {
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

  if (!user && !PUBLIC_PATHS.some((p) => pathname.startsWith(p))) {
    return null;
  }

  return <>{children}</>;
}
