import { Shield } from "lucide-react";

export default function PrivacyPage() {
  return (
    <div className="min-h-screen bg-codey-bg">
      <div className="mx-auto max-w-3xl px-4 py-16">
        <div className="flex items-center gap-3">
          <Shield className="h-6 w-6 text-codey-green" />
          <h1 className="text-2xl font-bold text-codey-text">Privacy Policy</h1>
        </div>
        <p className="mt-2 text-sm text-codey-text-muted">
          Last updated: March 27, 2026
        </p>

        <div className="mt-8 space-y-8 text-sm leading-relaxed text-codey-text-dim">
          <section>
            <h2 className="text-lg font-semibold text-codey-text">What Data We Collect</h2>
            <p className="mt-2">
              When you use Codey, we collect the following information:
            </p>
            <ul className="mt-2 list-inside list-disc space-y-1">
              <li>Email address and account credentials (hashed)</li>
              <li>Prompts you submit for code generation</li>
              <li>Generated code and project files</li>
              <li>GitHub username and OAuth tokens (if connected)</li>
              <li>Usage data: session history, credit usage, feature interactions</li>
              <li>Memory preferences you set or that Codey learns from your sessions</li>
              <li>Payment information (processed by Stripe; we never store full card numbers)</li>
            </ul>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-codey-text">How We Store Your Code</h2>
            <p className="mt-2">
              Code stored in your vault is retained for a maximum of 30 days after your last
              interaction with it. Active project code (used within the past 30 days) is stored
              indefinitely while your account is active. Code is stored encrypted at rest using
              AES-256 and transmitted over TLS 1.3.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-codey-text">Prompt and Session History</h2>
            <p className="mt-2">
              Your prompts are stored to provide session history and to power features like
              memory-based personalization. We do not use your prompts or generated code to
              train AI models. Prompt history can be viewed in your dashboard and deleted
              from the Settings page.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-codey-text">We Do Not Sell Your Data</h2>
            <p className="mt-2">
              Qira LLC does not sell, rent, or share your personal data with third parties for
              marketing purposes. We do not share your code, prompts, or usage data with anyone
              outside of Qira LLC, except as required by law or as described in this policy.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-codey-text">Payment Processing</h2>
            <p className="mt-2">
              All payment processing is handled by Stripe. We store only the last four digits of
              your card, the card brand, and expiration date for display purposes. We never have
              access to your full card number, CVV, or bank account details. See{" "}
              <a
                href="https://stripe.com/privacy"
                target="_blank"
                rel="noopener noreferrer"
                className="text-codey-green hover:underline"
              >
                Stripe&apos;s Privacy Policy
              </a>{" "}
              for more information on how they handle your payment data.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-codey-text">GitHub Token Security</h2>
            <p className="mt-2">
              If you connect your GitHub account, your OAuth token is encrypted using AES-256
              before storage. Tokens are scoped to the minimum permissions required (repo access).
              You can disconnect GitHub and revoke our access at any time from the Settings page,
              which immediately deletes the stored token.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-codey-text">Your Rights (GDPR and CCPA)</h2>
            <p className="mt-2">
              Regardless of where you are located, you have the following rights:
            </p>
            <ul className="mt-2 list-inside list-disc space-y-1">
              <li>
                <strong className="text-codey-text">Right to access:</strong> Request a copy of
                all personal data we hold about you.
              </li>
              <li>
                <strong className="text-codey-text">Right to correction:</strong> Update or
                correct inaccurate data.
              </li>
              <li>
                <strong className="text-codey-text">Right to deletion:</strong> Request deletion
                of your account and all associated data. We will process deletion requests within
                30 days.
              </li>
              <li>
                <strong className="text-codey-text">Right to export:</strong> Export your code
                and data from the vault at any time.
              </li>
              <li>
                <strong className="text-codey-text">Right to object:</strong> Opt out of
                non-essential data processing.
              </li>
            </ul>
            <p className="mt-2">
              To exercise any of these rights, email{" "}
              <a href="mailto:privacy@codey.ai" className="text-codey-green hover:underline">
                privacy@codey.ai
              </a>{" "}
              with your request. We will respond within 30 days.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-codey-text">Cookies and Analytics</h2>
            <p className="mt-2">
              We use essential cookies for authentication and session management. We do not use
              third-party advertising cookies. We use minimal, privacy-respecting analytics to
              understand feature usage. You can disable non-essential cookies in your browser
              settings.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-codey-text">Data Retention</h2>
            <p className="mt-2">
              Account data is retained while your account is active. After account deletion, all
              personal data, code, prompts, and session history are permanently deleted within
              30 days. Anonymized aggregate usage statistics may be retained indefinitely.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-codey-text">Changes to This Policy</h2>
            <p className="mt-2">
              We may update this policy from time to time. We will notify registered users of
              material changes by email. Continued use of Codey after changes constitutes
              acceptance.
            </p>
          </section>

          <section className="rounded-lg border border-codey-border bg-codey-card p-4">
            <p className="text-xs text-codey-text-muted">
              Qira LLC &middot; Phoenix, Arizona &middot; Privacy questions?{" "}
              <a href="mailto:privacy@codey.ai" className="text-codey-green hover:underline">
                privacy@codey.ai
              </a>
            </p>
          </section>
        </div>
      </div>
    </div>
  );
}
