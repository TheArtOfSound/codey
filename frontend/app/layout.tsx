import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { AuthProvider } from "@/lib/auth";
import "./globals.css";

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || "https://codey.imagineqira.com";

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

export const metadata: Metadata = {
  title: "Codey — The autonomous repo operator",
  description:
    "Codey runs continuous repo management across scan, bug repair, CI rescue, security passes, dependency upkeep, docs, and release blockers.",
  keywords: ["repo management", "autonomous repo operator", "CI rescue", "security automation", "dependency maintenance"],
  metadataBase: new URL(SITE_URL),
  alternates: {
    canonical: "/",
  },
  icons: {
    icon: "/favicon.svg",
  },
  openGraph: {
    title: "Codey — The autonomous repo operator",
    description:
      "Codey runs continuous repo management across scan, bug repair, CI rescue, security passes, dependency upkeep, docs, and release blockers.",
    type: "website",
    url: SITE_URL,
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrainsMono.variable} dark`}>
      <body className="min-h-screen bg-codey-bg-deep font-sans text-codey-text-primary antialiased">
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
