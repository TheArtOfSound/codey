"use client";

import { useState, useCallback } from "react";
import { loadStripe, type Stripe, type Appearance } from "@stripe/stripe-js";
import {
  Elements,
  PaymentElement,
  useStripe,
  useElements,
} from "@stripe/react-stripe-js";

// ── Stripe Instance ────────────────────────────────────────────────────────

const stripeKey = process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY;

let stripePromise: Promise<Stripe | null> | null = null;

export function getStripe(): Promise<Stripe | null> {
  if (!stripePromise && stripeKey) {
    stripePromise = loadStripe(stripeKey);
  }
  return stripePromise || Promise.resolve(null);
}

// ── Appearance ─────────────────────────────────────────────────────────────

export const stripeAppearance: Appearance = {
  theme: "night",
  variables: {
    colorPrimary: "#00ff88",
    colorBackground: "#14141f",
    colorText: "#e0e0e8",
    colorTextSecondary: "#8888a0",
    colorTextPlaceholder: "#55556a",
    colorDanger: "#ff4444",
    colorWarning: "#ffcc00",
    fontFamily: "Inter, system-ui, sans-serif",
    fontSizeBase: "14px",
    spacingUnit: "4px",
    borderRadius: "8px",
    colorIconCardCvc: "#8888a0",
    colorIconCardExpiry: "#8888a0",
    colorIconCardNumber: "#8888a0",
  },
  rules: {
    ".Input": {
      backgroundColor: "#0a0a0f",
      border: "1px solid #2a2a3e",
      boxShadow: "none",
      color: "#e0e0e8",
      fontSize: "14px",
      padding: "12px",
    },
    ".Input:focus": {
      border: "1px solid #00ff88",
      boxShadow: "0 0 8px rgba(0, 255, 136, 0.15)",
    },
    ".Input:hover": {
      border: "1px solid #3a3a4e",
    },
    ".Input--invalid": {
      border: "1px solid #ff4444",
      boxShadow: "0 0 8px rgba(255, 68, 68, 0.15)",
    },
    ".Label": {
      color: "#8888a0",
      fontSize: "12px",
      fontWeight: "500",
      textTransform: "uppercase",
      letterSpacing: "0.05em",
    },
    ".Tab": {
      backgroundColor: "#14141f",
      border: "1px solid #2a2a3e",
      color: "#8888a0",
    },
    ".Tab:hover": {
      backgroundColor: "#1a1a2e",
      color: "#e0e0e8",
    },
    ".Tab--selected": {
      backgroundColor: "#0a0a0f",
      border: "1px solid #00ff88",
      color: "#00ff88",
    },
    ".Error": {
      color: "#ff4444",
      fontSize: "12px",
    },
  },
};

// ── Stripe Elements Wrapper ────────────────────────────────────────────────

interface StripeProviderProps {
  clientSecret: string;
  children: React.ReactNode;
}

export function StripeProvider({ clientSecret, children }: StripeProviderProps) {
  return (
    <Elements
      stripe={getStripe()}
      options={{
        clientSecret,
        appearance: stripeAppearance,
      }}
    >
      {children}
    </Elements>
  );
}

// ── Payment Form ───────────────────────────────────────────────────────────

interface PaymentFormProps {
  onSuccess: (paymentIntentId: string) => void;
  onError?: (error: string) => void;
  submitLabel?: string;
  loading?: boolean;
}

export function PaymentForm({
  onSuccess,
  onError,
  submitLabel = "Pay",
  loading: externalLoading,
}: PaymentFormProps) {
  const stripe = useStripe();
  const elements = useElements();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();

      if (!stripe || !elements) {
        return;
      }

      setSubmitting(true);
      setError(null);

      const result = await stripe.confirmPayment({
        elements,
        confirmParams: {
          return_url: `${window.location.origin}/credits?payment=success`,
        },
        redirect: "if_required",
      });

      if (result.error) {
        const msg = result.error.message || "Payment failed. Please try again.";
        setError(msg);
        onError?.(msg);
        setSubmitting(false);
      } else if (result.paymentIntent) {
        onSuccess(result.paymentIntent.id);
        setSubmitting(false);
      }
    },
    [stripe, elements, onSuccess, onError]
  );

  const isLoading = submitting || externalLoading;

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <PaymentElement
        options={{
          layout: "tabs",
        }}
      />

      {error && (
        <div className="rounded-lg border border-codey-red/30 bg-codey-red-glow px-4 py-3 text-sm text-codey-red">
          {error}
        </div>
      )}

      <button
        type="submit"
        disabled={!stripe || isLoading}
        className="w-full rounded-lg bg-codey-green px-6 py-3 text-sm font-semibold text-codey-bg transition-all hover:shadow-glow-green disabled:cursor-not-allowed disabled:opacity-50"
      >
        {isLoading ? (
          <span className="flex items-center justify-center gap-2">
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-codey-bg border-t-transparent" />
            Processing...
          </span>
        ) : (
          submitLabel
        )}
      </button>
    </form>
  );
}

// ── Setup Form (for saving payment methods) ────────────────────────────────

interface SetupFormProps {
  onSuccess: (setupIntentId: string) => void;
  onError?: (error: string) => void;
}

export function SetupForm({ onSuccess, onError }: SetupFormProps) {
  const stripe = useStripe();
  const elements = useElements();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();

      if (!stripe || !elements) {
        return;
      }

      setSubmitting(true);
      setError(null);

      const result = await stripe.confirmSetup({
        elements,
        confirmParams: {
          return_url: `${window.location.origin}/settings?setup=success`,
        },
        redirect: "if_required",
      });

      if (result.error) {
        const msg = result.error.message || "Setup failed. Please try again.";
        setError(msg);
        onError?.(msg);
        setSubmitting(false);
      } else if (result.setupIntent) {
        onSuccess(result.setupIntent.id);
        setSubmitting(false);
      }
    },
    [stripe, elements, onSuccess, onError]
  );

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <PaymentElement options={{ layout: "tabs" }} />

      {error && (
        <div className="rounded-lg border border-codey-red/30 bg-codey-red-glow px-4 py-3 text-sm text-codey-red">
          {error}
        </div>
      )}

      <button
        type="submit"
        disabled={!stripe || submitting}
        className="w-full rounded-lg bg-codey-green px-6 py-3 text-sm font-semibold text-codey-bg transition-all hover:shadow-glow-green disabled:cursor-not-allowed disabled:opacity-50"
      >
        {submitting ? (
          <span className="flex items-center justify-center gap-2">
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-codey-bg border-t-transparent" />
            Saving...
          </span>
        ) : (
          "Save Payment Method"
        )}
      </button>
    </form>
  );
}
