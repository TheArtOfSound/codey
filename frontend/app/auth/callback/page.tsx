"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import {
  clearPendingOAuthRequest,
  loadPendingOAuthRequest,
} from "@/lib/oauth";

function OAuthCallbackPageContent() {
  const { loginWithGitHub, loginWithGoogle } = useAuth();
  const searchParams = useSearchParams();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const code = searchParams.get("code");
    const state = searchParams.get("state");
    const authComplete = searchParams.get("auth_complete") === "1";
    const pending = loadPendingOAuthRequest();

    async function handleCallback() {
      try {
        async function finalizeRedirect(redirectTo: string) {
          if (pending?.intent === "signup" && pending.referrerId) {
            try {
              await api.claimReferral(pending.referrerId);
            } catch {
              // Referral attribution should not block sign-in.
            }
          }
          clearPendingOAuthRequest();
          window.location.replace(redirectTo);
        }

        if (authComplete) {
          if (state && pending && pending.state !== state) {
            clearPendingOAuthRequest();
            setError("OAuth state validation failed. Please try again.");
            return;
          }
          const redirectTo = pending?.redirectTo || "/dashboard";
          await finalizeRedirect(redirectTo);
          return;
        }

        if (!code) {
          setError("No authorization code received.");
          return;
        }

        if (!state || !pending || pending.state !== state) {
          clearPendingOAuthRequest();
          setError("OAuth state validation failed. Please try again.");
          return;
        }

        if (pending.provider === "google") {
          await loginWithGoogle(code, state);
        } else {
          await loginWithGitHub(code, state);
        }
        const redirectTo = pending.redirectTo || "/dashboard";
        await finalizeRedirect(redirectTo);
      } catch (err: unknown) {
        clearPendingOAuthRequest();
        const apiErr = err as { detail?: string };
        setError(apiErr.detail || "Authentication failed. Please try again.");
      }
    }

    handleCallback();
  }, [searchParams, loginWithGitHub, loginWithGoogle]);

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-codey-bg px-4">
        <div className="w-full max-w-md text-center">
          <div className="rounded-xl border border-codey-border bg-codey-card p-8">
            <div className="mb-4 rounded-lg border border-codey-red/30 bg-codey-red-glow px-4 py-3 text-sm text-codey-red">
              {error}
            </div>
            <Link href="/auth/login" className="text-sm text-codey-green hover:underline">
              Back to login
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-codey-bg">
      <div className="flex flex-col items-center gap-4">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-codey-green border-t-transparent" />
        <span className="text-sm text-codey-text-dim">Completing sign in...</span>
      </div>
    </div>
  );
}

export default function OAuthCallbackPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-codey-bg">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-codey-green border-t-transparent" />
        </div>
      }
    >
      <OAuthCallbackPageContent />
    </Suspense>
  );
}
