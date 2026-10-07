import Link from "next/link";

export default function Footer() {
  return (
    <footer className="footer">
      <div className="wrap footer-inner">
        <div>
          <div className="label" style={{ color: "var(--fg)" }}>Mycelic</div>
          <div className="label">Continuous discovery for organizations</div>
          <div className="label">Private by design. Distributed by default.</div>
        </div>
        <div className="footer-links">
          <Link href="/technology">Technology</Link>
          <Link href="/research">Research</Link>
          <Link href="/company">Company</Link>
          <a href="https://github.com/anovruzov/NeuralGraph" rel="noopener">Source</a>
        </div>
      </div>
    </footer>
  );
}
