import { InstallWindow } from "./install-window";

export function FinalCta() {
  return (
    <section
      id="final-cta"
      className="relative bg-ink px-6 py-[110px] md:px-10"
    >
      {/* seam-to-headline wash — one soft vertical glow feathering across the
          boundary (no hard clip) and pooling behind the wordmark */}
      <div className="pointer-events-none absolute inset-x-0 -top-28 h-[640px] bg-[radial-gradient(60%_70%_at_50%_40%,rgba(94,234,160,0.11)_0%,transparent_72%)]" />

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
