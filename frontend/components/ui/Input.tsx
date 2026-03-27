"use client";

import React from "react";

interface InputProps {
  label?: string;
  error?: string;
  disabled?: boolean;
  placeholder?: string;
  value?: string;
  onChange?: (e: React.ChangeEvent<HTMLInputElement>) => void;
  type?: string;
  name?: string;
  className?: string;
  id?: string;
}

export default function Input({
  label,
  error,
  disabled = false,
  placeholder,
  value,
  onChange,
  type = "text",
  name,
  className = "",
  id,
}: InputProps) {
  const inputId = id || name || label?.toLowerCase().replace(/\s+/g, "-");

  return (
    <div className={className} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {label && (
        <label
          htmlFor={inputId}
          style={{
            fontSize: 14,
            fontWeight: 500,
            color: "#94a3b8",
          }}
        >
          {label}
        </label>
      )}
      <input
        id={inputId}
        type={type}
        name={name}
        disabled={disabled}
        placeholder={placeholder}
        value={value}
        onChange={onChange}
        className={`input ${error ? "input-error" : ""}`}
      />
      {error && (
        <span style={{ fontSize: 12, color: "#ef4444" }}>
          {error}
        </span>
      )}
    </div>
  );
}
