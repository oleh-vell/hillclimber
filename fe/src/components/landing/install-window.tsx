import { INIT_CMD, INSTALL_CMD } from "@/lib/site";
import { CopyButton } from "./copy-button";

/** The hero's install/start terminal: bash header, copy-all, both commands. */
export function InstallWindow() {
  return (
    <div className="w-[min(560px,92vw)] overflow-hidden rounded-xl border border-white/10 bg-[rgba(10,12,11,0.7)]">
      <div className="flex items-center justify-between border-b border-white/[0.07] px-[14px] py-[9px]">
        <span className="font-mono text-[11px] font-medium leading-none tracking-[0.08em] text-paper/[0.4]">
          bash
        </span>
        <CopyButton
          value={`${INSTALL_CMD} && ${INIT_CMD}`}
          className="font-mono text-[11px] font-semibold uppercase leading-none tracking-[0.08em] text-mint transition-opacity hover:opacity-70"
        />
      </div>
      <div className="space-y-[9px] overflow-x-auto px-4 py-[15px] font-mono text-[13px]">
        <div className="flex items-center gap-[11px]">
          <span className="text-mint">$</span>
          <span className="whitespace-nowrap text-paper/[0.92]">
            {INSTALL_CMD}
          </span>
        </div>
        <div className="flex items-center gap-[11px]">
          <span className="text-mint">$</span>
          <span className="whitespace-nowrap text-paper/[0.92]">
            {INIT_CMD}
          </span>
        </div>
      </div>
    </div>
  );
}
