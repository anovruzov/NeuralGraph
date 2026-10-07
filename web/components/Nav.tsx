"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/technology", label: "Technology" },
  { href: "/research", label: "Research" },
  { href: "/company", label: "Company" },
];

export default function Nav() {
  const path = usePathname();
  return (
    <header className="nav">
      <div className="wrap nav-inner">
        <Link href="/" className="wordmark" aria-label="Mycelic home">
          <span className="wordmark-mark" aria-hidden="true" />
          Mycelic
        </Link>
        <nav className="nav-links" aria-label="Primary">
          {links.map((l) => (
            <Link key={l.href} href={l.href} aria-current={path.startsWith(l.href) ? "page" : undefined}>
              {l.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}
