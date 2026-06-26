import type { MetadataRoute } from "next";

const SITE = process.env.NEXT_PUBLIC_SITE_URL || "https://codey.imagineqira.com";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        // Keep auth-gated / app-internal routes out of the index.
        disallow: [
          "/dashboard",
          "/settings",
          "/vault",
          "/credits",
          "/onboarding",
          "/api/",
          "/auth/callback",
          "/auth/reset-password",
        ],
      },
    ],
    sitemap: `${SITE}/sitemap.xml`,
    host: SITE,
  };
}
