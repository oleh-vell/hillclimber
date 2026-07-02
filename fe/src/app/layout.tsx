import type { Metadata } from "next";
import { Space_Grotesk, JetBrains_Mono } from "next/font/google";
import localFont from "next/font/local";
import "./globals.css";

const spaceGrotesk = Space_Grotesk({
  variable: "--font-space-grotesk",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  display: "swap",
});

// Display face used for the HILLCLIMBER wordmark and step numerals.
const gameplay = localFont({
  src: "../fonts/gameplay.ttf",
  variable: "--font-gameplay",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Hillclimber — An open-source /goal alternative",
  description:
    "Auto-improve your code. Define your goal, budget, and models—hillclimber orchestrates, executes, and monitors the work. Open-source and harness-agnostic.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${spaceGrotesk.variable} ${jetbrainsMono.variable} ${gameplay.variable}`}
    >
      <body>{children}</body>
    </html>
  );
}
