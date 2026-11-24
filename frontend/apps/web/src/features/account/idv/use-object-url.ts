"use client";

import { useEffect, useState } from "react";

/** A temporary address for a file held in memory, so it can be shown. It is released when the file changes or the component goes away; nothing is uploaded. */
export function useObjectUrl(file: File | null): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!file || typeof URL === "undefined" || typeof URL.createObjectURL !== "function") {
      setUrl(null);
      return;
    }
    const made = URL.createObjectURL(file);
    setUrl(made);
    return () => URL.revokeObjectURL(made);
  }, [file]);
  return url;
}
