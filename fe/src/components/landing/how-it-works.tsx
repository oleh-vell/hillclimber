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
    <pre className="m-0 h-[360px] overflow-y-auto whitespace-pre-wrap break-words px-6 py-[22px] font-mono text-[13.5px] font-medium leading-[1.85] text-paper/[0.9] [tab-size:2] select-text">
      <K>path_to_artefact</K>
      <Eq />
      <Str>&quot;my_repo/src/&quot;</Str>
      {"\n"}
      <K>strategy</K>
      <Eq />
      <Str>&quot;chain&quot;</Str>
      {"\n\n"}
      <Comment># Explicit target to climb</Comment>
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
      <Comment># Hard stop: max cycles, tokens or money.</Comment>
      {"\n"}
      <Table>[budget]</Table>
      {"\n"}
      <K>cycles</K>
      <Eq />
      <Num>5</Num>
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
      {"\n\n"}
      <Comment># kind = &quot;none&quot; to switch the sandbox off.</Comment>
      {"\n"}
      <Table>[sandbox]</Table>
      {"\n"}
      <K>kind</K>
      <Eq />
      <Str>&quot;seatbelt&quot;</Str>
      {"\n\n"}
      <Comment>
        # Proposes the next hypothesis for improving the artefact.
      </Comment>
      {"\n"}
      <Table>[agents.orchestrator]</Table>
      {"\n"}
      <K>harness</K>
      <Eq />
      <Str>&quot;claude&quot;</Str>
      {"\n"}
      <K>model</K>
      <Eq />
      <Str>&quot;claude-opus-4-8&quot;</Str>
      {"\n\n"}
      <Comment># Applies the proposed change to the artefact.</Comment>
      {"\n"}
      <Table>[agents.worker]</Table>
      {"\n"}
      <K>harness</K>
      <Eq />
      <Str>&quot;claude&quot;</Str>
      {"\n"}
      <K>model</K>
      <Eq />
      <Str>&quot;claude-opus-4-8&quot;</Str>
    </pre>
  );
}

/** Inline styles for the run-TUI transcript. */
const Mint = ({ children }: { children: ReactNode }) => (
  <span className="text-mint">{children}</span>
);
const Coral = ({ children }: { children: ReactNode }) => (
  <span className="text-coral">{children}</span>
);
const Sky = ({ children }: { children: ReactNode }) => (
  <span className="text-sky">{children}</span>
);
const Bold = ({ children }: { children: ReactNode }) => (
  <span className="font-semibold text-paper">{children}</span>
);
const Dim = ({ children }: { children: ReactNode }) => (
  <span className="text-paper/[0.4]">{children}</span>
);
const Hypo = ({ children }: { children: ReactNode }) => (
  <span className="italic text-paper/[0.6]">{children}</span>
);
const Trace = ({ children }: { children: ReactNode }) => (
  <span className="text-sky/[0.55]">{children}</span>
);

/**
 * A static frame of the `hillclimber run` dashboard. Mirrors the real TUI
 * (milestone history above a transient live region) with comment-style
 * annotations explaining each phase — the real thing redraws in place.
 */
function RunTui() {
  return (
    <div className="h-[360px] overflow-auto">
      <pre className="m-0 whitespace-pre px-6 py-[22px] font-mono text-[12.5px] font-medium leading-[1.85] text-paper/[0.9] select-text">
        <Mint>$</Mint> hillclimber run{"\n\n"}
        <Comment>
          # preflight — score the untouched artefact, check models
        </Comment>
        {"\n"}
        <Mint>✓</Mint> baseline <Bold>0.712</Bold>
        {"\n"}
        <Mint>✓</Mint> models verified{"\n"}
        <Mint>✓</Mint> strategy: <Bold>chain</Bold>
        {"\n\n"}
        <Comment>
          # each cycle: propose → apply → score, keep what climbs
        </Comment>
        {"\n"}
        <Sky>◆</Sky> <Bold>cycle 001:</Bold>{" "}
        <Hypo>Strip markup before matching field boundaries</Hypo>
        {"\n"}
        <Mint>▴</Mint> cycle 001 scored <Bold>0.781</Bold> <Mint>(+0.069)</Mint>
        {"\n"}
        <Sky>◆</Sky> <Bold>cycle 002:</Bold>{" "}
        <Hypo>Fuzzy-match malformed date fields</Hypo>
        {"\n"}
        <Coral>▾</Coral> cycle 002 scored <Bold>0.774</Bold>{" "}
        <Coral>(-0.007)</Coral>
        {"\n"}
        <Sky>◆</Sky> <Bold>cycle 003:</Bold>{" "}
        <Hypo>Normalize unicode before matching fields</Hypo>
        {"\n\n"}
        <Comment>
          # live status — redraws in place, gone when the run ends
        </Comment>
        {"\n"}
        <Sky>⠹</Sky> <Bold>cycle 3/5 — applying the hypothesis</Bold>
        {"           "}
        <Dim>12:47</Dim>
        {"\n"}
        {"  "}
        <Sky>baseline 0.712</Sky>
        <Dim>{"  ·  "}</Dim>
        <Mint>best 0.781</Mint>
        {"\n"}
        {"  "}
        <Dim>│</Dim> <Trace>Read(file_path=&apos;src/extract.py&apos;)</Trace>
        {"\n"}
        {"  "}
        <Dim>│</Dim>{" "}
        <Trace>Edit(file_path=&apos;src/extract.py&apos;, old_string=…)</Trace>
        {"\n"}
        {"  "}
        <Dim>│</Dim> <Dim>tool returned: ok</Dim>
      </pre>
    </div>
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
            <StepHeading
              numeral="01"
              kicker="Define"
              title="Write a spec file"
            />
            <div className="select-none overflow-hidden rounded-[14px] border border-white/10 bg-card shadow-[0_24px_60px_rgba(0,0,0,0.4)]">
              <WindowBar filename="hillclimber.toml" />
              <SpecToml />
            </div>
            <p className="mt-7 text-[16px] leading-[1.65] text-paper/[0.6]">
              The spec file defines the core of the long-running experiment.
              Define your goal, budget, and eval function to measure the
              improvement rate. <br />
              To generate the spec and eval files execute{" "}
              <code className="rounded-[5px] border border-white/10 bg-white/[0.06] px-[6px] py-[2px] font-mono text-[13px] text-mint">
                hillclimber init
              </code>
            </p>
          </div>

          {/* divider */}
          <span className="hidden self-stretch bg-white/10 md:block" />

          {/* 02 — Execute */}
          <div>
            <StepHeading
              numeral="02"
              kicker="Execute"
              title="Run hillclimber"
            />
            <div className="select-none overflow-hidden rounded-[14px] border border-white/10 bg-card shadow-[0_24px_60px_rgba(0,0,0,0.4)]">
              <WindowBar filename="~/my_repo" />
              <RunTui />
            </div>
            <p className="mt-7 text-[16px] leading-[1.65] text-paper/[0.6]">
              Hillclimber reads your spec and orchestrates the experiment. Each
              cycle is an isolated git worktee, with dedicated coding agent and
              tight feedback loop.
              <br /> To start climbing execute{" "}
              <code className="rounded-[5px] border border-white/10 bg-white/[0.06] px-[6px] py-[2px] font-mono text-[13px] text-mint">
                hillclimber run
              </code>
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
