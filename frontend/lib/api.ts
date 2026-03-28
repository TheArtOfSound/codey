const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ── Types ──────────────────────────────────────────────────────────────────

export interface User {
  id: string;
  email: string;
  name: string | null;
  avatar_url: string | null;
  plan: string;
  plan_status: string;
  credits_remaining: number;
  topup_credits: number;
  total_credits: number;
  created_at: string;
}

export interface CreditBalance {
  credits_remaining: number;
  plan_credits: number;
  topup_credits: number;
  next_refresh_at: string | null;
}

export interface CreditTransaction {
  id: string;
  amount: number;
  type: "usage" | "topup" | "plan_refresh" | "refund";
  description: string;
  session_id: string | null;
  created_at: string;
}

export interface Session {
  id: string;
  repo_id: string | null;
  prompt: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  credits_used: number;
  nfet_score_before: number | null;
  nfet_score_after: number | null;
  plan: string | null;
  result_summary: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface Repo {
  id: string;
  github_url: string;
  name: string;
  default_branch: string;
  last_analyzed_at: string | null;
  nfet_score: number | null;
  connected_at: string;
}

export interface Plan {
  id: string;
  name: string;
  price_monthly: number;
  credits_per_month: number;
  features: string[];
  stripe_price_id: string;
}

export interface PaymentMethod {
  id: string;
  brand: string;
  last4: string;
  exp_month: number;
  exp_year: number;
  is_default: boolean;
}

export interface Subscription {
  id: string;
  plan: string;
  status: "active" | "cancelled" | "past_due";
  current_period_end: string;
  cancel_at_period_end: boolean;
}

export interface TopupResult {
  client_secret: string;
  amount: number;
  credits: number;
}

export interface SubscriptionResult {
  client_secret: string | null;
  subscription_id: string;
  status: string;
}

export interface ApiError {
  detail: string;
  status: number;
}

// ── Client ─────────────────────────────────────────────────────────────────

class ApiClient {
  private token: string | null = null;

  constructor() {
    if (typeof window !== "undefined") {
      this.token = localStorage.getItem("codey_token");
    }
  }

  setToken(token: string | null) {
    this.token = token;
    if (typeof window !== "undefined") {
      if (token) {
        localStorage.setItem("codey_token", token);
      } else {
        localStorage.removeItem("codey_token");
      }
    }
  }

  getToken(): string | null {
    return this.token;
  }

  private async request<T>(
    path: string,
    options: RequestInit = {}
  ): Promise<T> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...(options.headers as Record<string, string>),
    };

    if (this.token) {
      headers["Authorization"] = `Bearer ${this.token}`;
    }

    const res = await fetch(`${BASE_URL}${path}`, {
      ...options,
      headers,
    });

    if (res.status === 401) {
      this.setToken(null);
      if (typeof window !== "undefined") {
        window.location.href = "/auth/login";
      }
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

  async loginWithGitHub(code: string): Promise<{ token: string; user: User }> {
    const data = await this.request<{ token: string; user: User }>(
      `/auth/github/callback?code=${encodeURIComponent(code)}`
    );
    this.setToken(data.token);
    return data;
  }

  async loginWithGoogle(code: string): Promise<{ token: string; user: User }> {
    const data = await this.request<{ token: string; user: User }>(
      `/auth/google/callback?code=${encodeURIComponent(code)}`
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
    this.setToken(null);
    if (typeof window !== "undefined") {
      window.location.href = "/auth/login";
    }
  }

  // ── User ──────────────────────────────────────────────────────────────

  async getMe(): Promise<User> {
    return this.request<User>("/users/me");
  }

  async updateProfile(data: { email?: string; name?: string }): Promise<User> {
    return this.request<User>("/users/me", {
      method: "PATCH",
      body: JSON.stringify(data),
    });
  }

  // ── Credits ───────────────────────────────────────────────────────────

  async getCredits(): Promise<CreditBalance> {
    return this.request<CreditBalance>("/credits");
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
    return this.request(`/credits/history${query ? `?${query}` : ""}`);
  }

  // ── Sessions ──────────────────────────────────────────────────────────

  async createSession(data: {
    repo_id?: string;
    prompt: string;
  }): Promise<Session> {
    return this.request<Session>("/sessions", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  async getSession(id: string): Promise<Session> {
    return this.request<Session>(`/sessions/${id}`);
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
    return this.request(`/sessions${query ? `?${query}` : ""}`);
  }

  async cancelSession(id: string): Promise<void> {
    return this.request<void>(`/sessions/${id}/cancel`, { method: "POST" });
  }

  // ── Repos ─────────────────────────────────────────────────────────────

  async getRepos(): Promise<Repo[]> {
    return this.request<Repo[]>("/repos");
  }

  async connectRepo(data: {
    github_url: string;
    branch?: string;
  }): Promise<Repo> {
    return this.request<Repo>("/repos", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  async disconnectRepo(id: string): Promise<void> {
    return this.request<void>(`/repos/${id}`, { method: "DELETE" });
  }

  async analyzeRepo(id: string): Promise<{ nfet_score: number }> {
    return this.request(`/repos/${id}/analyze`, { method: "POST" });
  }

  // ── Plans & Subscriptions ────────────────────────────────────────────

  async getPlans(): Promise<Plan[]> {
    return this.request<Plan[]>("/plans");
  }

  async getSubscription(): Promise<Subscription | null> {
    return this.request<Subscription | null>("/subscription");
  }

  async subscribe(priceId: string): Promise<SubscriptionResult> {
    return this.request<SubscriptionResult>("/subscription", {
      method: "POST",
      body: JSON.stringify({ price_id: priceId }),
    });
  }

  async confirmSubscription(subscriptionId: string): Promise<Subscription> {
    return this.request<Subscription>("/subscription/confirm", {
      method: "POST",
      body: JSON.stringify({ subscription_id: subscriptionId }),
    });
  }

  async cancelSubscription(): Promise<void> {
    return this.request<void>("/subscription", { method: "DELETE" });
  }

  async reactivateSubscription(): Promise<Subscription> {
    return this.request<Subscription>("/subscription/reactivate", {
      method: "POST",
    });
  }

  // ── Payments ──────────────────────────────────────────────────────────

  async createTopup(credits: number): Promise<TopupResult> {
    return this.request<TopupResult>("/payments/topup", {
      method: "POST",
      body: JSON.stringify({ credits }),
    });
  }

  async getPaymentMethods(): Promise<PaymentMethod[]> {
    return this.request<PaymentMethod[]>("/payments/methods");
  }

  async setDefaultPaymentMethod(methodId: string): Promise<void> {
    return this.request<void>(`/payments/methods/${methodId}/default`, {
      method: "POST",
    });
  }

  async deletePaymentMethod(methodId: string): Promise<void> {
    return this.request<void>(`/payments/methods/${methodId}`, {
      method: "DELETE",
    });
  }

  async getSetupIntent(): Promise<{ client_secret: string }> {
    return this.request("/payments/setup-intent", { method: "POST" });
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

  async delete<T>(path: string): Promise<T> {
    return this.request<T>(path, { method: "DELETE" });
  }
}

export const api = new ApiClient();
