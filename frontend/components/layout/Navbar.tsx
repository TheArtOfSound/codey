"use client";

import { useState, useRef, useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth";
import {
  Zap,
  ChevronDown,
  Settings,
  CreditCard,
  BarChart3,
  Code,
} from "lucide-react";

// ── Navbar ───────────────────────────────────────────────────────────────────

export default function Navbar() {
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node)
      ) {
        setDropdownOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const navLinks = user
    ? [
        { href: "/dashboard", label: "Dashboard" },
        { href: "/dashboard/prompt", label: "Prompt" },
        { href: "/pricing", label: "Pricing" },
      ]
    : [];

  return (
    <nav className="sticky top-0 z-50 flex h-14 items-center justify-between border-b border-codey-border bg-codey-bg/80 px-6 backdrop-blur-lg md:px-8">
      {/* Left: Logo */}
      <Link
        href={user ? "/dashboard" : "/"}
        className="text-lg font-bold tracking-tight"
      >
        <span className="text-codey-green">C</span>ODEY
      </Link>

      {/* Center: Nav links */}
      {navLinks.length > 0 && (
        <div className="hidden items-center gap-1 md:flex">
          {navLinks.map((link) => {
            const active = pathname.startsWith(link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
                  active
                    ? "bg-codey-card text-codey-text"
                    : "text-codey-text-dim hover:bg-codey-card hover:text-codey-text"
                }`}
              >
                {link.label}
              </Link>
            );
          })}
        </div>
      )}

      {/* Right */}
      <div className="flex items-center gap-3">
        {user ? (
          <>
            {/* Credit pill */}
            <Link
              href="/dashboard/credits"
              className="flex items-center gap-1.5 rounded-full border border-codey-border bg-codey-card px-3 py-1 text-xs font-medium text-codey-text-dim transition-colors hover:border-codey-border-light hover:text-codey-text"
            >
              <Zap className="h-3 w-3 text-codey-green" />
              {user.credits_remaining.toLocaleString()}
            </Link>

            {/* User dropdown */}
            <div className="relative" ref={dropdownRef}>
              <button
                onClick={() => setDropdownOpen(!dropdownOpen)}
                className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm transition-colors hover:bg-codey-card"
              >
                <div className="flex h-7 w-7 items-center justify-center rounded-full bg-codey-green/20 text-xs font-semibold text-codey-green">
                  {user.email.charAt(0).toUpperCase()}
                </div>
                <ChevronDown
                  className={`h-3.5 w-3.5 text-codey-text-dim transition-transform ${
                    dropdownOpen ? "rotate-180" : ""
                  }`}
                />
              </button>

              {dropdownOpen && (
                <div className="absolute right-0 top-full mt-1 w-56 animate-fade-in rounded-xl border border-codey-border bg-codey-card py-1.5 shadow-xl">
                  <div className="border-b border-codey-border px-4 py-2.5">
                    <p className="truncate text-sm font-medium text-codey-text">
                      {user.email}
                    </p>
                    <p className="text-xs text-codey-text-muted capitalize">
                      {user.plan} plan
                    </p>
                  </div>

                  <div className="py-1">
                    <DropdownLink
                      href="/dashboard"
                      icon={BarChart3}
                      label="Dashboard"
                      onClick={() => setDropdownOpen(false)}
                    />
                    <DropdownLink
                      href="/settings"
                      icon={Settings}
                      label="Settings"
                      onClick={() => setDropdownOpen(false)}
                    />
                    <DropdownLink
                      href="/settings/billing"
                      icon={CreditCard}
                      label="Billing"
                      onClick={() => setDropdownOpen(false)}
                    />
                  </div>

                  <div className="border-t border-codey-border pt-1">
                    <button
                      onClick={() => {
                        setDropdownOpen(false);
                        logout();
                      }}
                      className="flex w-full items-center gap-3 px-4 py-2 text-sm text-codey-red hover:bg-codey-card-hover"
                    >
                      Log out
                    </button>
                  </div>
                </div>
              )}
            </div>
          </>
        ) : (
          <>
            <Link href="/auth/login" className="btn-ghost text-sm">
              Log in
            </Link>
            <Link href="/auth/signup" className="btn-primary text-sm">
              Sign up
            </Link>
          </>
        )}
      </div>
    </nav>
  );
}

// ── Dropdown Link ────────────────────────────────────────────────────────────

function DropdownLink({
  href,
  icon: Icon,
  label,
  onClick,
}: {
  href: string;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  onClick?: () => void;
}) {
  return (
    <Link
      href={href}
      onClick={onClick}
      className="flex items-center gap-3 px-4 py-2 text-sm text-codey-text-dim hover:bg-codey-card-hover hover:text-codey-text"
    >
      <Icon className="h-4 w-4" />
      {label}
    </Link>
  );
}
