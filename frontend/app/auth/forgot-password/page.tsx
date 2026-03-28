"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await api.requestPasswordReset(email);
      setSent(true);
    } catch (err: unknown) {
      const apiErr = err as { detail?: string };
      setError(apiErr.detail || "Something went wrong. Please try again.");
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
            Reset your password
          </h1>
          <p className="mb-6 text-sm text-codey-text-dim">
            Enter your email and we&apos;ll send you a reset link.
          </p>

          {error && (
            <div className="mb-4 rounded-lg border border-codey-red/30 bg-codey-red-glow px-4 py-3 text-sm text-codey-red">
              {error}
            </div>
          )}

          {sent ? (
            <div className="rounded-lg border border-codey-green/30 bg-codey-green-glow px-4 py-3 text-sm text-codey-green">
              If an account with that email exists, a reset link has been sent.
              Check your inbox.
            </div>
          ) : (
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

              <button
                type="submit"
                disabled={loading}
                className="btn-primary w-full py-3"
              >
                {loading ? (
                  <span className="flex items-center justify-center gap-2">
                    <span className="h-4 w-4 animate-spin rounded-full border-2 border-codey-bg border-t-transparent" />
                    Sending...
                  </span>
                ) : (
                  "Send reset link"
                )}
              </button>
            </form>
          )}
        </div>

        <p className="mt-6 text-center text-sm text-codey-text-dim">
          Remember your password?{" "}
          <Link
            href="/auth/login"
            className="font-medium text-codey-green hover:underline"
          >
            Log in
          </Link>
        </p>
      </div>
    </div>
  );
}
