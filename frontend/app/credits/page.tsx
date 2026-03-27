"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import {
  api,
  type CreditBalance,
  type CreditTransaction,
} from "@/lib/api";
import { StripeProvider, PaymentForm } from "@/lib/stripe";
import DashboardLayout from "@/components/layout/DashboardLayout";
import { ProtectedRoute } from "@/lib/auth";
import {
  Zap,
  Plus,
  Minus,
  ArrowRight,
  RefreshCw,
  Clock,
  Loader2,
  Check,
  X,
  CreditCard,
} from "lucide-react";

// ── Types ─────────────────────────────────────────────────────────────────────

interface TopupPackage {
  credits: number;
  price: number;
  perCredit: string;
  popular?: boolean;
}

const TOPUP_PACKAGES: TopupPackage[] = [
  { credits: 100, price: 5, perCredit: "$0.050" },
  { credits: 500, price: 20, perCredit: "$0.040", popular: true },
  { credits: 1500, price: 50, perCredit: "$0.033" },
  { credits: 5000, price: 140, perCredit: "$0.028" },
];

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function txTypeLabel(type: CreditTransaction["type"]): {
  label: string;
  color: string;
  icon: typeof Plus;
} {
  switch (type) {
    case "usage":
      return { label: "Usage", color: "text-codey-red", icon: Minus };
    case "topup":
      return { label: "Top-up", color: "text-codey-green", icon: Plus };
    case "plan_refresh":
      return { label: "Plan refresh", color: "text-codey-green", icon: RefreshCw };
    case "refund":
      return { label: "Refund", color: "text-codey-green", icon: Plus };
    default:
      return { label: type, color: "text-codey-text-dim", icon: Minus };
  }
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function CreditsPage() {
  const { user } = useAuth();
  const [balance, setBalance] = useState<CreditBalance | null>(null);
  const [transactions, setTransactions] = useState<CreditTransaction[]>([]);
  const [txTotal, setTxTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  // Topup flow
  const [selectedPackage, setSelectedPackage] = useState<TopupPackage | null>(null);
  const [clientSecret, setClientSecret] = useState<string | null>(null);
  const [topupLoading, setTopupLoading] = useState(false);
  const [topupSuccess, setTopupSuccess] = useState(false);
  const [topupError, setTopupError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const [bal, history] = await Promise.all([
          api.getCredits(),
          api.getCreditHistory({ limit: 20 }),
        ]);
        setBalance(bal);
        setTransactions(history.transactions);
        setTxTotal(history.total);
      } catch (err) {
        console.error("Failed to load credit data:", err);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  async function handleBuyPackage(pkg: TopupPackage) {
    setSelectedPackage(pkg);
    setTopupLoading(true);
    setTopupError(null);
    try {
      const result = await api.createTopup(pkg.credits);
      setClientSecret(result.client_secret);
    } catch (err) {
      setTopupError("Failed to initiate purchase. Please try again.");
      setSelectedPackage(null);
    } finally {
      setTopupLoading(false);
    }
  }

  async function handlePaymentSuccess(paymentIntentId: string) {
    setTopupSuccess(true);
    setClientSecret(null);
    // Refresh balance
    try {
      const [bal, history] = await Promise.all([
        api.getCredits(),
        api.getCreditHistory({ limit: 20 }),
      ]);
      setBalance(bal);
      setTransactions(history.transactions);
      setTxTotal(history.total);
    } catch {}
    setTimeout(() => {
      setTopupSuccess(false);
      setSelectedPackage(null);
    }, 3000);
  }

  function handleCancelTopup() {
    setClientSecret(null);
    setSelectedPackage(null);
    setTopupError(null);
  }

  if (loading) {
    return (
      <ProtectedRoute>
        <DashboardLayout>
          <div className="flex h-64 items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-codey-green" />
          </div>
        </DashboardLayout>
      </ProtectedRoute>
    );
  }

  const planTotal = balance?.plan_credits ?? 0;
  const topupTotal = balance?.topup_credits ?? 0;
  const totalCredits = planTotal + topupTotal;
  const remaining = balance?.credits_remaining ?? 0;
  const used = totalCredits - remaining;
  const usagePercent = totalCredits > 0 ? Math.round((used / totalCredits) * 100) : 0;

  return (
    <ProtectedRoute>
      <DashboardLayout>
        <div className="mx-auto max-w-5xl space-y-6">
          <div>
            <h1 className="text-2xl font-bold text-codey-text">Credits</h1>
            <p className="mt-1 text-sm text-codey-text-dim">
              Manage your credit balance and purchase additional credits.
            </p>
          </div>

          {/* ── Balance Card ───────────────────────────────────────────── */}
          <div className="rounded-xl border border-codey-border bg-codey-card p-6">
            <div className="flex flex-col gap-6 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-xs font-medium uppercase tracking-wider text-codey-text-muted">
                  Current Balance
                </p>
                <p className="mt-2 text-4xl font-black text-codey-text">
                  {remaining.toLocaleString()}
                </p>
                <div className="mt-2 flex items-center gap-4 text-xs text-codey-text-dim">
                  <span>
                    Plan: {planTotal.toLocaleString()} credits
                  </span>
                  {topupTotal > 0 && (
                    <>
                      <span className="text-codey-text-muted">+</span>
                      <span>
                        Top-up: {topupTotal.toLocaleString()} credits
                      </span>
                    </>
                  )}
                </div>
              </div>
              {balance?.next_refresh_at && (
                <div className="text-right">
                  <p className="text-xs text-codey-text-muted">Next refresh</p>
                  <p className="text-sm font-medium text-codey-text">
                    {new Date(balance.next_refresh_at).toLocaleDateString("en-US", {
                      month: "long",
                      day: "numeric",
                    })}
                  </p>
                </div>
              )}
            </div>

            {/* Usage bar */}
            <div className="mt-5">
              <div className="flex items-center justify-between text-xs text-codey-text-muted">
                <span>{used.toLocaleString()} used</span>
                <span>{totalCredits.toLocaleString()} total</span>
              </div>
              <div className="mt-1.5 h-3 w-full overflow-hidden rounded-full bg-codey-bg">
                <div
                  className={`h-full rounded-full transition-all ${
                    usagePercent > 90
                      ? "bg-codey-red"
                      : usagePercent > 70
                        ? "bg-codey-yellow"
                        : "bg-codey-green"
                  }`}
                  style={{ width: `${usagePercent}%` }}
                />
              </div>
            </div>
          </div>

          {/* ── Top-up Packages ─────────────────────────────────────────── */}
          <div>
            <h2 className="text-sm font-semibold text-codey-text">Top Up Credits</h2>
            <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {TOPUP_PACKAGES.map((pkg) => (
                <div
                  key={pkg.credits}
                  className={`relative rounded-xl border p-5 transition-all ${
                    selectedPackage?.credits === pkg.credits
                      ? "border-codey-green bg-codey-green/5"
                      : "border-codey-border bg-codey-card hover:border-codey-border-light"
                  }`}
                >
                  {pkg.popular && (
                    <span className="absolute -top-2.5 left-4 rounded-full bg-codey-green px-2.5 py-0.5 text-[10px] font-bold text-codey-bg">
                      POPULAR
                    </span>
                  )}
                  <p className="text-2xl font-bold text-codey-text">
                    {pkg.credits.toLocaleString()}
                  </p>
                  <p className="text-xs text-codey-text-muted">credits</p>
                  <p className="mt-3 text-lg font-semibold text-codey-text">
                    ${pkg.price}
                  </p>
                  <p className="text-xs text-codey-text-dim">
                    {pkg.perCredit}/credit
                  </p>
                  <button
                    onClick={() => handleBuyPackage(pkg)}
                    disabled={topupLoading && selectedPackage?.credits === pkg.credits}
                    className="mt-4 w-full rounded-lg bg-codey-green px-4 py-2 text-sm font-semibold text-codey-bg transition-all hover:shadow-glow-green disabled:opacity-50"
                  >
                    {topupLoading && selectedPackage?.credits === pkg.credits ? (
                      <Loader2 className="mx-auto h-4 w-4 animate-spin" />
                    ) : (
                      "Buy"
                    )}
                  </button>
                </div>
              ))}
            </div>
          </div>

          {/* ── Stripe Payment Inline ──────────────────────────────────── */}
          {clientSecret && selectedPackage && (
            <div className="rounded-xl border border-codey-green/30 bg-codey-card p-6">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-semibold text-codey-text">
                    Purchase {selectedPackage.credits.toLocaleString()} credits
                  </h3>
                  <p className="text-xs text-codey-text-dim">
                    ${selectedPackage.price} one-time payment
                  </p>
                </div>
                <button
                  onClick={handleCancelTopup}
                  className="rounded-lg p-1.5 text-codey-text-muted hover:bg-codey-card-hover"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
              <StripeProvider clientSecret={clientSecret}>
                <PaymentForm
                  onSuccess={handlePaymentSuccess}
                  onError={(err) => setTopupError(err)}
                  submitLabel={`Pay $${selectedPackage.price}`}
                />
              </StripeProvider>
            </div>
          )}

          {/* ── Success message ────────────────────────────────────────── */}
          {topupSuccess && (
            <div className="rounded-lg border border-codey-green/30 bg-codey-green-glow px-4 py-3 text-sm text-codey-green">
              <Check className="mr-2 inline h-4 w-4" />
              Credits added successfully!
            </div>
          )}

          {/* ── Error message ──────────────────────────────────────────── */}
          {topupError && (
            <div className="rounded-lg border border-codey-red/30 bg-codey-red-glow px-4 py-3 text-sm text-codey-red">
              {topupError}
            </div>
          )}

          {/* ── Transaction History ─────────────────────────────────────── */}
          <div className="rounded-xl border border-codey-border bg-codey-card">
            <div className="border-b border-codey-border px-5 py-4">
              <h2 className="text-sm font-semibold text-codey-text">
                Transaction History
              </h2>
            </div>

            {transactions.length === 0 ? (
              <div className="px-5 py-12 text-center text-sm text-codey-text-dim">
                No transactions yet.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr className="border-b border-codey-border text-xs text-codey-text-muted">
                      <th className="px-5 py-3 font-medium">Date</th>
                      <th className="px-5 py-3 font-medium">Type</th>
                      <th className="px-5 py-3 font-medium">Description</th>
                      <th className="px-5 py-3 font-medium text-right">Amount</th>
                    </tr>
                  </thead>
                  <tbody>
                    {transactions.map((tx) => {
                      const info = txTypeLabel(tx.type);
                      const TxIcon = info.icon;
                      const isPositive = tx.amount > 0;

                      return (
                        <tr
                          key={tx.id}
                          className="border-b border-codey-border/50 hover:bg-codey-card-hover"
                        >
                          <td className="whitespace-nowrap px-5 py-3 text-xs text-codey-text-muted">
                            {formatDate(tx.created_at)}
                          </td>
                          <td className="px-5 py-3">
                            <span
                              className={`flex items-center gap-1.5 text-xs font-medium ${info.color}`}
                            >
                              <TxIcon className="h-3 w-3" />
                              {info.label}
                            </span>
                          </td>
                          <td className="max-w-[250px] truncate px-5 py-3 text-codey-text-dim">
                            {tx.description}
                          </td>
                          <td
                            className={`px-5 py-3 text-right font-mono font-medium ${
                              isPositive ? "text-codey-green" : "text-codey-red"
                            }`}
                          >
                            {isPositive ? "+" : ""}
                            {tx.amount.toLocaleString()}
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
      </DashboardLayout>
    </ProtectedRoute>
  );
}
