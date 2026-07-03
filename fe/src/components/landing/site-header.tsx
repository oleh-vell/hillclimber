import Link from "next/link";
import { Button } from "@/components/ui/button";
import { GITHUB_URL } from "@/lib/site";
import { ArrowUpRightIcon } from "./icons";

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-50 flex items-center justify-between border-b border-white/[0.08] bg-ink/[0.72] px-7 py-4 backdrop-blur-[10px]">
      <Link
        href="/#hero"
        className="font-display text-[18px] font-bold tracking-[0.04em] text-paper no-underline"
      >
        HILLCLIMBER
      </Link>
      <nav className="flex items-center gap-2" aria-label="Main navigation">
        <Button
          variant="link"
          nativeButton={false}
          render={<Link href="/docs" />}
          className="h-[38px] px-3 font-mono text-[12px] font-semibold tracking-[0.06em] text-paper/[0.72] no-underline underline-offset-[6px] transition-colors hover:text-paper hover:underline"
        >
          Docs
        </Button>
        <Button
          variant="outline"
          nativeButton={false}
          render={
            <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer" />
          }
          className="h-[38px] gap-[7px] border-white/[0.16] bg-white/[0.02] px-4 font-mono text-[12px] font-semibold tracking-[0.06em] text-paper transition-colors hover:border-mint/50 hover:bg-mint/[0.06] hover:text-paper"
        >
          GitHub
          <ArrowUpRightIcon strokeWidth={1.8} className="size-[14px]" />
        </Button>
      </nav>
    </header>
  );
}
