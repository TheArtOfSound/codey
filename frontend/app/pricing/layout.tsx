import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Pricing",
  description:
    "Codey pricing \u2014 start free, then scale. Credit-based plans for repo scanning, verified patches, and autopilot maintenance across your GitHub repositories.",
  alternates: { canonical: "/pricing" },
  openGraph: { title: "Pricing \u00b7 Codey", url: "/pricing" },
};

export default function PricingLayout({ children }: { children: ReactNode }) {
  return children;
}
