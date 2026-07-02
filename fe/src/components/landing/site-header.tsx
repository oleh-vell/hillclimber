import { Button } from "@/components/ui/button";
import { GITHUB_URL } from "@/lib/site";
import { LinkIcon } from "./icons";

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-50 flex items-center justify-between border-b border-white/[0.08] bg-ink/[0.72] px-7 py-4 backdrop-blur-[10px]">
      <a
        href="#hero"
        className="font-display text-[18px] font-bold tracking-[0.04em] text-paper no-underline"
      >
        HILLCLIMBER
      </a>
      <Button
        variant="outline"
        nativeButton={false}
        render={
          <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer" />
        }
        className="h-[38px] gap-[9px] rounded-full border-white/[0.16] bg-white/[0.02] px-4 font-mono text-[12px] font-semibold tracking-[0.06em] text-paper transition-colors hover:border-mint/50 hover:bg-mint/[0.06] hover:text-paper"
      >
        <LinkIcon strokeWidth={1.8} className="size-[15px]" />
        GitHub
      </Button>
    </header>
  );
}
