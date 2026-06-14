import type { MetadataRoute } from "next";

const SITE = process.env.NEXT_PUBLIC_SITE_URL || "https://codey.imagineqira.com";

// Public, indexable routes only (dashboard/settings/api are auth-gated).
export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date();
  const routes: Array<{ path: string; priority: number; freq: "weekly" | "monthly" }> = [
    { path: "", priority: 1.0, freq: "weekly" },
    { path: "/pricing", priority: 0.8, freq: "weekly" },
    { path: "/auth/signup", priority: 0.7, freq: "monthly" },
    { path: "/auth/login", priority: 0.5, freq: "monthly" },
    { path: "/changelog", priority: 0.5, freq: "weekly" },
    { path: "/privacy", priority: 0.3, freq: "monthly" },
    { path: "/terms", priority: 0.3, freq: "monthly" },
  ];
  return routes.map((r) => ({
    url: `${SITE}${r.path}`,
    lastModified: now,
    changeFrequency: r.freq,
    priority: r.priority,
  }));
}
