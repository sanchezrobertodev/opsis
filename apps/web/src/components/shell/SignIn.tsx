"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import type { Schemas } from "@opsis/api-client";
import { api, ApiError, SIGNED_OUT, unwrap } from "@/lib/api";
import { keys } from "@/lib/queries";
import { Button } from "@/components/ui/controls";

const session = () => unwrap(api.GET("/auth/session"));
const out = (s: Schemas["AuthStateOut"] | undefined) => Boolean(s?.required && !s.signed_in);

/** False while the studio waits for this browser to sign in again: work that saves itself holds off until then. */
export function useSignedIn(): boolean {
  return !out(useQuery({ queryKey: keys.auth, queryFn: session, staleTime: Infinity }).data);
}

/**
 * The studio when the API asks for its access token (ACCESS_TOKEN) and this browser hasn't given it: a sign-in instead
 * of every page. Until the API says so (or while it can't be reached) the pages show as they do without a token. Once
 * shown, they stay mounted under the sign-in, so a token that changes mid-edit loses nothing unsaved.
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const { data, isError } = useQuery({ queryKey: keys.auth, queryFn: session, staleTime: Infinity });
  const signedOut = out(data);
  const [shown, setShown] = useState(false);
  if (!shown && (data ? !signedOut : isError)) setShown(true);
  useEffect(() => {
    // a request was refused: the token changed, or this browser signed out in another tab
    const ask = () => {
      if (out(qc.getQueryData(keys.auth))) return; // already asking: pages still polling behind it would repeat it
      void qc.invalidateQueries({ queryKey: keys.auth }, { cancelRefetch: false });
    };
    window.addEventListener(SIGNED_OUT, ask);
    return () => window.removeEventListener(SIGNED_OUT, ask);
  }, [qc]);
  return (
    <>
      {(shown || !signedOut) && <div className="contents" inert={signedOut}>{children}</div>}
      {signedOut && <SignIn />}
    </>
  );
}

function SignIn() {
  const qc = useQueryClient();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [dropped, setDropped] = useState(false);

  const signIn = async () => {
    setBusy(true);
    try {
      await unwrap(api.POST("/auth/session", { body: { token } }));
      // the cookie only comes back if the browser kept it: an API on another site (localhost vs 127.0.0.1) can't set one
      const now = await session();
      if (!now.signed_in) return setDropped(true);
      qc.setQueryData(keys.auth, now);
      void qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== keys.auth[0] });
    } catch (err) {
      const wrong = err instanceof ApiError && err.status === 401;
      toast.error(wrong ? "That isn't the studio's access token" : err instanceof Error ? err.message : "Could not sign in");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-bay/85 px-4 backdrop-blur-sm">
      <form
        className="w-full max-w-sm space-y-5 rounded-[var(--radius-panel)] border border-line-soft bg-raised/80 p-6"
        onSubmit={(e) => {
          e.preventDefault();
          if (token.trim()) void signIn();
        }}
      >
        <div className="flex items-center gap-2.5">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/opsis.webp" alt="" width={26} height={26} className="size-[26px]" />
          <h1 className="type-title text-xl">Opsis</h1>
        </div>
        <div className="space-y-2">
          <label htmlFor="access-token" className="text-xs text-muted">
            Access token
          </label>
          <input
            id="access-token"
            type="password"
            autoFocus
            autoComplete="current-password"
            spellCheck={false}
            value={token}
            onChange={(e) => setToken(e.target.value)}
            className="h-10 w-full rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm outline-none focus:border-tungsten/60"
          />
          <p className="text-2xs leading-relaxed text-faint">
            The studio&rsquo;s ACCESS_TOKEN, from its .env. This browser stays signed in until it&rsquo;s changed.
          </p>
        </div>
        {dropped && (
          <p role="alert" className="text-xs leading-relaxed text-fault">
            The token is right, but this browser didn&rsquo;t keep the sign-in. Open the studio on the same host name
            as its API (for example both on localhost, not localhost and 127.0.0.1).
          </p>
        )}
        <Button type="submit" tone="primary" className="w-full" disabled={busy || !token.trim()}>
          Sign in
        </Button>
      </form>
    </div>
  );
}

/** Settings' sign-out, shown only when the studio asks for a token. */
export function SignOut() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: keys.auth, queryFn: session, staleTime: Infinity });
  if (!data?.required) return null;
  const signOut = async () => {
    try {
      const now = await unwrap(api.DELETE("/auth/session"));
      qc.setQueryData(keys.auth, now); // the gate shows the sign-in, so nothing watches the rest any more
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== keys.auth[0] });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not sign out");
    }
  };
  return (
    <section className="flex flex-wrap items-center justify-between gap-3 lg:col-span-2" aria-label="Access">
      <div>
        <h2 className="type-title text-xl">Access</h2>
        <p className="mt-1 max-w-2xl text-xs leading-relaxed text-muted">
          The studio asks for its access token. This browser is signed in.
        </p>
      </div>
      <Button tone="quiet" size="sm" onClick={() => void signOut()}>
        Sign out
      </Button>
    </section>
  );
}
