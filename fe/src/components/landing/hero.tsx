import { InstallWindow } from "./install-window";

export function Hero() {
  return (
    <section
      id="hero"
      className="relative flex min-h-screen flex-col items-center justify-center overflow-hidden bg-ink px-6 py-24"
    >
      {/* eyebrow */}
      <div className="mb-[30px] flex items-center gap-[14px]">
        <span className="h-px w-10 bg-white/[0.16]" />
        <span className="font-mono text-[12px] font-semibold uppercase leading-none tracking-[0.24em] text-mint">
          V0.0.1 (pre-release)
        </span>
        <span className="h-px w-10 bg-white/[0.16]" />
      </div>

      <h1 className="m-0 whitespace-nowrap text-center font-display text-[clamp(38px,12.3vw,168px)] font-bold leading-[0.92] tracking-[0.01em] text-paper md:text-[clamp(58px,13vw,168px)]">
        HILLCLI
        <span className="relative inline-block">
          M
          {/* climbing route up the right side of the M's V, onto the right stem top (Gameplay font), ink-box coords 142x163 */}
          <svg
            aria-hidden
            className="hc-route"
            viewBox="0 0 142 163"
            fill="none"
          >
            <path
              pathLength={1}
              d="M69 74 H74 V53 H84 V32 H95 V11 H105 V0 H142"
            />
          </svg>
        </span>
        BER
      </h1>

      <div className="mt-9 w-[min(560px,92vw)] text-left">
        <p className="m-0 whitespace-nowrap font-mono text-[clamp(15px,2.1vw,23px)] font-bold leading-[1.4] tracking-[-0.01em] text-paper">
          An open-source /goal alternative.
        </p>
        <p className="mt-[14px] font-mono text-[clamp(13px,1.3vw,15px)] font-normal leading-[1.65] text-paper/[0.5]">
          Auto-improve your code. Define your goal, budget, and
          models—hillclimber orchestrates, executes, and monitors the work.{" "}
          <br />
          Open-source and harness-agnostic.
        </p>
      </div>

      {/* install command */}
      <div className="mt-[34px]">
        <InstallWindow />
      </div>
    </section>
  );
}
