"use client";

import { useEffect, useState, useCallback } from "react";
import { useAuth } from "@/lib/auth";
import { api, type PaymentMethod } from "@/lib/api";
import { StripeProvider, SetupForm } from "@/lib/stripe";
import DashboardLayout from "@/components/layout/DashboardLayout";
import { ProtectedRoute } from "@/lib/auth";
import {
  User,
  Mail,
  Lock,
  GitBranch,
  Bell,
  Key,
  CreditCard,
  Trash2,
  Check,
  X,
  Eye,
  EyeOff,
  Copy,
  Loader2,
  AlertTriangle,
  ExternalLink,
  Plus,
  Shield,
} from "lucide-react";

// ── Toggle Switch ─────────────────────────────────────────────────────────────

function Toggle({
  enabled,
  onChange,
}: {
  enabled: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      onClick={() => onChange(!enabled)}
      className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors ${
        enabled ? "bg-codey-green" : "bg-codey-border"
      }`}
    >
      <span
        className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${
          enabled ? "translate-x-5" : "translate-x-0"
        }`}
      />
    </button>
  );
}

// ── Section Card ──────────────────────────────────────────────────────────────

function Section({
  title,
  description,
  icon: Icon,
  children,
  danger,
}: {
  title: string;
  description?: string;
  icon: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
  danger?: boolean;
}) {
  return (
    <div
      className={`rounded-xl border bg-codey-card ${
        danger ? "border-codey-red/30" : "border-codey-border"
      }`}
    >
      <div className="border-b border-codey-border/50 px-5 py-4">
        <div className="flex items-center gap-2">
          <Icon
            className={`h-4 w-4 ${danger ? "text-codey-red" : "text-codey-text-dim"}`}
          />
          <h2
            className={`text-sm font-semibold ${
              danger ? "text-codey-red" : "text-codey-text"
            }`}
          >
            {title}
          </h2>
        </div>
        {description && (
          <p className="mt-1 text-xs text-codey-text-muted">{description}</p>
        )}
      </div>
      <div className="px-5 py-5">{children}</div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const { user, refreshUser } = useAuth();

  // Profile
  const [name, setName] = useState("");
  const [profileSaving, setProfileSaving] = useState(false);
  const [profileSaved, setProfileSaved] = useState(false);

  // Password
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPasswords, setShowPasswords] = useState(false);
  const [passwordSaving, setPasswordSaving] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSaved, setPasswordSaved] = useState(false);

  // GitHub
  const [githubConnecting, setGithubConnecting] = useState(false);

  // Notifications
  const [emailSessionComplete, setEmailSessionComplete] = useState(true);
  const [emailAutonomous, setEmailAutonomous] = useState(true);
  const [emailBilling, setEmailBilling] = useState(true);
  const [emailNewsletter, setEmailNewsletter] = useState(false);

  // API keys
  const [apiKeys, setApiKeys] = useState<
    Array<{ id: string; key: string; created_at: string; last_used_at: string | null }>
  >([]);
  const [newKeyName, setNewKeyName] = useState("");
  const [generatedKey, setGeneratedKey] = useState<string | null>(null);
  const [keyCopied, setKeyCopied] = useState(false);

  // Billing
  const [paymentMethods, setPaymentMethods] = useState<PaymentMethod[]>([]);
  const [showAddCard, setShowAddCard] = useState(false);
  const [setupSecret, setSetupSecret] = useState<string | null>(null);
  const [setupLoading, setSetupLoading] = useState(false);

  // Delete
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [deleteInput, setDeleteInput] = useState("");
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (user) {
      setName(user.email.split("@")[0]);
    }
  }, [user]);

  useEffect(() => {
    api.getPaymentMethods().then(setPaymentMethods).catch(() => {});
  }, []);

  // ── Handlers ────────────────────────────────────────────────────────────────

  async function handleSaveProfile() {
    setProfileSaving(true);
    try {
      await api.updateProfile({});
      setProfileSaved(true);
      setTimeout(() => setProfileSaved(false), 2000);
      await refreshUser();
    } catch {}
    setProfileSaving(false);
  }

  async function handleChangePassword() {
    if (newPassword !== confirmPassword) {
      setPasswordError("Passwords do not match");
      return;
    }
    if (newPassword.length < 8) {
      setPasswordError("Password must be at least 8 characters");
      return;
    }
    setPasswordSaving(true);
    setPasswordError(null);
    try {
      // Call password change endpoint (not in current API, placeholder)
      await new Promise((r) => setTimeout(r, 1000));
      setPasswordSaved(true);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setTimeout(() => setPasswordSaved(false), 2000);
    } catch {
      setPasswordError("Failed to change password");
    }
    setPasswordSaving(false);
  }

  function handleConnectGitHub() {
    setGithubConnecting(true);
    const clientId = process.env.NEXT_PUBLIC_GITHUB_CLIENT_ID || "";
    const redirect = `${window.location.origin}/auth/github/callback`;
    window.location.href = `https://github.com/login/oauth/authorize?client_id=${clientId}&redirect_uri=${encodeURIComponent(redirect)}&scope=repo`;
  }

  function handleDisconnectGitHub() {
    // Would call an API endpoint to disconnect
    // For now just refresh user
    refreshUser();
  }

  async function handleGenerateApiKey() {
    if (!newKeyName.trim()) return;
    // Mock key generation
    const mockKey = `codey_${Array.from({ length: 32 }, () => "abcdef0123456789"[Math.floor(Math.random() * 16)]).join("")}`;
    setGeneratedKey(mockKey);
    setApiKeys((prev) => [
      ...prev,
      {
        id: Math.random().toString(36).slice(2),
        key: `${mockKey.slice(0, 12)}...`,
        created_at: new Date().toISOString(),
        last_used_at: null,
      },
    ]);
    setNewKeyName("");
  }

  async function handleCopyKey() {
    if (!generatedKey) return;
    await navigator.clipboard.writeText(generatedKey);
    setKeyCopied(true);
    setTimeout(() => setKeyCopied(false), 2000);
  }

  function handleRevokeKey(id: string) {
    setApiKeys((prev) => prev.filter((k) => k.id !== id));
  }

  async function handleAddCard() {
    setSetupLoading(true);
    try {
      const { client_secret } = await api.getSetupIntent();
      setSetupSecret(client_secret);
      setShowAddCard(true);
    } catch {}
    setSetupLoading(false);
  }

  async function handleSetupSuccess(setupIntentId: string) {
    setShowAddCard(false);
    setSetupSecret(null);
    // Refresh payment methods
    const methods = await api.getPaymentMethods();
    setPaymentMethods(methods);
  }

  async function handleDeleteAccount() {
    if (deleteInput !== "DELETE") return;
    setDeleting(true);
    try {
      // Would call api.deleteAccount()
      await new Promise((r) => setTimeout(r, 1000));
      window.location.href = "/";
    } catch {}
    setDeleting(false);
  }

  const isPro = user?.plan === "pro" || user?.plan === "team";

  return (
    <ProtectedRoute>
      <DashboardLayout>
        <div className="mx-auto max-w-3xl space-y-6">
          <div>
            <h1 className="text-2xl font-bold text-codey-text">Settings</h1>
            <p className="mt-1 text-sm text-codey-text-dim">
              Manage your account, connections, and preferences.
            </p>
          </div>

          {/* ── Profile ────────────────────────────────────────────────── */}
          <Section title="Profile" icon={User}>
            <div className="space-y-4">
              {/* Avatar */}
              <div className="flex items-center gap-4">
                <div className="flex h-16 w-16 items-center justify-center rounded-full bg-codey-green/20 text-2xl font-bold text-codey-green">
                  {user?.email.charAt(0).toUpperCase()}
                </div>
                <div>
                  <p className="text-sm font-medium text-codey-text">
                    {user?.email.split("@")[0]}
                  </p>
                  <p className="text-xs capitalize text-codey-text-dim">
                    {user?.plan} plan
                  </p>
                </div>
              </div>

              {/* Name */}
              <div>
                <label className="text-xs font-medium text-codey-text-dim">
                  Display Name
                </label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-codey-border bg-codey-bg px-4 py-2.5 text-sm text-codey-text focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
                />
              </div>

              {/* Email (read-only) */}
              <div>
                <label className="text-xs font-medium text-codey-text-dim">
                  Email
                </label>
                <div className="mt-1 flex items-center gap-2 rounded-lg border border-codey-border bg-codey-bg/50 px-4 py-2.5">
                  <Mail className="h-4 w-4 text-codey-text-muted" />
                  <span className="text-sm text-codey-text-dim">
                    {user?.email}
                  </span>
                </div>
              </div>

              <button
                onClick={handleSaveProfile}
                disabled={profileSaving}
                className="flex items-center gap-2 rounded-lg bg-codey-green px-4 py-2 text-sm font-semibold text-codey-bg transition-all hover:shadow-glow-green disabled:opacity-50"
              >
                {profileSaved ? (
                  <>
                    <Check className="h-4 w-4" />
                    Saved
                  </>
                ) : profileSaving ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  "Save changes"
                )}
              </button>
            </div>
          </Section>

          {/* ── Password ───────────────────────────────────────────────── */}
          <Section title="Change Password" icon={Lock}>
            <div className="space-y-4">
              <div>
                <label className="text-xs font-medium text-codey-text-dim">
                  Current Password
                </label>
                <div className="relative mt-1">
                  <input
                    type={showPasswords ? "text" : "password"}
                    value={currentPassword}
                    onChange={(e) => setCurrentPassword(e.target.value)}
                    className="w-full rounded-lg border border-codey-border bg-codey-bg px-4 py-2.5 pr-10 text-sm text-codey-text focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPasswords(!showPasswords)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-codey-text-muted hover:text-codey-text"
                  >
                    {showPasswords ? (
                      <EyeOff className="h-4 w-4" />
                    ) : (
                      <Eye className="h-4 w-4" />
                    )}
                  </button>
                </div>
              </div>

              <div>
                <label className="text-xs font-medium text-codey-text-dim">
                  New Password
                </label>
                <input
                  type={showPasswords ? "text" : "password"}
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-codey-border bg-codey-bg px-4 py-2.5 text-sm text-codey-text focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
                />
              </div>

              <div>
                <label className="text-xs font-medium text-codey-text-dim">
                  Confirm New Password
                </label>
                <input
                  type={showPasswords ? "text" : "password"}
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-codey-border bg-codey-bg px-4 py-2.5 text-sm text-codey-text focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
                />
              </div>

              {passwordError && (
                <p className="text-xs text-codey-red">{passwordError}</p>
              )}

              <button
                onClick={handleChangePassword}
                disabled={
                  passwordSaving ||
                  !currentPassword ||
                  !newPassword ||
                  !confirmPassword
                }
                className="flex items-center gap-2 rounded-lg bg-codey-green px-4 py-2 text-sm font-semibold text-codey-bg transition-all hover:shadow-glow-green disabled:opacity-50"
              >
                {passwordSaved ? (
                  <>
                    <Check className="h-4 w-4" />
                    Updated
                  </>
                ) : passwordSaving ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  "Change password"
                )}
              </button>
            </div>
          </Section>

          {/* ── GitHub Connection ───────────────────────────────────────── */}
          <Section
            title="GitHub Connection"
            description="Connect your GitHub account to link repos and enable autonomous mode."
            icon={GitBranch}
          >
            {user?.github_username ? (
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-full bg-codey-card-hover">
                    <GitBranch className="h-5 w-5 text-codey-green" />
                  </div>
                  <div>
                    <p className="text-sm font-medium text-codey-text">
                      {user.github_username}
                    </p>
                    <p className="text-xs text-codey-green">Connected</p>
                  </div>
                </div>
                <button
                  onClick={handleDisconnectGitHub}
                  className="rounded-lg border border-codey-red/30 px-3 py-1.5 text-xs text-codey-red hover:bg-codey-red-glow"
                >
                  Disconnect
                </button>
              </div>
            ) : (
              <button
                onClick={handleConnectGitHub}
                disabled={githubConnecting}
                className="flex items-center gap-2 rounded-lg bg-codey-card-hover px-4 py-2.5 text-sm font-medium text-codey-text transition-colors hover:bg-codey-border"
              >
                <GitBranch className="h-4 w-4" />
                {githubConnecting ? "Redirecting..." : "Connect GitHub"}
                <ExternalLink className="h-3 w-3 text-codey-text-muted" />
              </button>
            )}
          </Section>

          {/* ── Notifications ──────────────────────────────────────────── */}
          <Section title="Notifications" icon={Bell}>
            <div className="space-y-4">
              {[
                {
                  label: "Session complete",
                  desc: "Email when a prompt session finishes",
                  value: emailSessionComplete,
                  onChange: setEmailSessionComplete,
                },
                {
                  label: "Autonomous actions",
                  desc: "Email when autonomous mode takes action on your repos",
                  value: emailAutonomous,
                  onChange: setEmailAutonomous,
                },
                {
                  label: "Billing alerts",
                  desc: "Email for payment confirmations and low credit warnings",
                  value: emailBilling,
                  onChange: setEmailBilling,
                },
                {
                  label: "Product updates",
                  desc: "Occasional emails about new features and improvements",
                  value: emailNewsletter,
                  onChange: setEmailNewsletter,
                },
              ].map((item) => (
                <div
                  key={item.label}
                  className="flex items-center justify-between"
                >
                  <div>
                    <p className="text-sm text-codey-text">{item.label}</p>
                    <p className="text-xs text-codey-text-muted">{item.desc}</p>
                  </div>
                  <Toggle enabled={item.value} onChange={item.onChange} />
                </div>
              ))}
            </div>
          </Section>

          {/* ── API Keys (Pro+) ────────────────────────────────────────── */}
          <Section
            title="API Keys"
            description={
              isPro
                ? "Generate API keys to use Codey programmatically."
                : "Available on Pro and Team plans."
            }
            icon={Key}
          >
            {isPro ? (
              <div className="space-y-4">
                {/* Generate new key */}
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={newKeyName}
                    onChange={(e) => setNewKeyName(e.target.value)}
                    placeholder="Key name (e.g. CI/CD pipeline)"
                    className="flex-1 rounded-lg border border-codey-border bg-codey-bg px-4 py-2.5 text-sm text-codey-text placeholder:text-codey-text-muted focus:border-codey-green focus:outline-none focus:ring-1 focus:ring-codey-green/30"
                  />
                  <button
                    onClick={handleGenerateApiKey}
                    disabled={!newKeyName.trim()}
                    className="flex items-center gap-1.5 rounded-lg bg-codey-green px-4 py-2 text-sm font-semibold text-codey-bg hover:shadow-glow-green disabled:opacity-50"
                  >
                    <Plus className="h-4 w-4" />
                    Generate
                  </button>
                </div>

                {/* Show newly generated key */}
                {generatedKey && (
                  <div className="rounded-lg border border-codey-yellow/30 bg-codey-yellow-glow p-4">
                    <p className="text-xs font-medium text-codey-yellow">
                      Copy this key now — you will not see it again.
                    </p>
                    <div className="mt-2 flex items-center gap-2">
                      <code className="flex-1 rounded bg-codey-bg px-3 py-2 font-mono text-xs text-codey-text">
                        {generatedKey}
                      </code>
                      <button
                        onClick={handleCopyKey}
                        className="rounded-lg border border-codey-border p-2 text-codey-text-dim hover:bg-codey-card-hover"
                      >
                        {keyCopied ? (
                          <Check className="h-4 w-4 text-codey-green" />
                        ) : (
                          <Copy className="h-4 w-4" />
                        )}
                      </button>
                    </div>
                  </div>
                )}

                {/* Existing keys */}
                {apiKeys.length > 0 && (
                  <div className="space-y-2">
                    {apiKeys.map((key) => (
                      <div
                        key={key.id}
                        className="flex items-center justify-between rounded-lg border border-codey-border bg-codey-bg px-4 py-3"
                      >
                        <div>
                          <code className="font-mono text-xs text-codey-text-dim">
                            {key.key}
                          </code>
                          <p className="mt-0.5 text-xs text-codey-text-muted">
                            Created{" "}
                            {new Date(key.created_at).toLocaleDateString()}
                            {key.last_used_at &&
                              ` · Last used ${new Date(key.last_used_at).toLocaleDateString()}`}
                          </p>
                        </div>
                        <button
                          onClick={() => handleRevokeKey(key.id)}
                          className="rounded-lg p-1.5 text-codey-text-muted hover:bg-codey-card-hover hover:text-codey-red"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <div className="rounded-lg bg-codey-bg px-4 py-6 text-center">
                <Shield className="mx-auto h-8 w-8 text-codey-text-muted" />
                <p className="mt-2 text-sm text-codey-text-dim">
                  Upgrade to Pro to access API keys.
                </p>
                <a
                  href="/pricing"
                  className="mt-3 inline-block text-sm font-medium text-codey-green hover:underline"
                >
                  View plans
                </a>
              </div>
            )}
          </Section>

          {/* ── Billing ────────────────────────────────────────────────── */}
          <Section
            title="Billing"
            description="Manage your payment methods."
            icon={CreditCard}
          >
            <div className="space-y-4">
              {/* Existing cards */}
              {paymentMethods.length > 0 && (
                <div className="space-y-2">
                  {paymentMethods.map((pm) => (
                    <div
                      key={pm.id}
                      className="flex items-center justify-between rounded-lg border border-codey-border bg-codey-bg px-4 py-3"
                    >
                      <div className="flex items-center gap-3">
                        <CreditCard className="h-5 w-5 text-codey-text-dim" />
                        <div>
                          <p className="text-sm text-codey-text capitalize">
                            {pm.brand} ending in {pm.last4}
                          </p>
                          <p className="text-xs text-codey-text-muted">
                            Expires {pm.exp_month}/{pm.exp_year}
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        {pm.is_default && (
                          <span className="rounded-full bg-codey-green/10 px-2 py-0.5 text-xs font-medium text-codey-green">
                            Default
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Add card */}
              {showAddCard && setupSecret ? (
                <div className="rounded-lg border border-codey-border p-4">
                  <StripeProvider clientSecret={setupSecret}>
                    <SetupForm
                      onSuccess={handleSetupSuccess}
                      onError={() => {}}
                    />
                  </StripeProvider>
                </div>
              ) : (
                <button
                  onClick={handleAddCard}
                  disabled={setupLoading}
                  className="flex items-center gap-2 rounded-lg border border-codey-border px-4 py-2.5 text-sm text-codey-text-dim hover:bg-codey-card-hover hover:text-codey-text"
                >
                  {setupLoading ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Plus className="h-4 w-4" />
                  )}
                  Add payment method
                </button>
              )}
            </div>
          </Section>

          {/* ── Danger Zone ────────────────────────────────────────────── */}
          <Section title="Danger Zone" icon={AlertTriangle} danger>
            <div className="space-y-4">
              <p className="text-sm text-codey-text-dim">
                Permanently delete your account and all associated data. This
                action cannot be undone.
              </p>

              {deleteConfirm ? (
                <div className="space-y-3 rounded-lg border border-codey-red/30 bg-codey-red-glow p-4">
                  <p className="text-sm text-codey-red">
                    Type <strong>DELETE</strong> to confirm account deletion.
                  </p>
                  <input
                    type="text"
                    value={deleteInput}
                    onChange={(e) => setDeleteInput(e.target.value)}
                    placeholder="DELETE"
                    className="w-full rounded-lg border border-codey-red/30 bg-codey-bg px-4 py-2.5 text-sm text-codey-text placeholder:text-codey-text-muted focus:border-codey-red focus:outline-none"
                  />
                  <div className="flex gap-3">
                    <button
                      onClick={() => {
                        setDeleteConfirm(false);
                        setDeleteInput("");
                      }}
                      className="rounded-lg border border-codey-border px-4 py-2 text-sm text-codey-text-dim hover:bg-codey-card-hover"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={handleDeleteAccount}
                      disabled={deleteInput !== "DELETE" || deleting}
                      className="flex items-center gap-2 rounded-lg bg-codey-red px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
                    >
                      {deleting ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Trash2 className="h-4 w-4" />
                      )}
                      Delete my account
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  onClick={() => setDeleteConfirm(true)}
                  className="flex items-center gap-2 rounded-lg border border-codey-red/30 px-4 py-2 text-sm text-codey-red hover:bg-codey-red-glow"
                >
                  <Trash2 className="h-4 w-4" />
                  Delete account
                </button>
              )}
            </div>
          </Section>
        </div>
      </DashboardLayout>
    </ProtectedRoute>
  );
}
