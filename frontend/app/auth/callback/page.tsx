"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/lib/auth";

export default function OAuthCallbackPage() {
  const { loginWithGitHub, loginWithGoogle } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const code = searchParams.get("code");
    const provider = searchParams.get("provider") || detectProvider();

    if (!code) {
      setError("No authorization code received.");
      return;
    }

    async function handleCallback() {
      try {
        if (provider === "google") {
          await loginWithGoogle(code!);
        } else {
          await loginWithGitHub(code!);
        }
        router.push("/dashboard");
      } catch (err: unknown) {
        const apiErr = err as { detail?: string };
        setError(apiErr.detail || "Authentication failed. Please try again.");
      }
    }

    handleCallback();
  }, [searchParams, loginWithGitHub, loginWithGoogle, router]);

  function detectProvider(): string {
    const state = searchParams.get("state");
    const scope = searchParams.get("scope");
    if (scope?.includes("email") && scope?.includes("profile")) return "google";
    if (state?.includes("google")) return "google";
    return "github";
  }

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
