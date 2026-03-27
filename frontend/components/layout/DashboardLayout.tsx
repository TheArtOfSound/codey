"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth";
import {
  BarChart3,
  Code,
  Network,
  GitBranch,
  Bot,
  Zap,
  Settings,
  Menu,
  X,
  ChevronDown,
  CreditCard,
  Upload,
} from "lucide-react";

// ── Sidebar Items ────────────────────────────────────────────────────────────

interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}

const sidebarItems: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: BarChart3 },
  { href: "/dashboard/prompt", label: "New Prompt", icon: Code },
  { href: "/dashboard/analyze", label: "Analyze", icon: Network },
  { href: "/dashboard/repos", label: "Repos", icon: GitBranch },
  { href: "/dashboard/autonomous", label: "Autonomous", icon: Bot },
  { href: "/dashboard/credits", label: "Credits", icon: Zap },
  { href: "/settings", label: "Settings", icon: Settings },
];

// ── Layout ───────────────────────────────────────────────────────────────────

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [userMenuOpen, setUserMenuOpen] = useState(false);

  // Build breadcrumb from pathname
  const segments = pathname.split("/").filter(Boolean);
  const breadcrumb = segments.map((seg, i) => ({
    label: seg.charAt(0).toUpperCase() + seg.slice(1),
    href: "/" + segments.slice(0, i + 1).join("/"),
    isLast: i === segments.length - 1,
  }));

  function isActive(href: string) {
    if (href === "/dashboard") return pathname === "/dashboard";
    return pathname.startsWith(href);
  }

  return (
    <div className="flex h-screen bg-codey-bg">
      {/* ── Sidebar (desktop) ─────────────────────────────────────────── */}
      <aside className="hidden w-60 shrink-0 flex-col border-r border-codey-border bg-codey-card/50 lg:flex">
        {/* Logo */}
        <div className="flex h-14 items-center border-b border-codey-border px-5">
          <Link href="/dashboard" className="text-lg font-bold tracking-tight">
            <span className="text-codey-green">C</span>ODEY
          </Link>
        </div>

        {/* Nav items */}
        <nav className="flex-1 overflow-y-auto px-3 py-4">
          <ul className="space-y-1">
            {sidebarItems.map((item) => {
              const active = isActive(item.href);
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
                      active
                        ? "bg-codey-green/10 font-medium text-codey-green"
                        : "text-codey-text-dim hover:bg-codey-card-hover hover:text-codey-text"
                    }`}
                  >
                    <item.icon className="h-4 w-4 shrink-0" />
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        {/* Credit balance at bottom */}
        {user && (
          <div className="border-t border-codey-border px-4 py-4">
            <Link
              href="/dashboard/credits"
              className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-codey-text-dim transition-colors hover:bg-codey-card-hover hover:text-codey-text"
            >
              <Zap className="h-4 w-4 text-codey-green" />
              <span className="font-medium text-codey-text">
                {user.credits_remaining.toLocaleString()}
              </span>
              <span className="text-xs">credits</span>
            </Link>
          </div>
        )}
      </aside>

      {/* ── Mobile sidebar overlay ────────────────────────────────────── */}
      {sidebarOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/60"
            onClick={() => setSidebarOpen(false)}
          />
          {/* Drawer */}
          <aside className="absolute left-0 top-0 flex h-full w-64 flex-col bg-codey-card shadow-2xl animate-slide-up">
            <div className="flex h-14 items-center justify-between border-b border-codey-border px-5">
              <Link
                href="/dashboard"
                className="text-lg font-bold tracking-tight"
              >
                <span className="text-codey-green">C</span>ODEY
              </Link>
              <button
                onClick={() => setSidebarOpen(false)}
                className="rounded-md p-1 text-codey-text-dim hover:bg-codey-card-hover"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <nav className="flex-1 overflow-y-auto px-3 py-4">
              <ul className="space-y-1">
                {sidebarItems.map((item) => {
                  const active = isActive(item.href);
                  return (
                    <li key={item.href}>
                      <Link
                        href={item.href}
                        onClick={() => setSidebarOpen(false)}
                        className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
                          active
                            ? "bg-codey-green/10 font-medium text-codey-green"
                            : "text-codey-text-dim hover:bg-codey-card-hover hover:text-codey-text"
                        }`}
                      >
                        <item.icon className="h-4 w-4 shrink-0" />
                        {item.label}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </nav>

            {user && (
              <div className="border-t border-codey-border px-4 py-4">
                <div className="flex items-center gap-2 px-3 text-sm text-codey-text-dim">
                  <Zap className="h-4 w-4 text-codey-green" />
                  <span className="font-medium text-codey-text">
                    {user.credits_remaining.toLocaleString()}
                  </span>
                  <span className="text-xs">credits</span>
                </div>
              </div>
            )}
          </aside>
        </div>
      )}

      {/* ── Main content area ─────────────────────────────────────────── */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Top bar */}
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-codey-border bg-codey-bg px-4 md:px-6">
          <div className="flex items-center gap-3">
            {/* Mobile hamburger */}
            <button
              onClick={() => setSidebarOpen(true)}
              className="rounded-md p-1.5 text-codey-text-dim hover:bg-codey-card lg:hidden"
            >
              <Menu className="h-5 w-5" />
            </button>

            {/* Breadcrumb */}
            <nav className="flex items-center gap-1.5 text-sm">
              {breadcrumb.map((crumb, i) => (
                <span key={crumb.href} className="flex items-center gap-1.5">
                  {i > 0 && (
                    <span className="text-codey-text-muted">/</span>
                  )}
                  {crumb.isLast ? (
                    <span className="font-medium text-codey-text">
                      {crumb.label}
                    </span>
                  ) : (
                    <Link
                      href={crumb.href}
                      className="text-codey-text-dim hover:text-codey-text transition-colors"
                    >
                      {crumb.label}
                    </Link>
                  )}
                </span>
              ))}
            </nav>
          </div>

          <div className="flex items-center gap-3">
            {/* Credit pill */}
            {user && (
              <Link
                href="/dashboard/credits"
                className="hidden items-center gap-1.5 rounded-full border border-codey-border bg-codey-card px-3 py-1 text-xs font-medium text-codey-text-dim transition-colors hover:border-codey-border-light hover:text-codey-text sm:flex"
              >
                <Zap className="h-3 w-3 text-codey-green" />
                {user.credits_remaining.toLocaleString()}
              </Link>
            )}

            {/* User avatar */}
            {user && (
              <div className="relative">
                <button
                  onClick={() => setUserMenuOpen(!userMenuOpen)}
                  className="flex h-8 w-8 items-center justify-center rounded-full bg-codey-green/20 text-xs font-semibold text-codey-green transition-colors hover:bg-codey-green/30"
                >
                  {user.email.charAt(0).toUpperCase()}
                </button>

                {userMenuOpen && (
                  <>
                    <div
                      className="fixed inset-0 z-40"
                      onClick={() => setUserMenuOpen(false)}
                    />
                    <div className="absolute right-0 top-full z-50 mt-1 w-52 animate-fade-in rounded-xl border border-codey-border bg-codey-card py-1.5 shadow-xl">
                      <div className="border-b border-codey-border px-4 py-2.5">
                        <p className="truncate text-sm font-medium text-codey-text">
                          {user.email}
                        </p>
                        <p className="text-xs text-codey-text-muted capitalize">
                          {user.plan} plan
                        </p>
                      </div>
                      <div className="py-1">
                        <Link
                          href="/dashboard"
                          onClick={() => setUserMenuOpen(false)}
                          className="flex items-center gap-3 px-4 py-2 text-sm text-codey-text-dim hover:bg-codey-card-hover hover:text-codey-text"
                        >
                          <BarChart3 className="h-4 w-4" />
                          Dashboard
                        </Link>
                        <Link
                          href="/settings"
                          onClick={() => setUserMenuOpen(false)}
                          className="flex items-center gap-3 px-4 py-2 text-sm text-codey-text-dim hover:bg-codey-card-hover hover:text-codey-text"
                        >
                          <Settings className="h-4 w-4" />
                          Settings
                        </Link>
                        <Link
                          href="/settings/billing"
                          onClick={() => setUserMenuOpen(false)}
                          className="flex items-center gap-3 px-4 py-2 text-sm text-codey-text-dim hover:bg-codey-card-hover hover:text-codey-text"
                        >
                          <CreditCard className="h-4 w-4" />
                          Billing
                        </Link>
                      </div>
                      <div className="border-t border-codey-border pt-1">
                        <button
                          onClick={() => {
                            setUserMenuOpen(false);
                            logout();
                          }}
                          className="flex w-full items-center gap-3 px-4 py-2 text-sm text-codey-red hover:bg-codey-card-hover"
                        >
                          Log out
                        </button>
                      </div>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto p-4 md:p-6">{children}</main>
      </div>
    </div>
  );
}
