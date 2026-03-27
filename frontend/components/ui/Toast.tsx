"use client";

import React, { useEffect, useState } from "react";

type ToastVariant = "success" | "error" | "info" | "warning";

interface ToastItem {
  id: string;
  message: string;
  variant: ToastVariant;
}

interface ToastProps {
  toasts: ToastItem[];
  onDismiss: (id: string) => void;
}

const variantClass: Record<ToastVariant, string> = {
  success: "toast-item toast-success",
  error: "toast-item toast-error",
  info: "toast-item toast-info",
  warning: "toast-item toast-warning",
};

function ToastEntry({
  toast,
  onDismiss,
}: {
  toast: ToastItem;
  onDismiss: (id: string) => void;
}) {
  useEffect(() => {
    const timer = setTimeout(() => onDismiss(toast.id), 4000);
    return () => clearTimeout(timer);
  }, [toast.id, onDismiss]);

  return (
    <div className={variantClass[toast.variant]}>
      {toast.message}
    </div>
  );
}

export default function Toast({ toasts, onDismiss }: ToastProps) {
  if (toasts.length === 0) return null;

  return (
    <div className="toast">
      {toasts.map((t) => (
        <ToastEntry key={t.id} toast={t} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

// Hook for easy toast management
export function useToast() {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const addToast = (message: string, variant: ToastVariant = "info") => {
    const id = Date.now().toString(36) + Math.random().toString(36).slice(2);
    setToasts((prev) => [...prev, { id, message, variant }]);
  };

  const dismissToast = (id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  };

  return { toasts, addToast, dismissToast };
}
