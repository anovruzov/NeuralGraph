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
          <p className="dim">
            We are a small research-driven infrastructure company. The work is open, the benchmarks name their judges, and the
            negative results are published with the positive ones.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="wrap">
          <div className="facts reveal">
            <div>
              <div className="label">Thesis</div>
              <p>An organization can collectively contain knowledge that no individual participant possesses. Mycelic exists to find it.</p>
            </div>
            <div>
              <div className="label">Principle</div>
              <p>Private by design. Distributed by default. Raw data stays local; findings travel with their evidence and lineage.</p>
            </div>
            <div>
              <div className="label">Method</div>
              <p>Falsifiable questions, reproducible artifacts, and a benchmark report that argues against its own results.</p>
            </div>
            <div>
              <div className="label">Horizon</div>
              <p>Memory is the first primitive. Emergence is the second. Recursive discovery is the destination.</p>
            </div>
          </div>
          <div className="link-row reveal">
            <Link className="link" href="/research">Research</Link>
            <Link className="link" href="/technology">Technology</Link>
            <a className="link" href="https://github.com/anovruzov/NeuralGraph" rel="noopener">GitHub</a>
          </div>
        </div>
      </section>
    </>
  );
}
