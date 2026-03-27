"use client";

import DashboardLayout from "@/components/layout/DashboardLayout";
import { ProtectedRoute } from "@/lib/auth";

export default function DashboardRootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <ProtectedRoute>
      <DashboardLayout>{children}</DashboardLayout>
    </ProtectedRoute>
  );
}
