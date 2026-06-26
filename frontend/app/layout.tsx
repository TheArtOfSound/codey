import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { AuthProvider } from "@/lib/auth";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains",
  display: "swap",
});

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || "https://codey.imagineqira.com";
const DESCRIPTION =
  "Codey runs continuous repo management across scan, bug repair, CI rescue, security passes, dependency upkeep, docs, and release blockers — every change ships with a verifiable patch receipt.";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: "Codey — The autonomous repo operator",
    template: "%s · Codey",
  },
  description: DESCRIPTION,
  keywords: [
    "repo management",
    "autonomous repo operator",
    "AI coding agent",
    "CI rescue",
    "security automation",
    "dependency maintenance",
    "patch receipt",
  ],
  applicationName: "Codey",
  alternates: { canonical: "/" },
  openGraph: {
    title: "Codey — The autonomous repo operator",
    description: DESCRIPTION,
    url: SITE_URL,
    siteName: "Codey",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "Codey — The autonomous repo operator",
    description: DESCRIPTION,
  },
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      "max-image-preview": "large",
      "max-snippet": -1,
    },
  },
};

const jsonLd = {
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  name: "Codey",
  applicationCategory: "DeveloperApplication",
  operatingSystem: "Web",
  url: SITE_URL,
  description: DESCRIPTION,
  offers: { "@type": "Offer", price: "0", priceCurrency: "USD" },
  publisher: { "@type": "Organization", name: "Qira", url: SITE_URL },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrainsMono.variable} dark`}>
      <body className="min-h-screen bg-codey-bg-deep font-sans text-codey-text-primary antialiased">
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
        />
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
