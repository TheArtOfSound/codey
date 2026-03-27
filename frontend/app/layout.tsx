import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { AuthProvider } from "@/lib/auth";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Codey — Structural-Aware Coding Intelligence",
  description:
    "AI-powered coding sessions with structural health analysis. Write better code, understand structural impact.",
  keywords: ["AI", "coding", "structural analysis", "developer tools"],
  openGraph: {
    title: "Codey — Structural-Aware Coding Intelligence",
    description:
      "AI-powered coding sessions with structural health analysis. Write better code, understand structural impact.",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${inter.variable} dark`}>
      <body className="min-h-screen bg-codey-bg font-sans text-codey-text antialiased">
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
