import { getApiBaseUrl } from "./runtime-config";

const BASE_URL = getApiBaseUrl();

function getBrowserFrontendOrigin(): string | null {
  if (typeof window === "undefined") {
    return null;
  }

  return window.location.origin;
}

function getBrowserApiBaseUrl(): string | null {
  if (typeof window === "undefined") {
    return null;
  }

  return new URL(BASE_URL, window.location.origin).toString().replace(/\/$/, "");
}

// ── Types ──────────────────────────────────────────────────────────────────

export interface User {
  id: string;
  email: string;
  name: string | null;
  avatar_url: string | null;
  github_connected: boolean;
  plan: string;
  plan_display_name?: string;
  plan_status: string;
  credits_remaining: number;
  topup_credits: number;
  total_credits: number;
  credits_used_this_month?: number;
  monthly_allocation?: number;
  subscription_period_end?: string | null;
  created_at: string;
  last_active?: string | null;
}

export interface CreditBalance {
  subscription_credits: number;
  topup_credits: number;
  total: number;
  used_this_month: number;
  plan: string;
  monthly_allocation: number;
}

export interface CreditTransaction {
  id: string;
  amount: number;
  type: "usage" | "topup" | "plan_refresh" | "refund";
  description: string;
  session_id: string | null;
  created_at: string;
}

export interface PatchReceiptClaim {
  claim: string;
  sourceSection?: string;
  matchedByDiff: boolean;
  checkable?: boolean;
  evidence?: string | null;
  mismatchReason?: string | null;
}

export interface PatchReceipt {
  receiptId: string;
  runId: string;
  intent: string;
  status: string;
  filesRead: string[];
  filesChanged: Array<{
    path: string;
    additions: number;
    deletions: number;
    changeKind: string;
  }>;
  diffText: string;
  diffHash: string;
  claimsMade: PatchReceiptClaim[];
  commandsRun: Array<{ command: string; exitCode: number; passed: boolean }>;
  validation: {
    syntaxChecked: boolean;
    typecheckPassed?: boolean | null;
    lintPassed?: boolean | null;
    testsPassed?: boolean | null;
    buildPassed?: boolean | null;
    claimVerificationPassed: boolean;
    patchApplied: boolean;
    filesModifiedCount: number;
  };
  healthScore: number;
  finalSummary: string;
}

export interface Session {
  id: string;
  mode: string;
  repo_id: string | null;
  prompt: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  credits_used: number;
  health_score_before: number | null;
  health_score_after: number | null;
  lines_generated: number;
  files_modified: number;
  run_status: string | null;
  verification_passed: boolean | null;
  health_score: number | null;
  patch_receipt: PatchReceipt | null;
  plan: string | null;
  result_summary: string | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface PromptHealthReport {
  phase: string;
  health_score: number;
  coherence: number;
  stability: number;
  total_nodes: number;
  total_edges: number;
  summary: string;
  recommendations: string[];
}

export interface PromptSessionResult {
  session_id: string;
  estimated_credits: number;
  output: string | null;
  lines_generated: number;
  status: string;
  security_score: number | null;
  security_issues: string[];
  health: PromptHealthReport | null;
}

export interface AnalyzeComponent {
  name: string;
  file_path: string;
  stress: number;
  coupling: number;
  cohesion: number;
  cascade_depth: number;
}

export interface AnalyzeReport {
  report: {
    phase: string;
    health_score: number;
    coherence: number;
    stability: number;
    total_nodes: number;
    total_edges: number;
    mean_coupling: number;
    mean_cohesion: number;
    highest_stress_component: string;
    highest_stress_value: number;
    top_components: AnalyzeComponent[];
    summary: string;
  };
  recommendations: string[];
}

export interface Repo {
  id: string;
  github_url: string;
  name: string;
  default_branch: string;
  last_analyzed_at: string | null;
  health_score: number | null;
  connected_at: string;
  autonomous_mode_enabled?: boolean;
  autonomous_config?: Record<string, unknown> | null;
}

interface RepoApiResponse {
  id: string;
  full_name: string | null;
  clone_url: string | null;
  default_branch: string | null;
  language: string | null;
  autonomous_mode_enabled: boolean;
  autonomous_config: Record<string, unknown> | null;
  last_analyzed: string | null;
  es_score: number | null;
  created_at: string;
}

interface SessionApiResponse {
  id: string;
  mode: string;
  prompt: string | null;
  repo_connected: string | null;
  status: Session["status"];
  credits_charged: number;
  lines_generated: number;
  files_modified: number;
  nfet_phase_before?: string | null;
  nfet_phase_after: string | null;
  es_score_before?: number | null;
  es_score_after: number | null;
  output_summary: string | null;
  run_status?: string | null;
  verification_passed?: boolean | null;
  health_score?: number | null;
  patch_receipt?: PatchReceipt | null;
  error_message?: string | null;
  started_at: string;
  completed_at: string | null;
}

export interface Plan {
  id: string;
  key: string;
  name: string;
  price_monthly: number;
  credits: number;
  rollover: number;
  features: {
    github_repos: number;
    autonomous_mode: boolean;
    priority: boolean;
    max_upload_mb: number;
    seats?: number | null;
  };
}

export interface Invoice {
  id: string;
  number: string | null;
  status: string | null;
  amount_due: number;
  amount_paid: number;
  currency: string;
  period_start: string;
  period_end: string;
  hosted_invoice_url: string | null;
  pdf: string | null;
  created: string;
}

export interface PaymentMethod {
  id: string;
  brand: string;
  last4: string;
  exp_month: number;
  exp_year: number;
  is_default?: boolean;
}

export interface Subscription {
  id: string;
  plan: string;
  status: "active" | "cancelled" | "past_due";
  current_period_end: string;
  cancel_at_period_end: boolean;
}

export interface ApiKeyRecord {
  id: string;
  name: string | null;
  key_prefix: string | null;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  is_expired: boolean;
}

export interface TopupResult {
  client_secret: string;
}

export interface SubscriptionResult {
  client_secret: string | null;
  subscription_id: string | null;
  type: "setup_required" | "payment_required" | "active";
}

export interface ConfirmSubscriptionResult {
  plan: string;
  credits: number;
  subscription_id: string;
  status: string;
}

export interface ChangePlanResult {
  old_plan: string;
  new_plan: string;
  credits: number;
  subscription_id: string | null;
}

export interface CancelSubscriptionResult {
  status: string;
  access_until: string;
  subscription_id: string | null;
}

export interface ApiError {
  detail: string;
  status: number;
}

export interface OAuthProviders {
  github: boolean;
  google: boolean;
}

// ── Client ─────────────────────────────────────────────────────────────────

class ApiClient {
  private token: string | null = null;

  setToken(token: string | null) {
    this.token = token;
  }

  getToken(): string | null {
    return this.token;
  }

  private async request<T>(
    path: string,
    options: RequestInit = {}
  ): Promise<T> {
    const headers: Record<string, string> = {
      ...(options.headers as Record<string, string>),
    };
    const frontendOrigin = getBrowserFrontendOrigin();
    const apiBaseUrl = getBrowserApiBaseUrl();
    if (!(options.body instanceof FormData) && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    if (frontendOrigin) {
      headers["X-Codey-Frontend-Origin"] = frontendOrigin;
    }
    if (apiBaseUrl) {
      headers["X-Codey-Api-Base-Url"] = apiBaseUrl;
    }

    if (this.token) {
      headers["Authorization"] = `Bearer ${this.token}`;
    }

    const res = await fetch(`${BASE_URL}${path}`, {
      ...options,
      headers,
      credentials: "include",
    });

    if (res.status === 401) {
      this.setToken(null);
      throw { detail: "Unauthorized", status: 401 } as ApiError;
    }

    if (!res.ok) {
      const body = await res.json().catch(() => ({ detail: res.statusText }));
      throw {
        detail: body.detail || res.statusText,
        status: res.status,
      } as ApiError;
    }

    if (res.status === 204) {
      return undefined as T;
    }

    return res.json();
  }

  private mapSession(data: SessionApiResponse): Session {
    return {
      id: data.id,
      mode: data.mode,
      repo_id: data.repo_connected,
      prompt: data.prompt || "",
      status: data.status,
      credits_used: data.credits_charged,
      health_score_before: data.es_score_before ?? null,
      health_score_after: data.es_score_after,
      lines_generated: data.lines_generated,
      files_modified: data.files_modified,
      run_status: data.run_status ?? null,
      verification_passed: data.verification_passed ?? null,
      health_score: data.health_score ?? null,
      patch_receipt: data.patch_receipt ?? null,
      plan: data.nfet_phase_after,
      result_summary: data.output_summary,
      error_message: data.error_message ?? null,
      created_at: data.started_at,
      completed_at: data.completed_at,
    };
  }

  // ── Auth ──────────────────────────────────────────────────────────────

  async signup(email: string, password: string, name?: string): Promise<{ token: string; user: User }> {
    const data = await this.request<{ token: string; user: User }>("/auth/signup", {
      method: "POST",
      body: JSON.stringify({ email, password, name: name || undefined }),
    });
    this.setToken(data.token);
    return data;
  }

  async login(email: string, password: string): Promise<{ token: string; user: User }> {
    const data = await this.request<{ token: string; user: User }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    this.setToken(data.token);
    return data;
  }

  async getGitHubOAuthUrl(intent: "login" | "connect" = "login"): Promise<{ url: string; state: string }> {
    const query = intent === "login" ? "" : `?intent=${encodeURIComponent(intent)}`;
    return this.request<{ url: string; state: string }>(`/auth/github${query}`);
  }

  async getGoogleOAuthUrl(): Promise<{ url: string; state: string }> {
    return this.request<{ url: string; state: string }>("/auth/google");
  }

  async getAuthProviders(): Promise<OAuthProviders> {
    return this.request<OAuthProviders>("/auth/providers");
  }

  async loginWithGitHub(code: string, state: string): Promise<{ token: string; user: User }> {
    const data = await this.request<{ token: string; user: User }>(
      `/auth/github/callback?code=${encodeURIComponent(code)}&state=${encodeURIComponent(state)}`
    );
    this.setToken(data.token);
    return data;
  }

  async loginWithGoogle(code: string, state: string): Promise<{ token: string; user: User }> {
    const data = await this.request<{ token: string; user: User }>(
      `/auth/google/callback?code=${encodeURIComponent(code)}&state=${encodeURIComponent(state)}`
    );
    this.setToken(data.token);
    return data;
  }

  async requestPasswordReset(email: string): Promise<{ message: string }> {
    return this.request<{ message: string }>("/auth/reset-password", {
      method: "POST",
      body: JSON.stringify({ email }),
    });
  }

  async confirmPasswordReset(token: string, password: string): Promise<{ message: string }> {
    return this.request<{ message: string }>("/auth/reset-password/confirm", {
      method: "POST",
      body: JSON.stringify({ token, password }),
    });
  }

  logout() {
    void fetch(`${BASE_URL}/auth/logout`, {
      method: "POST",
      credentials: "include",
    }).catch(() => undefined);
    this.setToken(null);
    if (typeof window !== "undefined") {
      window.location.href = "/auth/login";
    }
  }

  // ── User ──────────────────────────────────────────────────────────────

  async getMe(): Promise<User> {
    return this.request<User>("/users/me");
  }

  async getByok(): Promise<{
    configured: boolean;
    provider: string | null;
    model: string | null;
    has_key: boolean;
    allowed_providers: string[];
  }> {
    return this.request("/users/me/byok");
  }

  async setByok(
    provider: string,
    apiKey: string,
    model?: string,
  ): Promise<{ configured: boolean; provider: string | null; model: string | null; has_key: boolean }> {
    return this.request("/users/me/byok", {
      method: "PUT",
      body: JSON.stringify({ provider, api_key: apiKey, model: model || null }),
    });
  }

  async clearByok(): Promise<void> {
    return this.request<void>("/users/me/byok", { method: "DELETE" });
  }

  async updateProfile(data: { email?: string; name?: string }): Promise<User> {
    return this.request<User>("/users/me", {
      method: "PATCH",
      body: JSON.stringify(data),
    });
  }

  async changePassword(currentPassword: string, newPassword: string): Promise<void> {
    return this.request<void>("/users/me/password", {
      method: "POST",
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    });
  }

  async disconnectGitHub(): Promise<void> {
    return this.request<void>("/users/me/github", {
      method: "DELETE",
    });
  }

  async connectGitHubToken(token: string): Promise<User> {
    return this.request<User>("/users/me/github/token", {
      method: "POST",
      body: JSON.stringify({ token }),
    });
  }

  // ── Credits ───────────────────────────────────────────────────────────

  async getCredits(): Promise<CreditBalance> {
    return this.request<CreditBalance>("/credits/balance");
  }

  async getCreditHistory(params?: {
    limit?: number;
    offset?: number;
    type?: string;
  }): Promise<{ transactions: CreditTransaction[]; total: number }> {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set("limit", String(params.limit));
    if (params?.offset) searchParams.set("offset", String(params.offset));
    if (params?.type) searchParams.set("type", params.type);
    const query = searchParams.toString();
    const data = await this.request<{ transactions: CreditTransaction[] }>(
      `/credits/history${query ? `?${query}` : ""}`
    );
    return {
      transactions: data.transactions,
      total: data.transactions.length,
    };
  }

  // ── Sessions ──────────────────────────────────────────────────────────

  async generateCode(data: {
    repo_id?: string;
    prompt: string;
    language?: string;
  }): Promise<PromptSessionResult> {
    return this.request<PromptSessionResult>("/sessions/prompt", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  async getSession(id: string): Promise<Session> {
    const data = await this.request<SessionApiResponse>(`/sessions/${id}`);
    return this.mapSession(data);
  }

  async getSessions(params?: {
    limit?: number;
    offset?: number;
    status?: string;
  }): Promise<{ sessions: Session[]; total: number }> {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set("limit", String(params.limit));
    if (params?.offset) searchParams.set("offset", String(params.offset));
    if (params?.status) searchParams.set("status", params.status);
    const query = searchParams.toString();
    const data = await this.request<{
      sessions: SessionApiResponse[];
      total: number;
      limit: number;
      offset: number;
    }>(`/users/me/sessions${query ? `?${query}` : ""}`);
    return {
      sessions: data.sessions.map((session) => this.mapSession(session)),
      total: data.total,
    };
  }

  async cancelSession(id: string): Promise<void> {
    return this.request<void>(`/sessions/${id}/cancel`, { method: "POST" });
  }

  async commitSession(id: string): Promise<{ session_id: string; credits_charged: number; message: string }> {
    return this.request<{ session_id: string; credits_charged: number; message: string }>(
      `/sessions/${id}/commit`,
      { method: "POST" }
    );
  }

  async analyzeUpload(files: File[]): Promise<AnalyzeReport> {
    const formData = new FormData();
    files.forEach((file) => formData.append("files", file));
    return this.request<AnalyzeReport>("/analyze/upload", {
      method: "POST",
      body: formData,
    });
  }

  // ── Repos ─────────────────────────────────────────────────────────────

  async getRepos(): Promise<Repo[]> {
    const repos = await this.request<RepoApiResponse[]>("/repos");
    return repos.map((repo) => ({
      id: repo.id,
      github_url: repo.clone_url || repo.full_name || "",
      name: repo.full_name?.split("/").pop() || repo.full_name || "Repository",
      default_branch: repo.default_branch || "main",
      last_analyzed_at: repo.last_analyzed,
      health_score: repo.es_score,
      connected_at: repo.created_at,
      autonomous_mode_enabled: repo.autonomous_mode_enabled,
      autonomous_config: repo.autonomous_config,
    }));
  }

  async connectRepo(data: {
    github_url: string;
    branch?: string;
  }): Promise<Repo> {
    const repo = await this.request<RepoApiResponse>("/repos", {
      method: "POST",
      body: JSON.stringify({
        github_repo_url: data.github_url,
      }),
    });
    return {
      id: repo.id,
      github_url: repo.clone_url || repo.full_name || "",
      name: repo.full_name?.split("/").pop() || repo.full_name || "Repository",
      default_branch: repo.default_branch || "main",
      last_analyzed_at: repo.last_analyzed,
      health_score: repo.es_score,
      connected_at: repo.created_at,
      autonomous_mode_enabled: repo.autonomous_mode_enabled,
      autonomous_config: repo.autonomous_config,
    };
  }

  async disconnectRepo(id: string): Promise<void> {
    return this.request<void>(`/repos/${id}`, { method: "DELETE" });
  }

  // ── Plans & Subscriptions ────────────────────────────────────────────

  async getPlans(): Promise<Plan[]> {
    const data = await this.request<{
      plans: Array<{
        key: string;
        name: string;
        price_monthly: number;
        credits: number;
        rollover: number;
        features: Plan["features"];
      }>;
    }>("/billing/plans");
    return data.plans.map((plan) => ({
      ...plan,
      id: plan.key,
    }));
  }

  async subscribe(plan: string): Promise<SubscriptionResult> {
    return this.request<SubscriptionResult>("/billing/subscribe", {
      method: "POST",
      body: JSON.stringify({ plan }),
    });
  }

  async confirmSubscription(
    subscriptionId: string
  ): Promise<ConfirmSubscriptionResult> {
    return this.request<ConfirmSubscriptionResult>("/billing/subscribe/confirm", {
      method: "POST",
      body: JSON.stringify({ subscription_id: subscriptionId }),
    });
  }

  async changePlan(plan: string): Promise<ChangePlanResult> {
    return this.request<ChangePlanResult>("/billing/change-plan", {
      method: "POST",
      body: JSON.stringify({ plan }),
    });
  }

  async cancelSubscription(): Promise<CancelSubscriptionResult> {
    return this.request<CancelSubscriptionResult>("/billing/cancel", {
      method: "POST",
    });
  }

  // ── Payments ──────────────────────────────────────────────────────────

  async createTopup(packageKey: string): Promise<TopupResult> {
    return this.request<TopupResult>("/billing/topup", {
      method: "POST",
      body: JSON.stringify({ package: packageKey }),
    });
  }

  async getPaymentMethods(): Promise<PaymentMethod[]> {
    return this.request<PaymentMethod[]>("/billing/payment-methods");
  }

  async deletePaymentMethod(methodId: string): Promise<void> {
    return this.request<void>(`/billing/payment-methods/${methodId}`, {
      method: "DELETE",
    });
  }

  async getSetupIntent(): Promise<{ client_secret: string }> {
    return this.request("/billing/payment-methods", { method: "POST" });
  }

  async getInvoices(): Promise<Invoice[]> {
    return this.request<Invoice[]>("/billing/invoices");
  }

  async claimReferral(
    referrerId: string
  ): Promise<{ status: string; referral_id: string }> {
    return this.request<{ status: string; referral_id: string }>("/referrals/claim", {
      method: "POST",
      body: JSON.stringify({ referrer_id: referrerId }),
    });
  }

  // ── API keys ─────────────────────────────────────────────────────────

  async getApiKeys(): Promise<ApiKeyRecord[]> {
    return this.request<ApiKeyRecord[]>("/users/me/api-keys");
  }

  async createApiKey(
    name: string,
    expiresInDays?: number
  ): Promise<{ api_key: string; key: ApiKeyRecord }> {
    return this.request<{ api_key: string; key: ApiKeyRecord }>("/users/me/api-keys", {
      method: "POST",
      body: JSON.stringify({
        name,
        expires_in_days: expiresInDays,
      }),
    });
  }

  async revokeApiKey(id: string): Promise<void> {
    return this.request<void>(`/users/me/api-keys/${id}`, {
      method: "DELETE",
    });
  }

  // ── Generic helpers ──────────────────────────────────────────────────

  async get<T>(path: string): Promise<T> {
    return this.request<T>(path);
  }

  async post<T>(path: string, body?: Record<string, unknown>): Promise<T> {
    return this.request<T>(path, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    });
  }

  async patch<T>(path: string, body?: Record<string, unknown>): Promise<T> {
    return this.request<T>(path, {
      method: "PATCH",
      body: body ? JSON.stringify(body) : undefined,
    });
  }

  async delete<T>(path: string, body?: Record<string, unknown>): Promise<T> {
    return this.request<T>(path, {
      method: "DELETE",
      body: body ? JSON.stringify(body) : undefined,
    });
  }
}

export const api = new ApiClient();
