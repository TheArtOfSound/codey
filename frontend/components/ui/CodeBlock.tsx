"use client";

import React, { useState } from "react";

interface CodeBlockProps {
  code: string;
  language?: string;
  filename?: string;
  showLineNumbers?: boolean;
  showCopy?: boolean;
  className?: string;
}

export default function CodeBlock({
  code,
  language,
  filename,
  showLineNumbers = false,
  showCopy = true,
  className = "",
}: CodeBlockProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const lines = code.split("\n");

  return (
    <div className={className} style={{ position: "relative" }}>
      {/* Header bar with filename/language and copy button */}
      {(filename || language || showCopy) && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "8px 16px",
            backgroundColor: "#0a1628",
            borderTopLeftRadius: 8,
            borderTopRightRadius: 8,
            border: "1px solid #1e3a5f",
            borderBottom: "none",
          }}
        >
          <span
            style={{
              fontSize: 12,
              color: "#475569",
              fontFamily: 'var(--font-jetbrains), "JetBrains Mono", Consolas, monospace',
            }}
          >
            {filename || language || ""}
          </span>
          {showCopy && (
            <button
              onClick={handleCopy}
              style={{
                background: "none",
                border: "none",
                color: copied ? "#22c55e" : "#475569",
                fontSize: 12,
                cursor: "pointer",
                fontFamily: 'var(--font-jetbrains), "JetBrains Mono", Consolas, monospace',
                padding: 0,
                transition: "color 150ms ease",
              }}
            >
              {copied ? "Copied" : "Copy"}
            </button>
          )}
        </div>
      )}

      {/* Code content */}
      <pre
        className="code-block"
        style={{
          margin: 0,
          borderTopLeftRadius: filename || language || showCopy ? 0 : undefined,
          borderTopRightRadius: filename || language || showCopy ? 0 : undefined,
        }}
      >
        <code>
          {lines.map((line, i) => (
            <React.Fragment key={i}>
              {showLineNumbers && (
                <span className="code-line-number">{i + 1}</span>
              )}
              {line}
              {i < lines.length - 1 ? "\n" : ""}
            </React.Fragment>
          ))}
        </code>
      </pre>
    </div>
  );
}
