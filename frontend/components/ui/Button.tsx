"use client";

import React from "react";

interface ButtonProps {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
  disabled?: boolean;
  children: React.ReactNode;
  onClick?: () => void;
  className?: string;
  type?: "button" | "submit";
}

const sizeStyles: Record<string, React.CSSProperties> = {
  sm: { fontSize: 12, padding: "4px 10px" },
  md: { fontSize: 14, padding: "8px 16px" },
  lg: { fontSize: 16, padding: "12px 24px" },
};

const variantClass: Record<string, string> = {
  primary: "btn-primary",
  secondary: "btn-secondary",
  ghost: "btn-ghost",
  danger: "btn-danger",
};

export default function Button({
  variant = "primary",
  size = "md",
  disabled = false,
  children,
  onClick,
  className = "",
  type = "button",
}: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className={`${variantClass[variant]} ${className}`}
      style={size !== "md" ? sizeStyles[size] : undefined}
    >
      {children}
    </button>
  );
}
