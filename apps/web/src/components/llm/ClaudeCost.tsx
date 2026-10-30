"use client";

import { usd } from "@/lib/cn";
import { useClaudeCalls } from "@/lib/queries";

/** The Claude model behind a button and what its calls typically cost, from the server's token prices. `purposes`
 * are the spend ledger's ("enhance", "cinema.plan") and add up (an episode's script and plan). `text` reads
 * "Claude Sonnet 5.5, about $0.011", or in mock mode "Claude Sonnet 5.5, free now, about $0.011 when live". */
export function useClaudeCost(...purposes: string[]): { name: string; price: string; text: string } {
  const { data } = useClaudeCalls();
  const calls = purposes.flatMap((p) => data?.calls[p] ?? []);
  if (!data || !calls.length) return { name: "Claude", price: "", text: "Claude" };
  const name = [...new Set(calls.map((c) => c.name))].join(" and ");
  const total = usd(calls.reduce((n, c) => n + c.usd, 0));
  const price = data.mode === "mock" ? `free now, about ${total} when live` : `about ${total}`;
  return { name, price, text: `${name}, ${price}` };
}
