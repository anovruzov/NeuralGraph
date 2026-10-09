import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Technology",
  description: "How Mycelic discovers, verifies, distributes, and learns across an organization without moving raw data.",
};

const STEPS = [
  { k: "Local intelligence", h: "Reason where information lives.", p: "Every participant keeps its records on its own node and reasons over them there. What leaves a node is structured: a claim, the evidence it rests on, and a sketch of what the node knows about." },
  { k: "Lineage", h: "Every finding remains connected to its evidence.", p: "Lineage records which agents, teams, and layers contributed, in what order. When evidence is retracted, every conclusion that depended on it is withdrawn and re-evaluated." },
  { k: "Independent support", h: "Separate sources can strengthen or challenge a hypothesis.", p: "Two reports that share an origin are one report. Contradiction is information. A conclusion can require corroboration from separate units before it is allowed to form." },
  { k: "Hypothesis formation", h: "Signals that would be weak alone can become meaningful together.", p: "A facet of a distributed problem is indistinguishable from ordinary chatter where it is observed. Hypotheses form from how signals line up across nodes, not from ranking documents." },
  { k: "Continual questioning", h: "The system identifies what it still needs to know.", p: "A hypothesis carries its uncertainty. Mycelic turns it into targeted questions: which source could confirm this, what would contradict it, what has changed since." },
  { k: "Verification", h: "Questions return to the relevant sources.", p: "The question travels to the node that holds the original evidence and is answered there, against the raw records, without those records leaving." },
  { k: "Distribution", h: "Verified knowledge is propagated back through the organization.", p: "A verified discovery is routed to the people, agents, and systems that need it, with its evidence attached and scoped to what each is entitled to see." },
  { k: "Recursive learning", h: "New knowledge changes what the system investigates next.", p: "Distribution changes the state of the network. That state shapes the next round of questions. The output of one discovery is the input of the next." },
];

export default function Technology() {
  return (
    <>
      <section className="page-head wrap">
        <div className="label signal">Technology</div>
        <h1 className="display">Reason where the data lives. Move only what is verified.</h1>
      </section>

      <section className="section" style={{ paddingTop: 0 }}>
        <div className="wrap">
          <div className="steps reveal">
            {STEPS.map((s, i) => (
              <div className="step" key={s.k}>
                <div className="label">{String(i + 1).padStart(2, "0")} · {s.k}</div>
                <h3>{s.h}</h3>
                <p>{s.p}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="section">
        <div className="wrap">
          <div className="section-head reveal">
            <div className="label">Underneath</div>
          </div>
          <div className="grid-2 reveal">
            <div className="stack hairline-top">
              <div className="label signal">NeuralGraph</div>
              <h3 className="h3">Local memory with structure and provenance.</h3>
              <p className="body">
                Each agent keeps its knowledge as a graph of observations, relations, and conversational pairs, and a question is routed
                to the participant most likely to hold the answer. On LoCoMo, that routing moved accuracy from 64.9% to 73.8% on the
                same 282 questions under the same judge, and the gain holds under every grader tried.
              </p>
            </div>
            <div className="stack hairline-top">
              <div className="label signal">Tesseract</div>
              <h3 className="h3">The retrieval engine under the memory.</h3>
              <p className="body">
                Hybrid retrieval over the local graph with a fixed context budget. Retrieval decides; reranking only reorders. Shared
                memories move as signed events on a durable log, agent to team to enterprise, and the whole state rebuilds from replay.
              </p>
            </div>
          </div>
          <div className="link-row reveal">
            <Link className="link" href="/research">Research</Link>
            <a className="link" href="https://github.com/anovruzov/NeuralGraph" rel="noopener">Source</a>
          </div>
        </div>
      </section>
    </>
  );
}
