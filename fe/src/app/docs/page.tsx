import type { Metadata } from "next";
import { SiteFooter } from "@/components/landing/site-footer";
import { SiteHeader } from "@/components/landing/site-header";
import { INIT_CMD, INSTALL_CMD } from "@/lib/site";

export const metadata: Metadata = {
  title: "Docs | Hillclimber",
  description:
    "Beta documentation for Hillclimber: overview, getting started, and key concepts.",
};

const navItems = [
  { href: "#overview", label: "Overview" },
  { href: "#getting-started", label: "Getting started" },
  { href: "#key-concepts", label: "Key concepts" },
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
          <nav
            className="flex gap-2 overflow-x-auto border-b border-white/[0.08] pb-4 md:flex-col md:overflow-visible md:border-b-0 md:border-r md:pb-0 md:pr-6"
            aria-label="Documentation sections"
          >
            {navItems.map((item) => (
              <a
                key={item.href}
                href={item.href}
                className="whitespace-nowrap rounded-md px-3 py-2 font-mono text-[13px] font-medium text-paper/[0.58] no-underline transition-colors hover:bg-white/[0.05] hover:text-paper"
              >
                {item.label}
              </a>
            ))}
          </nav>
        </aside>

        <article className="min-w-0">
          <div className="mb-12 border-b border-white/[0.08] pb-10">
            <p className="mb-4 mt-0 font-mono text-[12px] font-semibold uppercase tracking-[0.18em] text-amber">
              Beta release
            </p>
            <h1 className="m-0 text-[clamp(40px,7vw,72px)] font-semibold leading-[0.98] tracking-0 text-paper">
              Hillclimber docs
            </h1>
            <p className="mt-5 max-w-[720px] font-mono text-[15px] leading-[1.75] text-paper/[0.62]">
              A compact guide to running Hillclimber while the project is in
              beta. These docs cover the core workflow and the concepts you need
              before wiring it into a real codebase.
            </p>
          </div>

          <section id="overview" className="scroll-mt-28 border-b border-white/[0.08] pb-12">
            <h2 className="m-0 text-[28px] font-semibold tracking-0 text-paper">
              Overview
            </h2>
            <p className="mt-5 max-w-[760px] text-[16px] leading-[1.8] text-paper/[0.68]">
              Hillclimber automates iterative code improvement. You define a
              goal, provide an evaluation loop, set a budget, and let the tool
              run candidate changes through your chosen coding harness.
            </p>
            <p className="mt-4 max-w-[760px] text-[16px] leading-[1.8] text-paper/[0.68]">
              The beta release focuses on the essentials: local execution,
              measurable progress, and a simple configuration surface that can
              evolve with your project.
            </p>
          </section>

          <section
            id="getting-started"
            className="scroll-mt-28 border-b border-white/[0.08] py-12"
          >
            <h2 className="m-0 text-[28px] font-semibold tracking-0 text-paper">
              Getting started
            </h2>
            <ol className="mt-6 grid gap-4 pl-0 [counter-reset:step]">
              {[
                "Install the CLI globally.",
                "Run Hillclimber in a project with an evaluation command.",
                "Review the generated changes and keep the runs that improve your score.",
              ].map((step) => (
                <li
                  key={step}
                  className="grid grid-cols-[34px_minmax(0,1fr)] items-start gap-4 rounded-lg border border-white/[0.08] bg-white/[0.025] p-4 [counter-increment:step]"
                >
                  <span className="flex size-[34px] items-center justify-center rounded-md bg-mint text-center font-mono text-[13px] font-bold text-ink before:content-[counter(step)]" />
                  <span className="pt-[5px] text-[15px] leading-[1.6] text-paper/[0.72]">
                    {step}
                  </span>
                </li>
              ))}
            </ol>
            <div className="mt-6 overflow-hidden rounded-lg border border-white/[0.1] bg-card">
              <div className="border-b border-white/[0.07] px-4 py-2 font-mono text-[11px] font-medium tracking-[0.08em] text-paper/[0.42]">
                bash
              </div>
              <pre className="m-0 overflow-x-auto p-4 font-mono text-[13px] leading-[1.7] text-paper/[0.82]">
                <code>{`${INSTALL_CMD}
${INIT_CMD}
hillclimber run`}</code>
              </pre>
            </div>
          </section>

          <section id="key-concepts" className="scroll-mt-28 py-12">
            <h2 className="m-0 text-[28px] font-semibold tracking-0 text-paper">
              Key concepts
            </h2>
            <div className="mt-6 grid gap-4">
              {[
                {
                  title: "Goal",
                  body: "The concrete outcome Hillclimber should pursue during a run.",
                },
                {
                  title: "Eval",
                  body: "The command or script that scores whether a candidate change is better.",
                },
                {
                  title: "Harness",
                  body: "The coding agent backend used to propose and apply changes.",
                },
                {
                  title: "Budget",
                  body: "The time, token, or run limit that keeps beta experiments bounded.",
                },
              ].map((concept) => (
                <div
                  key={concept.title}
                  className="rounded-lg border border-white/[0.08] bg-white/[0.025] p-5"
                >
                  <h3 className="m-0 font-mono text-[13px] font-semibold uppercase tracking-[0.12em] text-mint">
                    {concept.title}
                  </h3>
                  <p className="mb-0 mt-3 text-[15px] leading-[1.7] text-paper/[0.66]">
                    {concept.body}
                  </p>
                </div>
              ))}
            </div>
          </section>
        </article>
      </main>
      <SiteFooter />
    </div>
  );
}
