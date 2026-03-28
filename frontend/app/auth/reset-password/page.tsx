"use client";

import { useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { api } from "@/lib/api";

export default function ResetPasswordPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token") || "";

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }

    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }

    if (!token) {
      setError("Missing reset token. Please use the link from your email.");
      return;
    }

    setLoading(true);
    try {
      await api.confirmPasswordReset(token, password);
      router.push("/auth/login?reset=success");
    } catch (err: unknown) {
      const apiErr = err as { detail?: string };
      setError(apiErr.detail || "Invalid or expired reset token.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-codey-bg px-4">
      <div className="w-full max-w-md">
        <Link
          href="/"
          className="mb-10 block text-center text-2xl font-bold tracking-tight"
        >
          <span className="text-codey-green">C</span>ODEY
        </Link>

        <div className="rounded-xl border border-codey-border bg-codey-card p-8">
          <h1 className="mb-1 text-xl font-semibold text-codey-text">
            Set new password
          </h1>
          <p className="mb-6 text-sm text-codey-text-dim">
            Enter your new password below.
          </p>

          {error && (
            <div className="mb-4 rounded-lg border border-codey-red/30 bg-codey-red-glow px-4 py-3 text-sm text-codey-red">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label
                htmlFor="password"
                className="mb-1.5 block text-sm font-medium text-codey-text-dim"
              >
                New password
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

            <div>
              <label
                htmlFor="confirm"
                className="mb-1.5 block text-sm font-medium text-codey-text-dim"
              >
                Confirm password
              </label>
              <input
                id="confirm"
                type="password"
                required
                autoComplete="new-password"
                placeholder="Re-enter password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
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
                  Resetting...
                </span>
              ) : (
                "Reset password"
              )}
            </button>
          </form>
        </div>

        <p className="mt-6 text-center text-sm text-codey-text-dim">
          <Link
            href="/auth/login"
            className="font-medium text-codey-green hover:underline"
          >
            Back to login
          </Link>
        </p>
      </div>
    </div>
  );
}
