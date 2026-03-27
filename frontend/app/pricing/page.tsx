"use client";

import { useState } from "react";
import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { Check, ChevronDown, Zap } from "lucide-react";

// ── Plan Data ────────────────────────────────────────────────────────────────

interface PlanCard {
  id: string;
  name: string;
  monthlyPrice: number;
  credits: number;
  features: string[];
  cta: string;
  highlighted?: boolean;
}

const plans: PlanCard[] = [
  {
    id: "free",
    name: "Free",
    monthlyPrice: 0,
    credits: 50,
    features: [
      "50 credits / month",
      "Single repo analysis",
      "Basic NFET metrics",
      "Community support",
    ],
    cta: "Get started",
  },
  {
    id: "starter",
    name: "Starter",
    monthlyPrice: 19,
    credits: 500,
    features: [
      "500 credits / month",
      "5 connected repos",
      "Full NFET dashboard",
      "Collapse prediction",
      "Email support",
    ],
    cta: "Subscribe",
  },
  {
    id: "pro",
    name: "Pro",
    monthlyPrice: 49,
    credits: 2000,
    features: [
      "2,000 credits / month",
      "Unlimited repos",
      "Autonomous mode",
      "Cascade simulation",
      "Priority support",
      "API access",
    ],
    cta: "Subscribe",
    highlighted: true,
  },
  {
    id: "team",
    name: "Team",
    monthlyPrice: 149,
    credits: 10000,
    features: [
      "10,000 credits / month",
      "Unlimited repos",
      "Everything in Pro",
      "Team dashboard",
      "SSO & audit logs",
      "Dedicated support",
      "Custom integrations",
    ],
    cta: "Subscribe",
  },
];

// ── Credit Top-ups ───────────────────────────────────────────────────────────

const topups = [
  { credits: 100, price: 5 },
  { credits: 500, price: 20 },
  { credits: 2000, price: 60 },
  { credits: 5000, price: 125 },
];

// ── FAQ ──────────────────────────────────────────────────────────────────────

const faqItems = [
  {
    q: "What is a credit?",
    a: "One credit roughly equals one AI-assisted action — a code generation, analysis step, or NFET computation. Simple completions cost 1 credit; complex autonomous tasks may cost 5-20 credits.",
  },
  {
    q: "Can I switch plans anytime?",
    a: "Yes. Upgrades take effect immediately with prorated billing. Downgrades take effect at the end of your current billing period.",
  },
  {
    q: "Do unused credits roll over?",
    a: "Plan credits reset each month. Purchased top-up credits never expire.",
  },
  {
    q: "What happens when I run out of credits?",
    a: "You can purchase top-up credits instantly or upgrade your plan. Codey will pause generation and let you choose before any credits are spent.",
  },
  {
    q: "Is there an annual discount?",
    a: "Yes — annual billing gives you 2 months free on any paid plan. Toggle the switch above to see annual pricing.",
  },
];

// ── Page ─────────────────────────────────────────────────────────────────────

export default function PricingPage() {
  const [annual, setAnnual] = useState(false);
  const { user } = useAuth();

  function planHref(plan: PlanCard) {
    if (plan.id === "free") return "/auth/signup";
    if (user) return `/settings/billing?subscribe=${plan.id}`;
    return `/auth/signup?plan=${plan.id}`;
  }

  function displayPrice(monthly: number) {
    if (monthly === 0) return "$0";
    if (annual) {
      const mo = Math.round(monthly * 10) / 12;
      // Show whole dollars for clean numbers
      return `$${Number.isInteger(mo) ? mo : mo.toFixed(0)}`;
    }
    return `$${monthly}`;
  }

  return (
    <div className="min-h-screen bg-codey-bg text-codey-text">
      {/* Nav */}
      <nav className="flex items-center justify-between px-6 py-5 md:px-12">
        <Link href="/" className="text-xl font-bold tracking-tight">
          <span className="text-codey-green">C</span>ODEY
        </Link>
        <div className="flex items-center gap-4">
          {user ? (
            <Link href="/dashboard" className="btn-ghost text-sm">
              Dashboard
            </Link>
          ) : (
            <>
              <Link href="/auth/login" className="btn-ghost text-sm">
                Log in
              </Link>
              <Link href="/auth/signup" className="btn-primary text-sm">
                Sign up free
              </Link>
            </>
          )}
        </div>
      </nav>

      {/* Header */}
      <section className="mx-auto max-w-5xl px-6 pb-12 pt-12 text-center md:pt-20">
        <h1 className="text-4xl font-bold md:text-5xl">
          Simple, transparent pricing
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-lg text-codey-text-dim">
          Start free. Pay only when you need more power.
        </p>

        {/* Toggle */}
        <div className="mt-8 flex items-center justify-center gap-3">
          <span
            className={`text-sm ${
              !annual ? "text-codey-text" : "text-codey-text-dim"
            }`}
          >
            Monthly
          </span>
          <button
            onClick={() => setAnnual(!annual)}
            className={`relative h-6 w-11 rounded-full transition-colors ${
              annual ? "bg-codey-green" : "bg-codey-border"
            }`}
          >
            <span
              className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-transform ${
                annual ? "translate-x-[22px]" : "translate-x-0.5"
              }`}
            />
          </button>
          <span
            className={`text-sm ${
              annual ? "text-codey-text" : "text-codey-text-dim"
            }`}
          >
            Annual
          </span>
          {annual && (
            <span className="badge-green ml-1 text-xs">2 months free</span>
          )}
        </div>
      </section>

      {/* Plan Cards */}
      <section className="mx-auto max-w-6xl px-6 pb-20">
        <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-4">
          {plans.map((plan) => (
            <div
              key={plan.id}
              className={`relative flex flex-col rounded-xl border p-6 transition-all ${
                plan.highlighted
                  ? "border-codey-green/50 bg-codey-card shadow-glow-green"
                  : "border-codey-border bg-codey-card hover:border-codey-border-light"
              }`}
            >
              {plan.highlighted && (
                <div className="absolute -top-3 left-1/2 -translate-x-1/2">
                  <span className="badge-green px-3 py-1 text-xs font-semibold">
                    Most popular
                  </span>
                </div>
              )}

              <h3 className="text-lg font-semibold">{plan.name}</h3>

              <div className="mt-3 flex items-baseline gap-1">
                <span className="text-4xl font-bold">
                  {displayPrice(plan.monthlyPrice)}
                </span>
                {plan.monthlyPrice > 0 && (
                  <span className="text-sm text-codey-text-dim">/ mo</span>
                )}
              </div>

              {annual && plan.monthlyPrice > 0 && (
                <p className="mt-1 text-xs text-codey-text-muted">
                  ${plan.monthlyPrice * 10}/year (billed annually)
                </p>
              )}

              <p className="mt-2 text-sm text-codey-text-dim">
                {plan.credits.toLocaleString()} credits / month
              </p>

              <ul className="mt-6 flex-1 space-y-3">
                {plan.features.map((feat) => (
                  <li
                    key={feat}
                    className="flex items-start gap-2 text-sm text-codey-text-dim"
                  >
                    <Check className="mt-0.5 h-4 w-4 shrink-0 text-codey-green" />
                    {feat}
                  </li>
                ))}
              </ul>

              <Link
                href={planHref(plan)}
                className={`mt-6 w-full text-center ${
                  plan.highlighted ? "btn-primary" : "btn-secondary"
                } py-2.5`}
              >
                {plan.cta}
              </Link>
            </div>
          ))}
        </div>
      </section>

      {/* Credit Top-ups */}
      <section className="mx-auto max-w-4xl px-6 pb-20">
        <h2 className="mb-2 text-center text-2xl font-bold">
          Need more credits?
        </h2>
        <p className="mb-8 text-center text-sm text-codey-text-dim">
          Top-up credits never expire. Buy what you need.
        </p>
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          {topups.map((t) => (
            <div
              key={t.credits}
              className="card-hover flex flex-col items-center gap-2 text-center"
            >
              <Zap className="h-5 w-5 text-codey-green" />
              <span className="text-2xl font-bold">
                {t.credits.toLocaleString()}
              </span>
              <span className="text-xs text-codey-text-dim">credits</span>
              <span className="text-lg font-semibold text-codey-green">
                ${t.price}
              </span>
              <span className="text-xs text-codey-text-muted">
                ${((t.price / t.credits) * 100).toFixed(1)}&cent; / credit
              </span>
            </div>
          ))}
        </div>
      </section>

      {/* FAQ */}
      <section className="mx-auto max-w-2xl px-6 pb-20">
        <h2 className="mb-8 text-center text-2xl font-bold">
          Frequently asked questions
        </h2>
        <FAQAccordion items={faqItems} />
      </section>

      {/* Footer */}
      <footer className="border-t border-codey-border bg-codey-card/30 px-6 py-10">
        <div className="mx-auto flex max-w-5xl flex-col items-center gap-4 md:flex-row md:justify-between">
          <span className="text-lg font-bold tracking-tight">
            <span className="text-codey-green">C</span>ODEY
          </span>
          <p className="text-xs text-codey-text-muted">
            Powered by NFET — Qira LLC &copy; {new Date().getFullYear()}
          </p>
        </div>
      </footer>
    </div>
  );
}

// ── FAQ Accordion ────────────────────────────────────────────────────────────

function FAQAccordion({
  items,
}: {
  items: { q: string; a: string }[];
}) {
  const [open, setOpen] = useState<number | null>(null);

  return (
    <div className="space-y-3">
      {items.map((item, i) => (
        <div
          key={i}
          className="rounded-lg border border-codey-border bg-codey-card"
        >
          <button
            onClick={() => setOpen(open === i ? null : i)}
            className="flex w-full items-center justify-between px-5 py-4 text-left text-sm font-medium text-codey-text"
          >
            {item.q}
            <ChevronDown
              className={`h-4 w-4 shrink-0 text-codey-text-dim transition-transform ${
                open === i ? "rotate-180" : ""
              }`}
            />
          </button>
          {open === i && (
            <div className="px-5 pb-4 text-sm leading-relaxed text-codey-text-dim animate-fade-in">
              {item.a}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
