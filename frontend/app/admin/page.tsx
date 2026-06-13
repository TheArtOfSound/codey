"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import {
  Shield,
  Users,
  DollarSign,
  Zap,
  Server,
  TrendingUp,
  UserPlus,
  Activity,
  Search,
  X,
  Loader2,
  Check,
  Megaphone,
  BarChart3,
  CreditCard,
} from "lucide-react";

// ── Types ─────────────────────────────────────────────────────────────────────

interface AdminStats {
  total_users: number;
  mrr_usd: number;
  total_credits_used: number;
  total_api_cost_usd: number;
  gross_margin: number;
  total_sessions: number;
  signups_last_30_days: number;
  conversion_rate: number;
}

interface AdminUser {
  id: string;
  email: string;
  plan: string;
  credits_remaining: number;
  created_at: string;
  last_active: string | null;
}

// ── Stat Card ─────────────────────────────────────────────────────────────────

function StatCard({
  icon: Icon,
  label,
  value,
  subtext,
  color = "text-codey-green",
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string;
  subtext?: string;
  color?: string;
}) {
  return (
    <div className="rounded-xl border border-codey-border bg-codey-card p-5 transition-colors hover:bg-codey-card-hover">
      <div className="flex items-center gap-2">
        <Icon className={`h-4 w-4 ${color}`} />
        <span className="text-xs font-medium text-codey-text-muted">{label}</span>
      </div>
      <p className="mt-2 text-2xl font-bold text-codey-text">{value}</p>
      {subtext && <p className="mt-1 text-xs text-codey-text-dim">{subtext}</p>}
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function AdminPage() {
  const { user } = useAuth();
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<AdminUser[]>([]);
  const [searching, setSearching] = useState(false);

  // Credit adjustment modal
  const [adjustUser, setAdjustUser] = useState<AdminUser | null>(null);
  const [adjustAmount, setAdjustAmount] = useState("");
  const [adjustReason, setAdjustReason] = useState("");
  const [adjusting, setAdjusting] = useState(false);
  const [adjustSuccess, setAdjustSuccess] = useState(false);
  const [adjustError, setAdjustError] = useState<string | null>(null);

  // Announcement banner
  const [bannerText, setBannerText] = useState("");
  const [bannerActive, setBannerActive] = useState(false);
  const [bannerSaving, setBannerSaving] = useState(false);
  const [bannerSaved, setBannerSaved] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const data = await api.get<AdminStats>("/admin/stats");
        setStats(data);
      } catch (err) {
        console.error("Failed to load admin stats:", err);
        setError("Admin access required or admin data unavailable.");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  async function handleSearch() {
    if (!searchQuery.trim()) return;
    setSearching(true);
    try {
      const results = await api.get<AdminUser[]>(`/admin/users?search=${encodeURIComponent(searchQuery)}`);
      setSearchResults(results);
    } catch (err) {
      console.error("User search failed:", err);
      setSearchResults([]);
    } finally {
      setSearching(false);
    }
  }

  async function handleAdjustCredits() {
    if (!adjustUser || !adjustAmount) return;
    setAdjusting(true);
    setAdjustError(null);
    try {
      await api.post(`/admin/users/${adjustUser.id}/credits`, {
        amount: parseInt(adjustAmount),
        reason: adjustReason,
      });
      setAdjustSuccess(true);
      setTimeout(() => {
        setAdjustSuccess(false);
        setAdjustUser(null);
        setAdjustAmount("");
        setAdjustReason("");
      }, 1500);
    } catch (err) {
      console.error("Failed to adjust credits:", err);
      setAdjustError("Failed to adjust credits.");
    } finally {
      setAdjusting(false);
    }
  }

  async function handleSaveBanner() {
    setBannerSaving(true);
    try {
      await api.post("/admin/announcement", {
        message: bannerActive ? bannerText : null,
        level: "info",
      });
    } catch (err) {
      console.error("Failed to save announcement:", err);
    }
    setBannerSaved(true);
    setTimeout(() => setBannerSaved(false), 2000);
    setBannerSaving(false);
  }

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-codey-green" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="mx-auto max-w-3xl rounded-xl border border-codey-red/30 bg-codey-card p-6">
        <h1 className="text-xl font-semibold text-codey-red">Admin Dashboard</h1>
        <p className="mt-2 text-sm text-codey-text-dim">{error}</p>
      </div>
    );
  }

  const revenueShare =
    stats && stats.mrr_usd > 0
      ? Math.max(8, Math.min(100, Math.round((stats.total_api_cost_usd / stats.mrr_usd) * 100)))
      : 0;
  const conversionShare = stats ? Math.max(6, Math.min(100, Math.round(stats.conversion_rate))) : 0;
  const signupMomentum =
    stats && stats.total_users > 0
      ? Math.max(6, Math.min(100, Math.round((stats.signups_last_30_days / stats.total_users) * 100)))
      : 0;

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      {/* Header */}
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold text-codey-text">
          <Shield className="h-6 w-6 text-codey-green" />
          Admin Dashboard
        </h1>
        <p className="mt-1 text-sm text-codey-text-dim">
          Platform overview and management tools.
        </p>
      </div>

      {/* Stats Grid */}
      {stats && (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <StatCard
            icon={Users}
            label="Total Users"
            value={stats.total_users.toLocaleString()}
            subtext={`${stats.signups_last_30_days} signups in 30 days`}
          />
          <StatCard
            icon={DollarSign}
            label="MRR"
            value={`$${stats.mrr_usd.toLocaleString()}`}
            color="text-codey-green"
          />
          <StatCard
            icon={Zap}
            label="Credits Used"
            value={stats.total_credits_used.toLocaleString()}
            subtext={`${stats.total_sessions} sessions`}
          />
          <StatCard
            icon={Server}
            label="API Costs"
            value={`$${stats.total_api_cost_usd.toLocaleString()}`}
            color="text-codey-yellow"
          />
          <StatCard
            icon={TrendingUp}
            label="Margin"
            value={`${stats.gross_margin}%`}
            color={stats.gross_margin > 50 ? "text-codey-green" : "text-codey-yellow"}
          />
          <StatCard
            icon={Activity}
            label="Sessions"
            value={stats.total_sessions.toLocaleString()}
          />
          <StatCard
            icon={UserPlus}
            label="Signups (30d)"
            value={stats.signups_last_30_days.toLocaleString()}
          />
          <StatCard
            icon={CreditCard}
            label="Conversion Rate"
            value={`${stats.conversion_rate}%`}
            subtext="Free to Paid"
          />
        </div>
      )}

      {/* Snapshot Charts */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="rounded-xl border border-codey-border bg-codey-card p-6">
          <div className="flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-codey-text-dim" />
            <h2 className="text-sm font-semibold text-codey-text">Revenue Snapshot</h2>
          </div>
          <div className="mt-5 space-y-4">
            <div>
              <div className="flex items-center justify-between text-xs text-codey-text-muted">
                <span>MRR</span>
                <span>${stats?.mrr_usd.toLocaleString()}</span>
              </div>
              <div className="mt-1.5 h-3 overflow-hidden rounded-full bg-codey-bg">
                <div className="h-full w-full rounded-full bg-codey-green" />
              </div>
            </div>
            <div>
              <div className="flex items-center justify-between text-xs text-codey-text-muted">
                <span>API cost share</span>
                <span>{revenueShare}% of MRR</span>
              </div>
              <div className="mt-1.5 h-3 overflow-hidden rounded-full bg-codey-bg">
                <div
                  className="h-full rounded-full bg-codey-yellow transition-all"
                  style={{ width: `${revenueShare}%` }}
                />
              </div>
            </div>
            <div className="rounded-lg border border-codey-border bg-codey-bg px-4 py-3 text-sm text-codey-text-dim">
              Gross margin is currently <span className="font-semibold text-codey-text">{stats?.gross_margin}%</span>.
            </div>
          </div>
        </div>

        <div className="rounded-xl border border-codey-border bg-codey-card p-6">
          <div className="flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-codey-text-dim" />
            <h2 className="text-sm font-semibold text-codey-text">Growth Snapshot</h2>
          </div>
          <div className="mt-5 space-y-4">
            <div>
              <div className="flex items-center justify-between text-xs text-codey-text-muted">
                <span>Free-to-paid conversion</span>
                <span>{stats?.conversion_rate}%</span>
              </div>
              <div className="mt-1.5 h-3 overflow-hidden rounded-full bg-codey-bg">
                <div
                  className="h-full rounded-full bg-codey-green transition-all"
                  style={{ width: `${conversionShare}%` }}
                />
              </div>
            </div>
            <div>
              <div className="flex items-center justify-between text-xs text-codey-text-muted">
                <span>30-day signup momentum</span>
                <span>{signupMomentum}% of total users</span>
              </div>
              <div className="mt-1.5 h-3 overflow-hidden rounded-full bg-codey-bg">
                <div
                  className="h-full rounded-full bg-codey-yellow transition-all"
                  style={{ width: `${signupMomentum}%` }}
                />
              </div>
            </div>
            <div className="rounded-lg border border-codey-border bg-codey-bg px-4 py-3 text-sm text-codey-text-dim">
              {stats?.signups_last_30_days.toLocaleString()} new accounts were created in the last 30 days.
            </div>
          </div>
        </div>
      </div>

      {/* User Search */}
      <div className="rounded-xl border border-codey-border bg-codey-card">
        <div className="border-b border-codey-border/50 px-5 py-4">
          <h2 className="text-sm font-semibold text-codey-text">User Search</h2>
        </div>
        <div className="px-5 py-4">
          <div className="flex gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-codey-text-muted" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSearch()}
                placeholder="Search by email, user ID..."
                className="w-full rounded-lg border border-codey-border bg-codey-bg py-2.5 pl-10 pr-4 text-sm text-codey-text placeholder:text-codey-text-muted focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
              />
            </div>
            <button
              onClick={handleSearch}
              disabled={searching || !searchQuery.trim()}
              className="flex items-center gap-1.5 rounded-lg bg-codey-green px-4 py-2 text-sm font-semibold text-codey-bg hover:shadow-glow-green disabled:opacity-50"
            >
              {searching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
              Search
            </button>
          </div>

          {/* Search Results */}
          {searchResults.length > 0 && (
            <div className="mt-4 overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-codey-border text-xs text-codey-text-muted">
                    <th className="py-2 pr-4 font-medium">Email</th>
                    <th className="py-2 pr-4 font-medium">Plan</th>
                    <th className="py-2 pr-4 font-medium">Credits</th>
                    <th className="py-2 pr-4 font-medium">Last Active</th>
                    <th className="py-2 pr-4 font-medium">Joined</th>
                    <th className="py-2 font-medium text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {searchResults.map((u) => (
                    <tr key={u.id} className="border-b border-codey-border/50 hover:bg-codey-card-hover">
                      <td className="py-3 pr-4 text-codey-text">{u.email}</td>
                      <td className="py-3 pr-4">
                        <span className="inline-flex rounded-full bg-codey-green/20 px-2 py-0.5 text-xs font-medium capitalize text-codey-green">
                          {u.plan}
                        </span>
                      </td>
                      <td className="py-3 pr-4 font-mono text-codey-text-dim">
                        {u.credits_remaining.toLocaleString()}
                      </td>
                      <td className="py-3 pr-4 text-codey-text-dim">
                        {u.last_active ? new Date(u.last_active).toLocaleDateString() : "--"}
                      </td>
                      <td className="py-3 pr-4 text-xs text-codey-text-muted">
                        {new Date(u.created_at).toLocaleDateString()}
                      </td>
                      <td className="py-3 text-right">
                        <button
                          onClick={() => setAdjustUser(u)}
                          className="rounded-lg border border-codey-border px-2.5 py-1 text-xs text-codey-text-dim hover:bg-codey-card-hover hover:text-codey-green"
                        >
                          Adjust Credits
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {/* Announcement Banner Editor */}
      <div className="rounded-xl border border-codey-border bg-codey-card">
        <div className="border-b border-codey-border/50 px-5 py-4">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-codey-text">
            <Megaphone className="h-4 w-4 text-codey-text-dim" />
            Announcement Banner
          </h2>
        </div>
        <div className="space-y-4 px-5 py-4">
          <div>
            <label className="text-xs font-medium text-codey-text-dim">Banner Text</label>
            <input
              type="text"
              value={bannerText}
              onChange={(e) => setBannerText(e.target.value)}
              placeholder="e.g., Codey v1.1 is live! Check the changelog for details."
              className="mt-1 w-full rounded-lg border border-codey-border bg-codey-bg px-4 py-2.5 text-sm text-codey-text placeholder:text-codey-text-muted focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
            />
          </div>

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <button
                onClick={() => setBannerActive(!bannerActive)}
                className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors ${
                  bannerActive ? "bg-codey-green" : "bg-codey-border"
                }`}
              >
                <span
                  className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${
                    bannerActive ? "translate-x-5" : "translate-x-0"
                  }`}
                />
              </button>
              <span className="text-sm text-codey-text-dim">
                {bannerActive ? "Banner is live" : "Banner is hidden"}
              </span>
            </div>

            <button
              onClick={handleSaveBanner}
              disabled={bannerSaving}
              className="flex items-center gap-2 rounded-lg bg-codey-green px-4 py-2 text-sm font-semibold text-codey-bg hover:shadow-glow-green disabled:opacity-50"
            >
              {bannerSaved ? (
                <>
                  <Check className="h-4 w-4" />
                  Saved
                </>
              ) : bannerSaving ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                "Save Banner"
              )}
            </button>
          </div>

          {/* Preview */}
          {bannerText && bannerActive && (
            <div className="rounded-lg border border-codey-green/30 bg-codey-green-glow px-4 py-3">
              <p className="text-xs font-medium text-codey-green">Preview:</p>
              <p className="mt-1 text-sm text-codey-text">{bannerText}</p>
            </div>
          )}
        </div>
      </div>

      {/* Credit Adjustment Modal */}
      {adjustUser && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/60" onClick={() => setAdjustUser(null)} />
          <div className="relative z-10 w-full max-w-md animate-fade-in rounded-2xl border border-codey-border bg-codey-card p-6 shadow-2xl">
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-semibold text-codey-text">Adjust Credits</h3>
              <button
                onClick={() => setAdjustUser(null)}
                className="rounded-lg p-1.5 text-codey-text-muted hover:bg-codey-card-hover"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <p className="mt-1 text-sm text-codey-text-dim">{adjustUser.email}</p>
            <p className="text-xs text-codey-text-muted">
              Current balance: {adjustUser.credits_remaining.toLocaleString()} credits
            </p>

            <div className="mt-4 space-y-3">
              {adjustError && (
                <div className="rounded-lg border border-codey-red/30 bg-codey-red-glow px-3 py-2 text-xs text-codey-red">
                  {adjustError}
                </div>
              )}
              <div>
                <label className="text-xs font-medium text-codey-text-dim">
                  Amount (positive to add, negative to remove)
                </label>
                <input
                  type="number"
                  value={adjustAmount}
                  onChange={(e) => setAdjustAmount(e.target.value)}
                  placeholder="e.g., 500 or -100"
                  className="mt-1 w-full rounded-lg border border-codey-border bg-codey-bg px-4 py-2.5 text-sm text-codey-text placeholder:text-codey-text-muted focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
                />
              </div>
              <div>
                <label className="text-xs font-medium text-codey-text-dim">Reason</label>
                <input
                  type="text"
                  value={adjustReason}
                  onChange={(e) => setAdjustReason(e.target.value)}
                  placeholder="e.g., Customer support compensation"
                  className="mt-1 w-full rounded-lg border border-codey-border bg-codey-bg px-4 py-2.5 text-sm text-codey-text placeholder:text-codey-text-muted focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
                />
              </div>
            </div>

            <div className="mt-5 flex justify-end gap-3">
              <button
                onClick={() => setAdjustUser(null)}
                className="rounded-lg border border-codey-border px-4 py-2 text-sm text-codey-text-dim hover:bg-codey-card-hover"
              >
                Cancel
              </button>
              <button
                onClick={handleAdjustCredits}
                disabled={adjusting || !adjustAmount}
                className="flex items-center gap-2 rounded-lg bg-codey-green px-4 py-2 text-sm font-semibold text-codey-bg hover:shadow-glow-green disabled:opacity-50"
              >
                {adjustSuccess ? (
                  <>
                    <Check className="h-4 w-4" />
                    Done
                  </>
                ) : adjusting ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  "Apply Adjustment"
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
