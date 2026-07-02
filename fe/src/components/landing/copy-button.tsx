"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

type CopyButtonProps = {
  value: string;
  className?: string;
  idleLabel?: string;
  copiedLabel?: string;
};

/**
 * Copy-to-clipboard trigger. Mirrors the design's inline copy affordance:
 * the label flips to "Copied" for ~1.6s after a successful copy.
 */
export function CopyButton({
  value,
  className,
  idleLabel = "Copy",
  copiedLabel = "Copied",
}: CopyButtonProps) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard?.writeText(value);
    } catch {
      // Clipboard can be unavailable (insecure context) — fail quietly.
    }
    setCopied(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 1600);
  }, [value]);

  return (
    <button
      type="button"
      onClick={onCopy}
      aria-label={`Copy: ${value}`}
      className={cn("cursor-pointer border-none bg-transparent p-0", className)}
    >
      {copied ? copiedLabel : idleLabel}
    </button>
  );
}
