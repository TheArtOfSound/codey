"use client";

import React from "react";

interface CardProps {
  children: React.ReactNode;
  interactive?: boolean;
  onClick?: () => void;
  className?: string;
}

export default function Card({
  children,
  interactive = false,
  onClick,
  className = "",
}: CardProps) {
  const classes = `card ${interactive ? "card-interactive" : ""} ${className}`;

  if (interactive) {
    return (
      <div className={classes} onClick={onClick} role="button" tabIndex={0}>
        {children}
      </div>
    );
  }

  return <div className={classes}>{children}</div>;
}
