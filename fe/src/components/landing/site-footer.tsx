import { AUTHOR_URL } from "@/lib/site";

export function SiteFooter() {
  return (
    <footer className="border-t border-white/[0.08] bg-ink px-10 py-10 text-center">
      <p className="m-0 font-mono text-[13px] font-medium leading-[1.6] tracking-[0.04em] text-paper/[0.45]">
        Built with love, tokens and human agency by{" "}
        <a
          href={AUTHOR_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="border-b border-mint/40 text-mint no-underline transition-opacity hover:opacity-70"
        >
          Oleh
        </a>
      </p>
    </footer>
  );
}
