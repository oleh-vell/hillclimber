"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

type DocsNavItem = {
  href: string;
  label: string;
};

/**
 * Sidebar navigation for the docs page with scroll-spy: the link for the
 * section currently in view is highlighted so readers can see where they are.
 */
export function DocsNav({ items }: { items: DocsNavItem[] }) {
  const [activeId, setActiveId] = useState(items[0]?.href.slice(1) ?? "");

  useEffect(() => {
    const ids = items.map((item) => item.href.slice(1));
    const sections = ids
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => el !== null);
    if (sections.length === 0) return;

    // Track which sections are on screen; highlight the topmost visible one so
    // the active link follows the reader as they scroll.
    const visible = new Set<string>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) visible.add(entry.target.id);
          else visible.delete(entry.target.id);
        }
        const topmost = ids.find((id) => visible.has(id));
        if (topmost) setActiveId(topmost);
      },
      { rootMargin: "-96px 0px -60% 0px" },
    );

    for (const section of sections) observer.observe(section);
    return () => observer.disconnect();
  }, [items]);

  return (
    <nav
      className="flex gap-2 overflow-x-auto border-b border-white/[0.08] pb-4 md:flex-col md:overflow-visible md:border-b-0 md:border-r md:pb-0 md:pr-6"
      aria-label="Documentation sections"
    >
      {items.map((item) => {
        const isActive = item.href.slice(1) === activeId;
        return (
          <a
            key={item.href}
            href={item.href}
            aria-current={isActive ? "true" : undefined}
            className={cn(
              "whitespace-nowrap rounded-md px-3 py-2 font-mono text-[13px] font-medium no-underline transition-colors",
              isActive
                ? "bg-white/[0.06] text-paper"
                : "text-paper/[0.58] hover:bg-white/[0.05] hover:text-paper",
            )}
          >
            {item.label}
          </a>
        );
      })}
    </nav>
  );
}
