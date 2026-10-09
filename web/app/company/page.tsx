import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Company",
  description: "Mycelic is building the infrastructure that lets organizations learn collectively.",
};

export default function Company() {
  return (
    <>
      <section className="page-head wrap">
        <div className="label signal">Company</div>
        <h1 className="display">We are building systems that let organizations learn collectively.</h1>
      </section>

      <section className="section" style={{ paddingTop: 0 }}>
        <div className="wrap prose reveal">
          <p>
            Companies are becoming networks of people, models, agents, software, and machines. No single model will understand
            everything happening inside them.
          </p>
          <p className="dim">
            Mycelic is building the infrastructure that lets knowledge emerge across that system, be verified at its source, and
            return to the network so the organization can learn from it.
          </p>
          <div className="link-row">
            <Link className="link" href="/research">Research</Link>
            <Link className="link" href="/technology">Technology</Link>
            <a className="link" href="https://github.com/anovruzov/NeuralGraph" rel="noopener">GitHub</a>
          </div>
        </div>
      </section>
    </>
  );
}
