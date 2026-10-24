import type { Metadata, Viewport } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import "./globals.css";
import Nav from "@/components/Nav";
import Footer from "@/components/Footer";
import Reveal from "@/components/Reveal";

export const metadata: Metadata = {
  metadataBase: new URL("https://myceliclabs.com"),
  title: { default: "Mycelic — Discover what your company is missing", template: "%s — Mycelic" },
  description:
    "Mycelic connects signals across people, agents, and systems to discover what none of them can see alone. Private by design. Distributed by default.",
  openGraph: {
    title: "Mycelic",
    description: "Continuous discovery for organizations. Private by design. Distributed by default.",
    type: "website",
    siteName: "Mycelic",
  },
};

export const viewport: Viewport = { themeColor: "#07080a", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body>
        <Nav />
        <main>{children}</main>
        <Footer />
        <Reveal />
      </body>
    </html>
  );
}
