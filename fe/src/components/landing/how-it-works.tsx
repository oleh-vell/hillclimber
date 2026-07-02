import type { ReactNode } from "react";

/** Faux window chrome (traffic-light dots + filename). */
function WindowBar({ filename }: { filename: string }) {
  return (
    <div className="flex items-center gap-[7px] border-b border-white/[0.07] px-[15px] py-[11px]">
      <span className="size-[11px] rounded-full bg-coral" />
      <span className="size-[11px] rounded-full bg-amber-soft" />
      <span className="size-[11px] rounded-full bg-mint" />
      <span className="ml-2 font-mono text-[12px] font-medium text-paper/[0.45]">
        {filename}
      </span>
    </div>
  );
}

function StepHeading({
  numeral,
  kicker,
  title,
}: {
  numeral: string;
  kicker: string;
  title: string;
}) {
  return (
    <div className="mb-[26px] flex items-start gap-[18px]">
      <span className="font-display text-[64px] leading-[0.8] text-mint/[0.85]">
        {numeral}
      </span>
      <div className="pt-[6px]">
        <div className="mb-[10px] font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.18em] text-paper/[0.4]">
          {kicker}
        </div>
        <h3 className="m-0 text-[25px] font-semibold tracking-[-0.01em] text-paper">
          {title}
        </h3>
      </div>
    </div>
  );
}

/** One highlighted line of the spec.toml sample. */
const K = ({ children }: { children: ReactNode }) => (
  <span className="text-paper/[0.9]">{children}</span>
);
const Eq = () => <span className="text-paper/[0.35]"> = </span>;
const Str = ({ children }: { children: ReactNode }) => (
  <span className="text-mint">{children}</span>
);
const Num = ({ children }: { children: ReactNode }) => (
  <span className="text-amber">{children}</span>
);
const Comment = ({ children }: { children: ReactNode }) => (
  <span className="italic text-paper/[0.32]">{children}</span>
);
const Table = ({ children }: { children: ReactNode }) => (
  <span className="text-sky">{children}</span>
);

function SpecToml() {
  return (
    <pre className="m-0 whitespace-pre-wrap break-words px-6 py-[22px] font-mono text-[13.5px] font-medium leading-[1.85] text-paper/[0.9] [tab-size:2]">
      <K>path_to_artefact</K>
      <Eq />
      <Str>&quot;my_repo/src/&quot;</Str>
      {"\n"}
      <K>strategy</K>
      <Eq />
      <Str>&quot;chain&quot;</Str>
      {"\n\n"}
      <Comment>
        # Climb toward a perfect extraction score; stop early if we reach it.
      </Comment>
      {"\n"}
      <Table>[goal]</Table>
      {"\n"}
      <K>direction</K>
      <Eq />
      <Str>&quot;maximize&quot;</Str>
      {"\n"}
      <K>target</K>
      <Eq />
      <Num>1.0</Num>
      {"\n\n"}
      <Comment># Hard stop: number of cycles to attempt.</Comment>
      {"\n"}
      <Table>[budget]</Table>
      {"\n"}
      <K>cycles</K>
      <Eq />
      <Num>1</Num>
      {"\n\n"}
      <Comment># Eval function</Comment>
      {"\n"}
      <Table>[scorer]</Table>
      {"\n"}
      <K>kind</K>
      <Eq />
      <Str>&quot;command&quot;</Str>
      {"\n"}
      <K>cmd</K>
      <Eq />
      <Str>&quot;python eval.py&quot;</Str>
    </pre>
  );
}

export function HowItWorks() {
  return (
    <section
      id="how-it-works"
      className="relative border-t border-white/[0.08] bg-panel px-6 py-[120px] md:px-10"
    >
      <div className="mx-auto max-w-[1180px]">
        <div className="mb-[60px] text-center">
          <div className="font-mono text-[12px] font-semibold uppercase leading-none tracking-[0.2em] text-mint">
            How it works
          </div>
        </div>

        <div className="grid grid-cols-1 items-start gap-14 md:grid-cols-[1fr_1px_1fr] md:gap-[56px]">
          {/* 01 — Define */}
          <div>
            <StepHeading numeral="01" kicker="Define" title="Write a spec file" />
            <div className="select-none overflow-hidden rounded-[14px] border border-white/10 bg-card shadow-[0_24px_60px_rgba(0,0,0,0.4)]">
              <WindowBar filename="spec.toml" />
              <SpecToml />
            </div>
            <p className="mt-7 text-[16px] leading-[1.65] text-paper/[0.6]">
              A spec is a plain-text declaration of intent — your goal, the metric
              to optimize, the budget, and which models to use. No glue code, no
              orchestration. Just describe the hill you want to climb.
            </p>
          </div>

          {/* divider */}
          <span className="hidden self-stretch bg-white/10 md:block" />

          {/* 02 — Execute */}
          <div>
            <StepHeading numeral="02" kicker="Execute" title="Run hillclimber" />
            <p className="mb-7 text-[16px] leading-[1.65] text-paper/[0.6]">
              hillclimber reads your spec and takes over — generating candidate
              changes, running each in an isolated git branch, scoring it against
              your metric, and keeping only what improves. Watch it climb in real
              time.
            </p>
            <div className="flex aspect-[16/11] w-full items-center justify-center rounded-[14px] border border-white/10 bg-card shadow-[0_24px_60px_rgba(0,0,0,0.4)]">
              <span className="font-mono text-[13px] text-paper/[0.3]">
                Drop run / TUI screenshot
              </span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
