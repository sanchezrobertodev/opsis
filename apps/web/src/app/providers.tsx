"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { Toaster } from "sonner";
import { ApiError } from "@/lib/api";
import { RealtimeProvider } from "@/lib/realtime";
import { AuthGate } from "@/components/shell/SignIn";
import { PictureViewer } from "@/components/ui/PictureViewer";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // a refusal for want of the access token brings the sign-in back instead (shell/SignIn.tsx)
            retry: (n, err) => n < 1 && !(err instanceof ApiError && err.status === 401),
            refetchOnWindowFocus: false,
            staleTime: 5_000,
          },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <AuthGate>
        <RealtimeProvider>{children}</RealtimeProvider>
      </AuthGate>
      <PictureViewer />
      <Toaster
        theme="dark"
        position="bottom-right"
        toastOptions={{
          style: {
            background: "var(--color-raised)",
            border: "1px solid var(--color-line)",
            color: "var(--color-chalk)",
            fontFamily: "var(--font-sans)",
          },
        }}
      />
    </QueryClientProvider>
  );
}
