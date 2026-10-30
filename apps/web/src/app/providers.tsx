"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { Toaster } from "sonner";
import { RealtimeProvider } from "@/lib/realtime";
import { PictureViewer } from "@/components/ui/PictureViewer";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 5_000 },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <RealtimeProvider>{children}</RealtimeProvider>
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
