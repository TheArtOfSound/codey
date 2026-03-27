"use client";

import React from "react";

type HealthStatus = "healthy" | "watch" | "risk";

interface HealthIndicatorProps {
  status: HealthStatus;
  className?: string;
}

const statusConfig: Record<HealthStatus, { dotClass: string; label: string }> = {
  healthy: {
    dotClass: "health-dot health-dot-healthy",
    label: "Healthy",
  },
  watch: {
    dotClass: "health-dot health-dot-watch",
    label: "Watch",
  },
  risk: {
    dotClass: "health-dot health-dot-risk",
    label: "Critical",
  },
};

export default function HealthIndicator({ status, className = "" }: HealthIndicatorProps) {
  const config = statusConfig[status];

  return (
    <div
      className={className}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
      }}
    >
      <span className={config.dotClass} />
      <span
        style={{
          fontSize: 13,
          fontWeight: 500,
          color: "#94a3b8",
        }}
      >
        {config.label}
      </span>
    </div>
  );
}
