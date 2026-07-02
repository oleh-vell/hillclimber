import { SiteHeader } from "@/components/landing/site-header";
import { Hero } from "@/components/landing/hero";
import { HowItWorks } from "@/components/landing/how-it-works";
import { WhyHillclimber } from "@/components/landing/why-hillclimber";
import { FinalCta } from "@/components/landing/final-cta";
import { SiteFooter } from "@/components/landing/site-footer";

export default function Home() {
  return (
    <div className="bg-ink font-sans">
      <SiteHeader />
      <main>
        <Hero />
        <HowItWorks />
        <WhyHillclimber />
        <FinalCta />
      </main>
      <SiteFooter />
    </div>
  );
}
