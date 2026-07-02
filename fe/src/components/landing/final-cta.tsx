import { Button } from "@/components/ui/button";
import { GITHUB_URL, INSTALL_CMD } from "@/lib/site";
import { CopyButton } from "./copy-button";
import { GithubMark } from "./icons";

export function FinalCta() {
  return (
    <section
      id="final-cta"
      className="relative overflow-hidden border-t border-white/[0.08] bg-ink px-6 py-[140px] md:px-10"
    >
      {/* radial glow */}
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(60%_70%_at_50%_30%,rgba(94,234,160,0.10)_0%,transparent_70%)]" />

      <div className="relative mx-auto flex max-w-[680px] flex-col items-center text-center">
        <div className="mb-[22px] font-mono text-[12px] font-semibold uppercase leading-none tracking-[0.2em] text-mint">
          Start climbing
        </div>
        <h2 className="m-0 font-display text-[clamp(38px,6vw,72px)] font-bold leading-[0.96] tracking-[0.005em] text-paper">
          Point it at your repo.
        </h2>
        <p className="mt-6 max-w-[440px] text-[17px] leading-[1.6] text-paper/[0.55]">
          Install the CLI, write a spec, and let hillclimber do the climbing. Free
          and open-source.
        </p>

        <div className="mt-10 flex flex-wrap items-center justify-center gap-4">
          {/* install command pill */}
          <div className="flex h-[52px] items-center rounded-full border border-white/[0.14] bg-white/[0.018] pl-[22px] font-mono">
            <span className="text-[14px] text-mint">$</span>
            <span className="ml-3 text-[14px] text-paper/[0.92]">
              {INSTALL_CMD}
            </span>
            <CopyButton
              value={INSTALL_CMD}
              className="ml-[18px] h-full rounded-r-full border-l border-white/[0.12] px-[22px] font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.1em] text-paper/[0.62] transition-colors hover:bg-white/5 hover:text-white"
            />
          </div>

          {/* primary GitHub CTA */}
          <Button
            nativeButton={false}
            render={<a href={GITHUB_URL} target="_blank" rel="noopener noreferrer" />}
            className="h-[52px] gap-[10px] rounded-full bg-mint px-[26px] font-mono text-[14px] font-semibold tracking-[0.02em] text-ink transition-opacity hover:bg-mint hover:opacity-85"
          >
            <GithubMark className="size-[18px]" />
            View on GitHub
          </Button>
        </div>
      </div>
    </section>
  );
}
