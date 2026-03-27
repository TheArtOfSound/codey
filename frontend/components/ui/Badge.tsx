"use client";

import React from "react";

interface BadgeProps {
  variant: "healthy" | "warning" | "error" | "neutral";
  children: React.ReactNode;
  className?: string;
}

const variantClass: Record<string, string> = {
  healthy: "badge-healthy",
  warning: "badge-warning",
  error: "badge-error",
  neutral: "badge-neutral",
};

export default function Badge({ variant, children, className = "" }: BadgeProps) {
  return (
    <span className={`${variantClass[variant]} ${className}`}>
      {children}
    </span>
  );
}
