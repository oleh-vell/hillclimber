import type { Metadata } from "next";
import { CopyButton } from "@/components/landing/copy-button";
import { DocsNav } from "@/components/landing/docs-nav";
import { SiteFooter } from "@/components/landing/site-footer";
import { SiteHeader } from "@/components/landing/site-header";
import { AUTHOR_URL } from "@/lib/site";

function CodeBlock({ children }: { children: string }) {
  return (
    <div className="mt-4 overflow-hidden rounded-lg border border-white/[0.1] bg-card">
      <div className="flex items-center justify-between border-b border-white/[0.07] px-4 py-2 font-mono text-[11px] font-medium tracking-[0.08em] text-paper/[0.42]">
        bash
        <CopyButton
          value={children}
          className="font-semibold uppercase text-mint transition-opacity hover:opacity-70"
        />
      </div>
      <pre className="m-0 overflow-x-auto p-4 font-mono text-[13px] leading-[1.7] text-paper/[0.82]">
        <code>{children}</code>
      </pre>
    </div>
  );
}

export const metadata: Metadata = {
  title: "Docs | Hillclimber",
  description:
    "Beta documentation for Hillclimber: overview, getting started, and key concepts.",
};

const navItems = [
  { href: "#overview", label: "Overview" },
  { href: "#getting-started", label: "Getting started" },
  { href: "#key-concepts", label: "Key concepts" },
  { href: "#feedback", label: "Feedback" },
];

export default function DocsPage() {
  return (
    <div className="min-h-screen bg-ink font-sans text-paper">
      <SiteHeader />
      <main className="mx-auto grid w-full max-w-[1120px] gap-10 px-6 py-12 md:grid-cols-[220px_minmax(0,1fr)] md:px-10 md:py-16">
        <aside className="md:sticky md:top-[86px] md:h-fit">
          <p className="mb-4 mt-0 font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-mint">
            Docs
          </p>
          <DocsNav items={navItems} />
        </aside>

        <article className="min-w-0">
          <div className="mb-12 border-b border-white/[0.08] pb-10">
            <p className="mb-4 mt-0 font-mono text-[12px] font-semibold uppercase tracking-[0.18em] text-amber">
              Pre-release version
            </p>
            <h1 className="m-0 whitespace-nowrap font-display text-[clamp(28px,5.5vw,56px)] font-bold leading-[1] tracking-[0.01em] text-paper">
              Hillclimber docs
            </h1>
          </div>

          <section
            id="overview"
            className="scroll-mt-28 border-b border-white/[0.08] pb-12"
          >
            <h2 className="m-0 text-[28px] font-semibold tracking-0 text-paper">
              Overview
            </h2>
            <p className="mt-5 max-w-[760px] text-[16px] leading-[1.8] text-paper/[0.85]">
              Hillclimber is a framework for long-running agentic sessions aimed
              at measurable, eval-driven codebase improvement.
            </p>
            <p className="mt-4 max-w-[760px] text-[16px] leading-[1.8] text-paper/[0.85]">
              Its distinct feature is that it pushes you to explicitly define an
              eval function and spec (success criteria, budget, models) for the
              experiment. This is particularly useful when you want to run long
              sessions yet you don&apos;t have unlimited tokens to burn and you
              want fine control over long-running jobs.
            </p>
            <p className="mt-4 max-w-[760px] text-[16px] leading-[1.8] text-paper/[0.85]">
              By being open-source and harness agnostic, Hillclimber allows you
              to swap harnesses (Claude Code, Codex, Cursor etc) and models and
              choose the one that most suits your needs and budget.
            </p>
          </section>

          <section
            id="getting-started"
            className="scroll-mt-28 border-b border-white/[0.08] py-12"
          >
            <h2 className="m-0 text-[28px] font-semibold tracking-0 text-paper">
              Getting started
            </h2>

            <ol className="mt-8 grid gap-8 pl-0 [counter-reset:step]">
              <li className="grid grid-cols-[34px_minmax(0,1fr)] items-start gap-4 [counter-increment:step]">
                <span className="mt-[2px] flex size-[34px] items-center justify-center rounded-md bg-mint text-center font-mono text-[13px] font-bold text-ink before:content-[counter(step)]" />
                <div className="min-w-0">
                  <p className="m-0 text-[16px] leading-[1.7] text-paper/[0.85]">
                    To run Hillclimber,{" "}
                    <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[13px] text-paper/[0.82]">
                      cd
                    </code>{" "}
                    to your project and run the init command.
                  </p>
                  <CodeBlock>{`cd my_projects/project_x
hillclimber init -i`}</CodeBlock>
                  <p className="mt-4 text-[15px] leading-[1.75] text-paper/[0.72]">
                    After running the init command and following the wizard
                    instructions, two files will be produced:{" "}
                    <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[13px] text-paper/[0.82]">
                      hillclimber.toml
                    </code>{" "}
                    and{" "}
                    <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[13px] text-paper/[0.82]">
                      eval.py
                    </code>
                    .
                  </p>
                  <ul className="mt-3 grid gap-2 pl-5 text-[15px] leading-[1.7] text-paper/[0.72] [&>li]:list-disc">
                    <li>
                      <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[13px] text-paper/[0.82]">
                        hillclimber.toml
                      </code>{" "}
                      — defines the specs for the experiment. Hillclimber was
                      designed to explicitly push users towards defining goal,
                      budget, models etc.
                    </li>
                    <li>
                      <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[13px] text-paper/[0.82]">
                        eval.py
                      </code>{" "}
                      — defines an eval/fitness function.
                    </li>
                  </ul>
                </div>
              </li>

              <li className="grid grid-cols-[34px_minmax(0,1fr)] items-start gap-4 [counter-increment:step]">
                <span className="mt-[2px] flex size-[34px] items-center justify-center rounded-md bg-mint text-center font-mono text-[13px] font-bold text-ink before:content-[counter(step)]" />
                <div className="min-w-0">
                  <p className="m-0 text-[16px] leading-[1.7] text-paper/[0.85]">
                    Implement the{" "}
                    <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[13px] text-paper/[0.82]">
                      evaluate
                    </code>{" "}
                    function inside{" "}
                    <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[13px] text-paper/[0.82]">
                      eval.py
                    </code>
                    .
                  </p>
                  <p className="mt-3 text-[15px] leading-[1.75] text-paper/[0.72]">
                    Hillclimber uses the{" "}
                    <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[13px] text-paper/[0.82]">
                      eval.py
                    </code>{" "}
                    file to calculate the baseline score and delta for each
                    cycle of the experiment. You must implement{" "}
                    <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[13px] text-paper/[0.82]">
                      evaluate
                    </code>{" "}
                    before running a Hillclimber experiment.
                  </p>
                  <p className="mt-3 text-[15px] leading-[1.75] text-paper/[0.72]">
                    Pro tip: ask the coding agent of your choice to implement it
                    for you{" "}
                    <span className="line-through decoration-paper/[0.4]">
                      if you are lazy
                    </span>{" "}
                    😉
                  </p>
                </div>
              </li>

              <li className="grid grid-cols-[34px_minmax(0,1fr)] items-start gap-4 [counter-increment:step]">
                <span className="mt-[2px] flex size-[34px] items-center justify-center rounded-md bg-mint text-center font-mono text-[13px] font-bold text-ink before:content-[counter(step)]" />
                <div className="min-w-0">
                  <p className="m-0 text-[16px] leading-[1.7] text-paper/[0.85]">
                    Commit the{" "}
                    <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[13px] text-paper/[0.82]">
                      hillclimber.toml
                    </code>{" "}
                    and{" "}
                    <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[13px] text-paper/[0.82]">
                      eval.py
                    </code>{" "}
                    files.
                  </p>
                  <div className="mt-4 rounded-lg border border-amber/[0.35] bg-amber/[0.08] p-4">
                    <p className="m-0 flex items-center gap-2 font-mono text-[12px] font-semibold uppercase tracking-[0.12em] text-amber">
                      <span aria-hidden>⚠️</span> Annoyance Warning
                    </p>
                    <p className="mt-3 text-[15px] leading-[1.75] text-paper/[0.85]">
                      This is an annoying part of the current version and
                      I&apos;m looking forward to a better solution in upcoming
                      versions. Thank you for being with me!
                    </p>
                  </div>
                  <p className="mt-4 text-[15px] leading-[1.75] text-paper/[0.72]">
                    Hillclimber runs each experiment in its own dedicated
                    workspace, forked from your latest commit — which is what
                    lets it run multiple cycles in parallel. The tradeoff:
                    because those workspaces are checked out from committed
                    state, any uncommitted work (including the freshly created{" "}
                    <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[13px] text-paper/[0.82]">
                      hillclimber.toml
                    </code>{" "}
                    and{" "}
                    <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[13px] text-paper/[0.82]">
                      eval.py
                    </code>
                    ) won&apos;t make it into them.
                  </p>
                  <p className="mt-3 text-[15px] leading-[1.75] text-paper/[0.72]">
                    Before a run, you&apos;ll need to commit everything —
                    otherwise Hillclimber will stop and ask you to, rather than
                    risk scoring two different versions of your code.
                  </p>
                </div>
              </li>

              <li className="grid grid-cols-[34px_minmax(0,1fr)] items-start gap-4 [counter-increment:step]">
                <span className="mt-[2px] flex size-[34px] items-center justify-center rounded-md bg-mint text-center font-mono text-[13px] font-bold text-ink before:content-[counter(step)]" />
                <div className="min-w-0">
                  <p className="m-0 text-[16px] leading-[1.7] text-paper/[0.85]">
                    Start climbing.
                  </p>
                  <p className="mt-3 text-[15px] leading-[1.75] text-paper/[0.72]">
                    Execute the run command and Hillclimber starts improving
                    your codebase.
                  </p>
                  <CodeBlock>hillclimber run</CodeBlock>
                </div>
              </li>
            </ol>
          </section>

          <section
            id="key-concepts"
            className="scroll-mt-28 border-b border-white/[0.08] py-12"
          >
            <h2 className="m-0 text-[28px] font-semibold tracking-0 text-paper">
              Key concepts
            </h2>
            <div className="mt-6 grid gap-4">
              {[
                {
                  title: "Experiment",
                  body: "One full run of the hillclimber run command.",
                },
                {
                  title: "Cycle",
                  body: "One attempt to improve the codebase. An experiment consists of 1..n cycles. Cycles can run in parallel, so you explore multiple improvements at once rather than one at a time.",
                },
                {
                  title: "Strategy",
                  body: "A predefined workflow that Hillclimber uses to improve your code. Currently the user must define whether they want a simple strategy (cheaper and faster, but potentially lower improvement rates) or a more sophisticated one.",
                },
                {
                  title: "Artefact",
                  body: "File or folder that Hillclimber should improve.",
                },
                {
                  title: "Goal",
                  body: "A specific eval score that Hillclimber should achieve. If the goal is achieved, Hillclimber stops the experiment.",
                },
                {
                  title: "Budget",
                  body: "Max number of cycles, tokens or money that Hillclimber will use. If the budget is exhausted, Hillclimber stops the experiment.",
                },
                {
                  title: "Agent",
                  body: "The entity that does all the work. An agent consists of a harness (Claude Code, Codex, Cursor) and a model.",
                },
              ].map((concept) => (
                <div
                  key={concept.title}
                  className="rounded-lg border border-white/[0.08] bg-white/[0.025] p-5"
                >
                  <h3 className="m-0 font-mono text-[13px] font-semibold uppercase tracking-[0.12em] text-mint">
                    {concept.title}
                  </h3>
                  <p className="mb-0 mt-3 text-[15px] leading-[1.7] text-paper/[0.72]">
                    {concept.body}
                  </p>
                </div>
              ))}
            </div>
          </section>

          <section id="feedback" className="scroll-mt-28 py-12">
            <h2 className="m-0 text-[28px] font-semibold tracking-0 text-paper">
              Feedback
            </h2>
            <p className="mt-5 max-w-[760px] text-[16px] leading-[1.8] text-paper/[0.85]">
              I&apos;d appreciate any feedback you have. Feel free to{" "}
              <a
                href={AUTHOR_URL}
                target="_blank"
                rel="noreferrer"
                className="text-mint underline decoration-mint/[0.4] underline-offset-4 transition-colors hover:decoration-mint"
              >
                DM me
              </a>{" "}
              or run:
            </p>
            <CodeBlock>{`hillclimber feedback "My take is..."`}</CodeBlock>
          </section>
        </article>
      </main>
      <SiteFooter />
    </div>
  );
}
