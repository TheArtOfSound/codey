"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import DashboardLayout from "@/components/layout/DashboardLayout";
import { ProtectedRoute, useAuth } from "@/lib/auth";
import {
  api,
  type CancelSubscriptionResult,
  type Invoice,
  type PaymentMethod,
  type Plan,
} from "@/lib/api";
import { PaymentForm, SetupForm, StripeProvider } from "@/lib/stripe";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  CreditCard,
  ExternalLink,
  Loader2,
  RefreshCw,
  Shield,
  X,
} from "lucide-react";

const STRIPE_CONFIGURED = Boolean(process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY);

function formatMoney(cents: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: cents % 100 === 0 ? 0 : 2,
  }).format(cents / 100);
}

function formatInvoiceDate(date: string): string {
  return new Date(date).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function getErrorMessage(error: unknown, fallback: string): string {
  const apiError = error as { detail?: string };
  return apiError.detail || fallback;
}

function BillingPageContent() {
  const { user, refreshUser } = useAuth();
  const searchParams = useSearchParams();

  const requestedPlan = searchParams.get("subscribe");
  const referralId = searchParams.get("ref");
  const setupSuccess = searchParams.get("setup") === "success";
  const paymentSuccess = searchParams.get("payment") === "success";
  const returnedSubscriptionId = searchParams.get("subscription");

  const [plans, setPlans] = useState<Plan[]>([]);
  const [paymentMethods, setPaymentMethods] = useState<PaymentMethod[]>([]);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(requestedPlan);
  const [checkoutMode, setCheckoutMode] = useState<"setup" | "payment" | null>(null);
  const [clientSecret, setClientSecret] = useState<string | null>(null);
  const [pendingSubscriptionId, setPendingSubscriptionId] = useState<string | null>(
    returnedSubscriptionId
  );
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cancelState, setCancelState] = useState<CancelSubscriptionResult | null>(null);
  const [removingMethodId, setRemovingMethodId] = useState<string | null>(null);
  const [handledSetupResume, setHandledSetupResume] = useState(false);
  const [handledPaymentResume, setHandledPaymentResume] = useState(false);

  const planMap = useMemo(
    () => new Map(plans.map((plan) => [plan.id, plan])),
    [plans]
  );
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const currentPlan = user ? planMap.get(user.plan) : undefined;
  const selectedPlan = selectedPlanId ? planMap.get(selectedPlanId) : undefined;

  useEffect(() => {
    if (requestedPlan) {
      setSelectedPlanId(requestedPlan);
    }
  }, [requestedPlan]);

  useEffect(() => {
    let active = true;

    async function loadData(showLoader = true) {
      if (showLoader) {
        setLoading(true);
      }
      const [plansResult, methodsResult, invoicesResult] = await Promise.allSettled([
        api.getPlans(),
        api.getPaymentMethods(),
        api.getInvoices(),
      ]);

      if (!active) {
        return;
      }

      if (plansResult.status === "fulfilled") {
        setPlans(plansResult.value);
      } else {
        setError(getErrorMessage(plansResult.reason, "Failed to load plans."));
      }

      setPaymentMethods(
        methodsResult.status === "fulfilled" ? methodsResult.value : []
      );
      setInvoices(invoicesResult.status === "fulfilled" ? invoicesResult.value : []);
      setLoading(false);
    }

    loadData();

    return () => {
      active = false;
    };
  }, []);

  async function reloadBillingData() {
    const [plansResult, methodsResult, invoicesResult] = await Promise.allSettled([
      api.getPlans(),
      api.getPaymentMethods(),
      api.getInvoices(),
    ]);

    if (plansResult.status === "fulfilled") {
      setPlans(plansResult.value);
    }
    setPaymentMethods(methodsResult.status === "fulfilled" ? methodsResult.value : []);
    setInvoices(invoicesResult.status === "fulfilled" ? invoicesResult.value : []);
  }

  function clearCheckout() {
    setCheckoutMode(null);
    setClientSecret(null);
    setPendingSubscriptionId(null);
  }

  function cleanBillingUrl(planId?: string | null) {
    if (typeof window === "undefined") {
      return;
    }
    const params = new URLSearchParams();
    const nextPlan = planId || selectedPlanId;
    if (nextPlan) {
      params.set("subscribe", nextPlan);
    }
    if (referralId) {
      params.set("ref", referralId);
    }
    const query = params.toString();
    window.history.replaceState({}, "", query ? `/settings/billing?${query}` : "/settings/billing");
  }

  async function finalizeSubscription(subscriptionId: string) {
    setActionLoading(true);
    setError(null);
    try {
      const result = await api.confirmSubscription(subscriptionId);
      await refreshUser();
      await reloadBillingData();
      clearCheckout();
      cleanBillingUrl(result.plan);
      setMessage(`Your ${result.plan} plan is now active.`);
    } catch (err: unknown) {
      setError(getErrorMessage(err, "Failed to confirm subscription."));
    } finally {
      setActionLoading(false);
    }
  }

  async function beginSubscription(planId: string) {
    setActionLoading(true);
    setError(null);
    setMessage(null);
    setSelectedPlanId(planId);

    try {
      const result = await api.subscribe(planId);
      if (result.type === "setup_required") {
        setCheckoutMode("setup");
        setClientSecret(result.client_secret);
        setPendingSubscriptionId(null);
      } else if (result.type === "payment_required") {
        setCheckoutMode("payment");
        setClientSecret(result.client_secret);
        setPendingSubscriptionId(result.subscription_id);
        cleanBillingUrl(planId);
      } else {
        await refreshUser();
        await reloadBillingData();
        clearCheckout();
        cleanBillingUrl(planId);
        setMessage(`Your ${planId} plan is now active.`);
      }
    } catch (err: unknown) {
      setError(getErrorMessage(err, "Failed to start subscription."));
    } finally {
      setActionLoading(false);
    }
  }

  async function handleSelectPlan(planId: string) {
    if (!user) {
      return;
    }

    if (planId === user.plan) {
      setSelectedPlanId(planId);
      setMessage("You are already on this plan.");
      return;
    }

    if (planId === "free") {
      setError("Use cancellation below to return to the free plan at the end of your billing period.");
      return;
    }

    if (user.plan !== "free") {
      setActionLoading(true);
      setError(null);
      setMessage(null);
      setSelectedPlanId(planId);
      try {
        const result = await api.changePlan(planId);
        await refreshUser();
        await reloadBillingData();
        clearCheckout();
        cleanBillingUrl(planId);
        setMessage(`Plan changed from ${result.old_plan} to ${result.new_plan}.`);
      } catch (err: unknown) {
        setError(getErrorMessage(err, "Failed to change plan."));
      } finally {
        setActionLoading(false);
      }
      return;
    }

    if (!STRIPE_CONFIGURED) {
      setError("Stripe is not configured for this deployment yet.");
      return;
    }

    await beginSubscription(planId);
  }

  async function handleSetupSuccess() {
    if (!selectedPlanId) {
      setError("No target plan selected for this subscription.");
      return;
    }
    await beginSubscription(selectedPlanId);
  }

  async function handlePaymentSuccess() {
    if (!pendingSubscriptionId) {
      setError("Missing subscription handoff. Please retry the checkout.");
      return;
    }
    await finalizeSubscription(pendingSubscriptionId);
  }

  async function handleCancelSubscription() {
    setActionLoading(true);
    setError(null);
    setMessage(null);
    try {
      const result = await api.cancelSubscription();
      setCancelState(result);
      await refreshUser();
      await reloadBillingData();
      setMessage("Cancellation scheduled. Your current access remains active until the billing period ends.");
    } catch (err: unknown) {
      setError(getErrorMessage(err, "Failed to cancel subscription."));
    } finally {
      setActionLoading(false);
    }
  }

  async function handleRemovePaymentMethod(methodId: string) {
    setRemovingMethodId(methodId);
    setError(null);
    try {
      await api.deletePaymentMethod(methodId);
      const methods = await api.getPaymentMethods();
      setPaymentMethods(methods);
    } catch (err: unknown) {
      setError(getErrorMessage(err, "Failed to remove payment method."));
    } finally {
      setRemovingMethodId(null);
    }
  }

  useEffect(() => {
    if (!selectedPlanId || !setupSuccess || handledSetupResume) {
      return;
    }
    setHandledSetupResume(true);
    void beginSubscription(selectedPlanId);
  }, [selectedPlanId, setupSuccess, handledSetupResume]);

  useEffect(() => {
    if (!returnedSubscriptionId || !paymentSuccess || handledPaymentResume) {
      return;
    }
    setHandledPaymentResume(true);
    void finalizeSubscription(returnedSubscriptionId);
  }, [returnedSubscriptionId, paymentSuccess, handledPaymentResume]);

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

  return (
    <ProtectedRoute>
      <DashboardLayout>
        <div className="mx-auto max-w-6xl space-y-6">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h1 className="text-2xl font-bold text-codey-text">Billing</h1>
              <p className="mt-1 text-sm text-codey-text-dim">
                Manage your subscription, saved cards, and invoices.
              </p>
            </div>
            <Link href="/credits" className="text-sm font-medium text-codey-green hover:underline">
              Need more credits instead?
            </Link>
          </div>

          {requestedPlan && selectedPlan && (
            <div className="rounded-xl border border-codey-green/30 bg-codey-green-glow px-5 py-4 text-sm text-codey-green">
              Complete your upgrade to <strong>{selectedPlan.name}</strong>.
              {referralId && " Your referral bonus will be applied after your first paid upgrade becomes active."}
            </div>
          )}

          {message && (
            <div className="rounded-xl border border-codey-green/30 bg-codey-green-glow px-5 py-4 text-sm text-codey-green">
              <Check className="mr-2 inline h-4 w-4" />
              {message}
            </div>
          )}

          {error && (
            <div className="rounded-xl border border-codey-red/30 bg-codey-red-glow px-5 py-4 text-sm text-codey-red">
              <AlertTriangle className="mr-2 inline h-4 w-4" />
              {error}
            </div>
          )}

          <div className="grid gap-6 lg:grid-cols-[1.1fr_0.9fr]">
            <div className="space-y-6">
              <section className="rounded-xl border border-codey-border bg-codey-card p-6">
                <div className="flex flex-col gap-5 md:flex-row md:items-start md:justify-between">
                  <div>
                    <p className="text-xs font-medium uppercase tracking-wider text-codey-text-muted">
                      Current subscription
                    </p>
                    <h2 className="mt-2 text-2xl font-bold text-codey-text">
                      {user?.plan_display_name || currentPlan?.name || user?.plan}
                    </h2>
                    <p className="mt-2 text-sm text-codey-text-dim">
                      Status: <span className="capitalize text-codey-text">{user?.plan_status}</span>
                      {user?.subscription_period_end &&
                        ` · Access until ${formatInvoiceDate(user.subscription_period_end)}`}
                    </p>
                  </div>
                  <div className="rounded-lg border border-codey-border bg-codey-bg px-4 py-3 text-sm">
                    <p className="text-codey-text-dim">Monthly allocation</p>
                    <p className="mt-1 text-lg font-semibold text-codey-text">
                      {(user?.monthly_allocation ?? currentPlan?.credits ?? user?.credits_remaining ?? 0).toLocaleString()} credits
                    </p>
                  </div>
                </div>

                {user?.plan !== "free" && (
                  <div className="mt-5 flex flex-wrap items-center gap-3">
                    <button
                      onClick={handleCancelSubscription}
                      disabled={actionLoading || user?.plan_status === "cancelling"}
                      className="rounded-lg border border-codey-red/30 px-4 py-2 text-sm text-codey-red hover:bg-codey-red-glow disabled:opacity-50"
                    >
                      {actionLoading ? "Working..." : user?.plan_status === "cancelling" ? "Cancellation scheduled" : "Cancel subscription"}
                    </button>
                    {cancelState?.access_until && (
                      <span className="text-xs text-codey-text-dim">
                        Access remains active until {formatInvoiceDate(cancelState.access_until)}.
                      </span>
                    )}
                  </div>
                )}
              </section>

              <section className="rounded-xl border border-codey-border bg-codey-card p-6">
                <div className="flex items-center justify-between">
                  <div>
                    <h2 className="text-sm font-semibold text-codey-text">Plans</h2>
                    <p className="mt-1 text-xs text-codey-text-muted">
                      Switch plans without leaving the product.
                    </p>
                  </div>
                  <button
                    onClick={() => {
                      setError(null);
                      setMessage(null);
                      void reloadBillingData();
                    }}
                    className="rounded-lg border border-codey-border p-2 text-codey-text-dim hover:bg-codey-card-hover"
                    aria-label="Refresh billing data"
                  >
                    <RefreshCw className="h-4 w-4" />
                  </button>
                </div>

                <div className="mt-4 grid gap-4 md:grid-cols-2">
                  {plans.map((plan) => {
                    const current = plan.id === user?.plan;
                    const paidPlan = plan.id !== "free";
                    const currentPrice = currentPlan?.price_monthly ?? 0;
                    const isUpgrade = plan.price_monthly > currentPrice;
                    const isDowngrade = plan.price_monthly < currentPrice;
                    const disabled =
                      actionLoading ||
                      (current && user?.plan_status !== "cancelling") ||
                      (plan.id === "free" && user?.plan !== "free");
                    const buttonLabel = current
                      ? user?.plan_status === "cancelling"
                        ? "Cancellation scheduled"
                        : "Current plan"
                      : user?.plan === "free"
                        ? paidPlan
                          ? "Subscribe"
                          : "Current plan"
                        : plan.id === "free"
                          ? "Use cancellation below"
                          : isUpgrade
                            ? "Upgrade"
                            : isDowngrade
                              ? "Downgrade"
                              : "Switch";

                    return (
                      <div
                        key={plan.id}
                        className={`rounded-xl border p-5 transition-colors ${
                          selectedPlanId === plan.id
                            ? "border-codey-green/50 bg-codey-green/5"
                            : "border-codey-border bg-codey-bg"
                        }`}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <h3 className="text-lg font-semibold text-codey-text">
                              {plan.name}
                            </h3>
                            <p className="mt-1 text-sm text-codey-text-dim">
                              {plan.credits.toLocaleString()} credits / month
                            </p>
                          </div>
                          <div className="text-right">
                            <p className="text-2xl font-bold text-codey-text">
                              {formatMoney(plan.price_monthly)}
                            </p>
                            {paidPlan && (
                              <p className="text-xs text-codey-text-muted">monthly</p>
                            )}
                          </div>
                        </div>

                        <div className="mt-4 space-y-1 text-sm text-codey-text-dim">
                          <p>
                            {plan.features.github_repos < 0
                              ? "Unlimited repositories"
                              : `${plan.features.github_repos} connected repo${plan.features.github_repos === 1 ? "" : "s"}`}
                          </p>
                          <p>
                            Max upload size: {plan.features.max_upload_mb.toLocaleString()} MB
                          </p>
                          <p>
                            Autonomous mode: {plan.features.autonomous_mode ? "Included" : "Not included"}
                          </p>
                          <p>
                            Priority support: {plan.features.priority ? "Included" : "Standard"}
                          </p>
                          {plan.features.seats ? <p>{plan.features.seats} seats included</p> : null}
                        </div>

                        <button
                          onClick={() => void handleSelectPlan(plan.id)}
                          disabled={disabled}
                          className={`mt-5 w-full rounded-lg px-4 py-2.5 text-sm font-semibold transition-all ${
                            current
                              ? "border border-codey-border bg-codey-card-hover text-codey-text-dim"
                              : "bg-codey-green text-codey-bg hover:shadow-glow-green"
                          } disabled:cursor-not-allowed disabled:opacity-60`}
                        >
                          {actionLoading && selectedPlanId === plan.id ? (
                            <span className="flex items-center justify-center gap-2">
                              <Loader2 className="h-4 w-4 animate-spin" />
                              Working...
                            </span>
                          ) : (
                            buttonLabel
                          )}
                        </button>
                      </div>
                    );
                  })}
                </div>
              </section>

              {clientSecret && checkoutMode && selectedPlanId && (
                <section className="rounded-xl border border-codey-green/30 bg-codey-card p-6">
                  <div className="mb-5 flex items-start justify-between gap-4">
                    <div>
                      <h2 className="text-sm font-semibold text-codey-text">
                        {checkoutMode === "setup" ? "Add a payment method" : "Confirm your subscription"}
                      </h2>
                      <p className="mt-1 text-sm text-codey-text-dim">
                        {checkoutMode === "setup"
                          ? `Save a card to continue subscribing to ${selectedPlan?.name || selectedPlanId}.`
                          : `Finish activating your ${selectedPlan?.name || selectedPlanId} subscription.`}
                      </p>
                    </div>
                    <button
                      onClick={() => {
                        clearCheckout();
                        setMessage(null);
                        setError(null);
                      }}
                      className="rounded-lg p-2 text-codey-text-dim hover:bg-codey-card-hover"
                      aria-label="Close checkout"
                    >
                      <X className="h-4 w-4" />
                    </button>
                  </div>

                  <StripeProvider clientSecret={clientSecret}>
                    {checkoutMode === "setup" ? (
                      <SetupForm
                        onSuccess={() => void handleSetupSuccess()}
                        onError={(messageText) => setError(messageText)}
                        returnUrl={`${origin}/settings/billing?subscribe=${encodeURIComponent(
                          selectedPlanId
                        )}${referralId ? `&ref=${encodeURIComponent(referralId)}` : ""}&setup=success`}
                      />
                    ) : (
                      <PaymentForm
                        onSuccess={() => void handlePaymentSuccess()}
                        onError={(messageText) => setError(messageText)}
                        submitLabel={`Activate ${selectedPlan?.name || selectedPlanId}`}
                        returnUrl={`${origin}/settings/billing?subscribe=${encodeURIComponent(
                          selectedPlanId
                        )}&subscription=${encodeURIComponent(
                          pendingSubscriptionId || ""
                        )}${referralId ? `&ref=${encodeURIComponent(referralId)}` : ""}&payment=success`}
                      />
                    )}
                  </StripeProvider>
                </section>
              )}
            </div>

            <div className="space-y-6">
              <section className="rounded-xl border border-codey-border bg-codey-card p-6">
                <div className="flex items-center gap-2">
                  <CreditCard className="h-4 w-4 text-codey-text-dim" />
                  <h2 className="text-sm font-semibold text-codey-text">Payment methods</h2>
                </div>

                {paymentMethods.length === 0 ? (
                  <div className="mt-4 rounded-lg border border-codey-border bg-codey-bg px-4 py-6 text-center text-sm text-codey-text-dim">
                    No saved cards yet. Add one from checkout or the main settings page.
                    <div className="mt-3">
                      <Link href="/settings" className="font-medium text-codey-green hover:underline">
                        Open settings
                      </Link>
                    </div>
                  </div>
                ) : (
                  <div className="mt-4 space-y-3">
                    {paymentMethods.map((method) => (
                      <div
                        key={method.id}
                        className="rounded-lg border border-codey-border bg-codey-bg px-4 py-3"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <p className="text-sm text-codey-text capitalize">
                              {method.brand} ending in {method.last4}
                            </p>
                            <p className="mt-1 text-xs text-codey-text-muted">
                              Expires {method.exp_month}/{method.exp_year}
                            </p>
                          </div>
                          <div className="flex items-center gap-2">
                            {method.is_default ? (
                              <span className="rounded-full bg-codey-green/10 px-2 py-0.5 text-[11px] font-medium text-codey-green">
                                Default
                              </span>
                            ) : null}
                            <button
                              onClick={() => void handleRemovePaymentMethod(method.id)}
                              disabled={removingMethodId === method.id}
                              className="rounded-lg border border-codey-border px-2 py-1 text-xs text-codey-text-dim hover:bg-codey-card-hover disabled:opacity-50"
                            >
                              {removingMethodId === method.id ? "Removing..." : "Remove"}
                            </button>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </section>

              <section className="rounded-xl border border-codey-border bg-codey-card p-6">
                <div className="flex items-center gap-2">
                  <Shield className="h-4 w-4 text-codey-text-dim" />
                  <h2 className="text-sm font-semibold text-codey-text">Invoices</h2>
                </div>

                {invoices.length === 0 ? (
                  <div className="mt-4 rounded-lg border border-codey-border bg-codey-bg px-4 py-6 text-center text-sm text-codey-text-dim">
                    No invoices yet.
                  </div>
                ) : (
                  <div className="mt-4 space-y-3">
                    {invoices.map((invoice) => (
                      <div
                        key={invoice.id}
                        className="rounded-lg border border-codey-border bg-codey-bg px-4 py-3"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <p className="text-sm font-medium text-codey-text">
                              {invoice.number || invoice.id}
                            </p>
                            <p className="mt-1 text-xs text-codey-text-muted">
                              {formatInvoiceDate(invoice.period_start)} to {formatInvoiceDate(invoice.period_end)}
                            </p>
                            <p className="mt-1 text-xs capitalize text-codey-text-dim">
                              Status: {invoice.status || "unknown"}
                            </p>
                          </div>
                          <div className="text-right">
                            <p className="text-sm font-semibold text-codey-text">
                              {formatMoney(invoice.amount_paid || invoice.amount_due)}
                            </p>
                            {invoice.hosted_invoice_url ? (
                              <a
                                href={invoice.hosted_invoice_url}
                                target="_blank"
                                rel="noreferrer"
                                className="mt-1 inline-flex items-center gap-1 text-xs text-codey-green hover:underline"
                              >
                                View
                                <ExternalLink className="h-3 w-3" />
                              </a>
                            ) : null}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </section>

              <section className="rounded-xl border border-codey-border bg-codey-card p-6">
                <h2 className="text-sm font-semibold text-codey-text">What changes here</h2>
                <div className="mt-4 space-y-2 text-sm text-codey-text-dim">
                  <p className="flex items-start gap-2">
                    <ArrowRight className="mt-0.5 h-4 w-4 shrink-0 text-codey-green" />
                    Upgrades take effect immediately.
                  </p>
                  <p className="flex items-start gap-2">
                    <ArrowRight className="mt-0.5 h-4 w-4 shrink-0 text-codey-green" />
                    Downgrades are applied using the existing subscription instead of sending you back through signup.
                  </p>
                  <p className="flex items-start gap-2">
                    <ArrowRight className="mt-0.5 h-4 w-4 shrink-0 text-codey-green" />
                    Cancelling returns the account to free access when the current billing period ends.
                  </p>
                </div>
              </section>
            </div>
          </div>
        </div>
      </DashboardLayout>
    </ProtectedRoute>
  );
}

export default function BillingPage() {
  return (
    <Suspense
      fallback={
        <ProtectedRoute>
          <DashboardLayout>
            <div className="flex h-64 items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-codey-green" />
            </div>
          </DashboardLayout>
        </ProtectedRoute>
      }
    >
      <BillingPageContent />
    </Suspense>
  );
}
