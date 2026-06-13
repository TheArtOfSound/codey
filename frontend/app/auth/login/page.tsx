"use client";

import { Suspense, useEffect, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { storePendingOAuthRequest } from "@/lib/oauth";


function LoginPageContent() {
  const { login } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const redirect = searchParams.get("redirect") || "/dashboard";
  const referrerId = searchParams.get("ref");

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [oauthLoading, setOauthLoading] = useState<"github" | "google" | null>(null);
  const [providerAvailability, setProviderAvailability] = useState({
    github: false,
    google: false,
  });

  useEffect(() => {
    let active = true;
    api
      .getAuthProviders()
      .then((providers) => {
        if (active) {
          setProviderAvailability(providers);
        }
      })
      .catch(() => {
        if (active) {
          setProviderAvailability({ github: false, google: false });
        }
      });

    return () => {
      active = false;
    };
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(email, password);
      router.push(redirect);
    } catch (err: unknown) {
      const apiErr = err as { detail?: string };
      setError(apiErr.detail || "Invalid email or password.");
    } finally {
      setLoading(false);
    }
  }

  async function handleOAuth(provider: "github" | "google") {
    setError(null);
    setOauthLoading(provider);
    try {
      const result =
        provider === "github"
          ? await api.getGitHubOAuthUrl()
          : await api.getGoogleOAuthUrl();
      storePendingOAuthRequest({
        provider,
        redirectTo: redirect,
        state: result.state,
        intent: "login",
      });
      window.location.href = result.url;
      return;
    } catch (err: unknown) {
      const apiErr = err as { detail?: string };
      setError(apiErr.detail || `Could not start ${provider} sign-in.`);
      setOauthLoading(null);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-codey-bg px-4">
      <div className="w-full max-w-md">
        {/* Logo */}
        <Link
          href="/"
          className="mb-10 block text-center text-2xl font-bold tracking-tight"
        >
          <span className="text-codey-green">C</span>ODEY
        </Link>

        <div className="rounded-xl border border-codey-border bg-codey-card p-8">
          <h1 className="mb-1 text-xl font-semibold text-codey-text">
            Return to control room
          </h1>
          <p className="mb-6 text-sm text-codey-text-dim">
            Log in to review repo health, queue work, and manage autopilot
          </p>

          {error && (
            <div className="mb-4 rounded-lg border border-codey-red/30 bg-codey-red-glow px-4 py-3 text-sm text-codey-red">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label
                htmlFor="email"
                className="mb-1.5 block text-sm font-medium text-codey-text-dim"
              >
                Email
              </label>
              <input
                id="email"
                type="email"
                required
                autoComplete="email"
                placeholder="you@company.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="input"
              />
            </div>

            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <label
                  htmlFor="password"
                  className="text-sm font-medium text-codey-text-dim"
                >
                  Password
                </label>
                <Link
                  href="/auth/forgot-password"
                  className="text-xs text-codey-green hover:underline"
                >
                  Forgot password?
                </Link>
              </div>
              <input
                id="password"
                type="password"
                required
                autoComplete="current-password"
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="input"
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className="btn-primary w-full py-3"
            >
              {loading ? (
                <span className="flex items-center justify-center gap-2">
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-codey-bg border-t-transparent" />
                  Logging in...
                </span>
              ) : (
                "Log in"
              )}
            </button>
          </form>

          <div className="my-6 flex items-center gap-3">
            <div className="h-px flex-1 bg-codey-border" />
            <span className="text-xs uppercase tracking-wide text-codey-text-muted">
              Or continue with
            </span>
            <div className="h-px flex-1 bg-codey-border" />
          </div>

          {providerAvailability.github || providerAvailability.google ? (
            <div className="space-y-3">
              {providerAvailability.github && (
                <button
                  type="button"
                  disabled={oauthLoading !== null}
                  onClick={() => handleOAuth("github")}
                  className="btn-ghost w-full py-3"
                >
                  {oauthLoading === "github" ? "Connecting GitHub..." : "Continue with GitHub"}
                </button>
              )}
              {providerAvailability.google && (
                <button
                  type="button"
                  disabled={oauthLoading !== null}
                  onClick={() => handleOAuth("google")}
                  className="btn-ghost w-full py-3"
                >
                  {oauthLoading === "google" ? "Connecting Google..." : "Continue with Google"}
                </button>
              )}
            </div>
          ) : (
            <div className="rounded-lg border border-codey-border bg-codey-card-muted px-4 py-3 text-sm text-codey-text-dim">
              OAuth sign-in is not configured for this deployment yet.
            </div>
          )}

        </div>

        <p className="mt-6 text-center text-sm text-codey-text-dim">
          Don&apos;t have an account?{" "}
          <Link
            href={`/auth/signup${referrerId ? `?ref=${encodeURIComponent(referrerId)}` : ""}`}
            className="font-medium text-codey-green hover:underline"
          >
            Sign up
          </Link>
        </p>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-codey-bg">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-codey-green border-t-transparent" />
        </div>
      }
    >
      <LoginPageContent />
    </Suspense>
  );
}
