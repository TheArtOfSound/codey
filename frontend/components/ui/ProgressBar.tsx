"use client";

import React from "react";

interface ProgressBarProps {
  value: number; // 0-100
  className?: string;
}

export default function ProgressBar({ value, className = "" }: ProgressBarProps) {
  const clamped = Math.min(100, Math.max(0, value));

  return (
    <div className={`progress-track ${className}`}>
      <div
        className="progress-fill"
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}
