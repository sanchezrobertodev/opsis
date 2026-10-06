"use client";

import { useEffect, useState } from "react";

/** `value`, settled for `ms`. Keyed by its JSON so a new-but-equal object doesn't restart the timer. */
export function useDebounced<T>(value: T, ms: number): T {
  const key = JSON.stringify(value);
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(JSON.parse(key) as T), ms);
    return () => clearTimeout(t);
  }, [key, ms]);
  return v;
}
