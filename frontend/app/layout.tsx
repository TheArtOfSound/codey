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

export const metadata: Metadata = {
  title: "Codey — The coding agent that understands your codebase",
  description:
    "Codey analyzes your codebase as a living network. Every line it writes, it knows exactly where that line sits — and what breaking it would cost.",
  keywords: ["AI", "coding", "structural analysis", "developer tools", "coding agent"],
  openGraph: {
    title: "Codey — The coding agent that understands your codebase",
    description:
      "Codey analyzes your codebase as a living network. Every line it writes, it knows exactly where that line sits — and what breaking it would cost.",
    type: "website",
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
