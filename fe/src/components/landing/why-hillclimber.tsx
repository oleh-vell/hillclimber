import type { ReactNode } from "react";
import {
  CodeIcon,
  ExtendIcon,
  LinkIcon,
  ShieldIcon,
  SlidersIcon,
} from "./icons";

function WhyCard() {
  return (
    <div className="mx-auto max-w-[680px] text-left">
      <div className="overflow-hidden rounded-[14px] border border-white/10 bg-card shadow-[0_24px_60px_rgba(0,0,0,0.4)]">
        <div className="flex items-center gap-[7px] border-b border-white/[0.07] px-[15px] py-[11px]">
          <span className="size-[11px] rounded-full bg-coral" />
          <span className="size-[11px] rounded-full bg-amber-soft" />
          <span className="size-[11px] rounded-full bg-mint" />
          <span className="ml-2 font-mono text-[12px] font-medium text-paper/[0.45]">
            why.md
          </span>
        </div>
        <div className="px-[30px] pb-[26px] pt-[30px] font-mono [text-wrap:pretty]">
          <p className="m-0 text-[14.5px] leading-[1.85] text-paper/[0.82]">
            <span className="text-mint/[0.65]"># </span>Models are great at
            iteratively improving performance. But without explicit constraints
            and goals, you risk burning tokens and losing control.
          </p>
          <p className="mt-5 text-[14.5px] leading-[1.85] text-paper/[0.82]">
            <span className="text-mint/[0.65]"># </span>I built hillclimber to
            do two things:
          </p>
          <p className="ml-5 mt-3 text-[14.5px] leading-[1.85] text-paper/[0.7]">
            <span className="text-mint">1. </span> Force you to be explicit
            upfront — what you want, and how much you&apos;re willing to spend.
          </p>
          <p className="ml-5 mt-[6px] text-[14.5px] leading-[1.85] text-paper/[0.7]">
            <span className="text-mint">2.</span> Leave you free to choose any
            model provider you like.
          </p>
        </div>
      </div>
    </div>
  );
}

type Feature = {
  icon: ReactNode;
  title: string;
  description: string;
  comingSoon?: boolean;
};

const FEATURES: Feature[] = [
  {
    icon: <SlidersIcon className="size-[22px]" />,
    title: "You're in control",
    description:
      "Set the goal, budget, and guardrails. Approve or roll back any change — hillclimber never touches main without your rules.",
  },
  {
    icon: <CodeIcon className="size-[22px]" />,
    title: "Free & open-source",
    description:
      "MIT-licensed and fully self-hostable. No vendor lock-in, no usage caps — read the code, fork it, ship it.",
  },
  {
    icon: <ExtendIcon className="size-[22px]" />,
    title: "Extendable by design",
    description:
      "Plug in custom scorers, generators, and hooks. Every stage of the loop is an interface you can override.",
  },
  {
    icon: <ShieldIcon className="size-[22px]" />,
    title: "Durable execution",
    description:
      "Long-running jobs survive crashes and restarts. Every cycle is checkpointed and resumable.",
    comingSoon: true,
  },
  {
    icon: <LinkIcon strokeWidth={1.6} className="size-[22px]" />,
    title: "Use with your harness",
    description:
      "Bring your own eval harness or agent framework. hillclimber orchestrates around it, not the other way around.",
    comingSoon: true,
  },
];

function ComingSoonBadge() {
  return (
    <span className="mt-[3px] flex-none rounded-full border border-amber-soft/30 bg-amber-soft/[0.08] px-[9px] py-[5px] font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.12em] text-amber-soft">
      Coming soon
    </span>
  );
}

function FeatureRow({ icon, title, description, comingSoon }: Feature) {
  return (
    <div className="grid grid-cols-[64px_1fr] items-center gap-[30px] border-b border-white/10 px-2 py-[30px]">
      <span
        className={
          comingSoon
            ? "inline-flex size-12 items-center justify-center rounded-xl border border-white/10 bg-white/[0.02] text-mint/[0.7]"
            : "inline-flex size-12 items-center justify-center rounded-xl border border-mint/30 bg-mint/[0.08] text-mint"
        }
      >
        {icon}
      </span>
      {comingSoon ? (
        <div className="flex flex-wrap items-start gap-[14px]">
          <div className="min-w-[280px] flex-1">
            <h3 className="mb-[6px] text-[21px] font-semibold tracking-[-0.01em] text-paper/[0.8]">
              {title}
            </h3>
            <p className="m-0 text-[15px] leading-[1.55] text-paper/[0.45]">
              {description}
            </p>
          </div>
          <ComingSoonBadge />
        </div>
      ) : (
        <div>
          <h3 className="mb-[6px] text-[21px] font-semibold tracking-[-0.01em] text-paper">
            {title}
          </h3>
          <p className="m-0 text-[15px] leading-[1.55] text-paper/[0.55]">
            {description}
          </p>
        </div>
      )}
    </div>
  );
}

export function WhyHillclimber() {
  return (
    <section
      id="why-hillclimber"
      className="relative border-t border-white/[0.08] bg-panel px-6 py-[120px] md:px-10"
    >
      <div className="mx-auto max-w-[980px]">
        <div className="mb-[56px] text-center">
          <div className="mb-[26px] font-mono text-[12px] font-semibold uppercase leading-none tracking-[0.2em] text-mint">
            Why hillclimber
          </div>
          <WhyCard />
        </div>

        <div className="flex flex-col border-t border-white/10">
          {FEATURES.map((feature) => (
            <FeatureRow key={feature.title} {...feature} />
          ))}
        </div>
      </div>
    </section>
  );
}
