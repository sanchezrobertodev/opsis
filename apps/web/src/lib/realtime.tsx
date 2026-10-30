"use client";

import { useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { API_URL, type Generation } from "./api";
import { keys, patchGeneration } from "./queries";

type Connection = "connecting" | "live" | "offline";
const RealtimeContext = createContext<Connection>("connecting");

export function useConnection() {
  return useContext(RealtimeContext);
}

type EventMessage = {
  id: number | null;
  topic: "generation" | "asset" | "pipeline" | "system";
  type: string;
  subject_id: string | null;
  payload: Record<string, unknown>;
};

/**
 * One EventSource for the whole app. Generation events patch cached lists in place (status, progress,
 * cost) so progress is smooth; anything that changes the set of items triggers a throttled refetch.
 */
export function RealtimeProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const [state, setState] = useState<Connection>("connecting");
  const pending = useRef<Set<string>>(new Set());
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const flush = () => {
      timer.current = null;
      for (const scope of pending.current) qc.invalidateQueries({ queryKey: [scope] });
      pending.current.clear();
    };
    const invalidate = (...scopes: string[]) => {
      scopes.forEach((s) => pending.current.add(s));
      if (!timer.current) timer.current = setTimeout(flush, 250);
    };

    const source = new EventSource(`${API_URL}/events/stream`);
    source.onopen = () => setState("live");
    source.onerror = () => setState(source.readyState === EventSource.CLOSED ? "offline" : "connecting");
    source.onmessage = (event) => {
      let msg: EventMessage;
      try {
        msg = JSON.parse(event.data);
      } catch {
        return;
      }
      if (msg.type === "generation.deleted") {
        // removed from history (maybe from another tab): its outputs left the library and a film's takes too
        invalidate("generations", "assets", "run", "takes");
      } else if (msg.topic === "generation" && msg.subject_id) {
        const p = msg.payload as Partial<Generation> & { generation_id?: string };
        const patch: Partial<Generation> = {
          status: p.status,
          status_reason: p.status_reason ?? null,
          progress: p.progress,
          estimated_cost_usd: p.estimated_cost_usd ?? null,
          actual_cost_usd: p.actual_cost_usd ?? null,
          error_code: p.error_code ?? null,
          error_message: p.error_message ?? null,
        };
        const known = patchGeneration(qc, msg.subject_id, patch);
        if (!known || msg.type === "generation.succeeded") invalidate("generations");
        if (msg.type === "generation.succeeded" || msg.type === "generation.failed") invalidate("budget");
        if (msg.type !== "generation.progress") invalidate("run");
      } else if (msg.topic === "pipeline") {
        // runs can give a character its picture (portraits, reference sheets)
        invalidate("runs", "run", "elements");
      } else if (msg.topic === "asset") {
        invalidate("assets", "generations", "asset");
      } else if (msg.type === "system.registry_reloaded") {
        qc.invalidateQueries({ queryKey: keys.registry });
        qc.invalidateQueries({ queryKey: ["model"] });
        toast("Model registry reloaded");
      } else if (msg.type === "system.registry_error") {
        toast.error("Registry change rejected. Keeping the last working version; check the API log.");
      }
    };
    return () => {
      source.close();
      if (timer.current) clearTimeout(timer.current);
    };
  }, [qc]);

  return <RealtimeContext.Provider value={state}>{children}</RealtimeContext.Provider>;
}
