"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import {
  Gift,
  Copy,
  Check,
  Users,
  Zap,
  TrendingUp,
  ExternalLink,
  Loader2,
  UserCheck,
  Clock,
  Send,
} from "lucide-react";

// ── Types ─────────────────────────────────────────────────────────────────────

interface ReferralStats {
  referral_code: string;
  referral_link: string;
  total_sent: number;
  total_converted: number;
  credits_earned: number;
}

interface Referral {
  id: string;
  email: string;
  status: "pending" | "signed_up" | "converted";
  invited_at: string;
  converted_at: string | null;
  credits_earned: number;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function statusBadge(status: Referral["status"]): { label: string; color: string } {
  switch (status) {
    case "pending":
      return { label: "Pending", color: "bg-codey-yellow/20 text-codey-yellow" };
    case "signed_up":
      return { label: "Signed Up", color: "bg-codey-text-dim/20 text-codey-text-dim" };
    case "converted":
      return { label: "Converted", color: "bg-codey-green/20 text-codey-green" };
  }
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function ReferralsPage() {
  const { user } = useAuth();
  const [stats, setStats] = useState<ReferralStats | null>(null);
  const [referrals, setReferrals] = useState<Referral[]>([]);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const [statsData, refData] = await Promise.all([
          api.get<ReferralStats>("/referrals/stats"),
          api.get<Referral[]>("/referrals"),
        ]);
        setStats(statsData);
        setReferrals(refData);
      } catch {
        // Demo data
        const code = user?.email?.split("@")[0]?.toUpperCase() || "CODEY";
        setStats({
          referral_code: code,
          referral_link: `https://codey.ai/ref/${code}`,
          total_sent: 8,
          total_converted: 3,
          credits_earned: 750,
        });
        setReferrals([
          {
            id: "r1",
            email: "alice@example.com",
            status: "converted",
            invited_at: new Date(Date.now() - 604800_000).toISOString(),
            converted_at: new Date(Date.now() - 432000_000).toISOString(),
            credits_earned: 250,
          },
          {
            id: "r2",
            email: "bob@example.com",
            status: "converted",
            invited_at: new Date(Date.now() - 1209600_000).toISOString(),
            converted_at: new Date(Date.now() - 864000_000).toISOString(),
            credits_earned: 250,
          },
          {
            id: "r3",
            email: "carol@example.com",
            status: "signed_up",
            invited_at: new Date(Date.now() - 259200_000).toISOString(),
            converted_at: null,
            credits_earned: 0,
          },
          {
            id: "r4",
            email: "dan@example.com",
            status: "converted",
            invited_at: new Date(Date.now() - 172800_000).toISOString(),
            converted_at: new Date(Date.now() - 86400_000).toISOString(),
            credits_earned: 250,
          },
          {
            id: "r5",
            email: "eve@example.com",
            status: "pending",
            invited_at: new Date(Date.now() - 86400_000).toISOString(),
            converted_at: null,
            credits_earned: 0,
          },
        ]);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [user]);

  function handleCopyLink() {
    if (!stats) return;
    navigator.clipboard.writeText(stats.referral_link);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  function handleShareTwitter() {
    if (!stats) return;
    const text = encodeURIComponent(
      `I've been using Codey to generate production-ready code from plain English. Try it out with my referral link and get bonus credits:`
    );
    const url = encodeURIComponent(stats.referral_link);
    window.open(`https://twitter.com/intent/tweet?text=${text}&url=${url}`, "_blank");
  }

  function handleShareLinkedIn() {
    if (!stats) return;
    const url = encodeURIComponent(stats.referral_link);
    window.open(`https://www.linkedin.com/sharing/share-offsite/?url=${url}`, "_blank");
  }

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-codey-green" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      {/* Header */}
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold text-codey-text">
          <Gift className="h-6 w-6 text-codey-green" />
          Referrals
        </h1>
        <p className="mt-1 text-sm text-codey-text-dim">
          Invite other developers and earn 250 credits for every referral that upgrades.
        </p>
      </div>

      {/* Referral Link Card */}
      {stats && (
        <div className="rounded-xl border border-codey-green/30 bg-codey-card p-6">
          <h2 className="text-sm font-semibold text-codey-text">Your Referral Link</h2>
          <div className="mt-3 flex gap-2">
            <div className="flex flex-1 items-center rounded-lg border border-codey-border bg-codey-bg px-4 py-3">
              <span className="flex-1 truncate font-mono text-sm text-codey-text">
                {stats.referral_link}
              </span>
            </div>
            <button
              onClick={handleCopyLink}
              className="flex items-center gap-1.5 rounded-lg bg-codey-green px-4 py-2 text-sm font-semibold text-codey-bg transition-all hover:shadow-glow-green"
            >
              {copied ? (
                <>
                  <Check className="h-4 w-4" />
                  Copied
                </>
              ) : (
                <>
                  <Copy className="h-4 w-4" />
                  Copy
                </>
              )}
            </button>
          </div>

          {/* Share Buttons */}
          <div className="mt-4 flex flex-wrap gap-2">
            <button
              onClick={handleShareTwitter}
              className="flex items-center gap-1.5 rounded-lg border border-codey-border px-3 py-2 text-xs font-medium text-codey-text-dim transition-colors hover:bg-codey-card-hover hover:text-codey-text"
            >
              <ExternalLink className="h-3 w-3" />
              Share on X
            </button>
            <button
              onClick={handleShareLinkedIn}
              className="flex items-center gap-1.5 rounded-lg border border-codey-border px-3 py-2 text-xs font-medium text-codey-text-dim transition-colors hover:bg-codey-card-hover hover:text-codey-text"
            >
              <ExternalLink className="h-3 w-3" />
              Share on LinkedIn
            </button>
            <button
              onClick={handleCopyLink}
              className="flex items-center gap-1.5 rounded-lg border border-codey-border px-3 py-2 text-xs font-medium text-codey-text-dim transition-colors hover:bg-codey-card-hover hover:text-codey-text"
            >
              <Copy className="h-3 w-3" />
              Copy Link
            </button>
          </div>
        </div>
      )}

      {/* Stats Cards */}
      {stats && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div className="rounded-xl border border-codey-border bg-codey-card p-5">
            <div className="flex items-center gap-2">
              <Send className="h-4 w-4 text-codey-text-dim" />
              <span className="text-xs font-medium text-codey-text-muted">Referrals Sent</span>
            </div>
            <p className="mt-2 text-2xl font-bold text-codey-text">{stats.total_sent}</p>
          </div>
          <div className="rounded-xl border border-codey-border bg-codey-card p-5">
            <div className="flex items-center gap-2">
              <UserCheck className="h-4 w-4 text-codey-green" />
              <span className="text-xs font-medium text-codey-text-muted">Converted</span>
            </div>
            <p className="mt-2 text-2xl font-bold text-codey-text">{stats.total_converted}</p>
            {stats.total_sent > 0 && (
              <p className="mt-1 text-xs text-codey-text-dim">
                {((stats.total_converted / stats.total_sent) * 100).toFixed(0)}% conversion rate
              </p>
            )}
          </div>
          <div className="rounded-xl border border-codey-border bg-codey-card p-5">
            <div className="flex items-center gap-2">
              <Zap className="h-4 w-4 text-codey-green" />
              <span className="text-xs font-medium text-codey-text-muted">Credits Earned</span>
            </div>
            <p className="mt-2 text-2xl font-bold text-codey-green">
              {stats.credits_earned.toLocaleString()}
            </p>
          </div>
        </div>
      )}

      {/* How It Works */}
      <div className="rounded-xl border border-codey-border bg-codey-card p-6">
        <h2 className="text-sm font-semibold text-codey-text">How It Works</h2>
        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
          {[
            {
              step: "1",
              title: "Share your link",
              desc: "Send your referral link to another developer",
            },
            {
              step: "2",
              title: "They sign up",
              desc: "They create an account and get bonus free credits",
            },
            {
              step: "3",
              title: "You both earn",
              desc: "When they upgrade to Pro, you both get 250 credits",
            },
          ].map(({ step, title, desc }) => (
            <div key={step} className="flex items-start gap-3">
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-codey-green/20 text-sm font-bold text-codey-green">
                {step}
              </div>
              <div>
                <p className="text-sm font-medium text-codey-text">{title}</p>
                <p className="mt-0.5 text-xs text-codey-text-dim">{desc}</p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Referral List */}
      <div className="rounded-xl border border-codey-border bg-codey-card">
        <div className="border-b border-codey-border/50 px-5 py-4">
          <h2 className="text-sm font-semibold text-codey-text">Your Referrals</h2>
        </div>

        {referrals.length === 0 ? (
          <div className="px-5 py-12 text-center text-sm text-codey-text-dim">
            No referrals yet. Share your link to start earning credits.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-codey-border text-xs text-codey-text-muted">
                  <th className="px-5 py-3 font-medium">Email</th>
                  <th className="px-5 py-3 font-medium">Status</th>
                  <th className="px-5 py-3 font-medium">Invited</th>
                  <th className="px-5 py-3 font-medium">Converted</th>
                  <th className="px-5 py-3 font-medium text-right">Credits</th>
                </tr>
              </thead>
              <tbody>
                {referrals.map((ref) => {
                  const badge = statusBadge(ref.status);
                  return (
                    <tr
                      key={ref.id}
                      className="border-b border-codey-border/50 transition-colors hover:bg-codey-card-hover"
                    >
                      <td className="px-5 py-3 text-codey-text">{ref.email}</td>
                      <td className="px-5 py-3">
                        <span
                          className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${badge.color}`}
                        >
                          {badge.label}
                        </span>
                      </td>
                      <td className="px-5 py-3 text-xs text-codey-text-muted">
                        {formatDate(ref.invited_at)}
                      </td>
                      <td className="px-5 py-3 text-xs text-codey-text-muted">
                        {ref.converted_at ? formatDate(ref.converted_at) : "--"}
                      </td>
                      <td className="px-5 py-3 text-right">
                        {ref.credits_earned > 0 ? (
                          <span className="font-mono font-medium text-codey-green">
                            +{ref.credits_earned}
                          </span>
                        ) : (
                          <span className="text-codey-text-muted">--</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
