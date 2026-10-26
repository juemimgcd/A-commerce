"use client";

import { useSyncExternalStore } from "react";
import { z } from "zod";

const subscribe = (callback: () => void) => {
  window.addEventListener("storage", callback);
  window.addEventListener("commerce-storage", callback);
  return () => {
    window.removeEventListener("storage", callback);
    window.removeEventListener("commerce-storage", callback);
  };
};
export function useStored<T>(key: string, schema: z.ZodType<T>) {
  const raw = useSyncExternalStore(
    subscribe,
    () => {
      try {
        return localStorage.getItem(key);
      } catch {
        return null;
      }
    },
    () => null,
  );
  let value: T | undefined;
  try {
    const result = schema.safeParse(raw ? JSON.parse(raw) : undefined);
    if (result.success) value = result.data;
  } catch {
    /* Ignore malformed local drafts. */
  }
  function save(next?: T) {
    if (next === undefined) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(next));
    window.dispatchEvent(new Event("commerce-storage"));
  }
  return [value, save] as const;
}
