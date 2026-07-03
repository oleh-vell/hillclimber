import { InstallWindow } from "./install-window";

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

        <div className="mt-10">
          <InstallWindow />
        </div>
      </div>
    </section>
  );
}
