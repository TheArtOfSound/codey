"use client";

import { Suspense, useEffect, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { storePendingOAuthRequest } from "@/lib/oauth";

function SignupPageContent() {
  const { signup } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const plan = searchParams.get("plan");
  const referrerId = searchParams.get("ref");

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [oauthLoading, setOauthLoading] = useState<"github" | "google" | null>(null);
  const [providerAvailability, setProviderAvailability] = useState({
    github: false,
    google: false,
  });

  async function claimReferralIfPresent() {
    if (!referrerId) {
      return;
    }
    try {
      await api.claimReferral(referrerId);
    } catch {
      // Referral attribution should not block account creation.
    }
  }

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

    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }

    setLoading(true);
    try {
      await signup(email, password, name || undefined);
      await claimReferralIfPresent();
      // If they selected a plan, redirect to billing to complete subscription
      if (plan && plan !== "free") {
        const suffix = referrerId ? `&ref=${encodeURIComponent(referrerId)}` : "";
        router.push(`/settings/billing?subscribe=${plan}${suffix}`);
      } else {
        router.push("/dashboard");
      }
    } catch (err: unknown) {
      const apiErr = err as { detail?: string };
      setError(apiErr.detail || "Could not create account. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  const planLabels: Record<string, string> = {
    starter: "Starter",
    pro: "Pro",
    team: "Team",
  };

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
        redirectTo:
          plan && plan !== "free"
            ? `/settings/billing?subscribe=${plan}${referrerId ? `&ref=${encodeURIComponent(referrerId)}` : ""}`
            : "/dashboard",
        state: result.state,
        intent: "signup",
        referrerId: referrerId || undefined,
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

        {/* Plan banner */}
        {plan && planLabels[plan] && (
          <div className="mb-4 rounded-lg border border-codey-green/30 bg-codey-green-glow px-4 py-3 text-center text-sm text-codey-green">
            You selected the <strong>{planLabels[plan]}</strong> plan. Create
            your account to continue.
          </div>
        )}

        {referrerId && (
          <div className="mb-4 rounded-lg border border-codey-border bg-codey-card-muted px-4 py-3 text-center text-sm text-codey-text-dim">
            You&apos;re joining through a referral link. Your welcome bonus will apply after your first paid upgrade.
          </div>
        )}

        <div className="rounded-xl border border-codey-border bg-codey-card p-8">
          <h1 className="mb-1 text-xl font-semibold text-codey-text">
            Create your account
          </h1>
          <p className="mb-6 text-sm text-codey-text-dim">
            Start managing repositories with Codey in under a minute
          </p>

          {error && (
            <div className="mb-4 rounded-lg border border-codey-red/30 bg-codey-red-glow px-4 py-3 text-sm text-codey-red">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label
                htmlFor="name"
                className="mb-1.5 block text-sm font-medium text-codey-text-dim"
              >
                Name
              </label>
              <input
                id="name"
                type="text"
                autoComplete="name"
                placeholder="Ada Lovelace"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="input"
              />
            </div>

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
              <label
                htmlFor="password"
                className="mb-1.5 block text-sm font-medium text-codey-text-dim"
              >
                Password
              </label>
              <input
                id="password"
                type="password"
                required
                autoComplete="new-password"
                placeholder="Min. 8 characters"
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
                  Creating account...
                </span>
              ) : (
                "Create account"
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
          Already have an account?{" "}
          <Link
            href={`/auth/login${referrerId ? `?ref=${encodeURIComponent(referrerId)}` : ""}`}
            className="font-medium text-codey-green hover:underline"
          >
            Log in
          </Link>
        </p>
      </div>
    </div>
  );
}

export default function SignupPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-codey-bg">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-codey-green border-t-transparent" />
        </div>
      }
    >
      <SignupPageContent />
    </Suspense>
  );
}
