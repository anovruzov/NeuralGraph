import type { Metadata } from "next";
import Link from "next/link";
import SystemVisual from "@/components/SystemVisual";

export const metadata: Metadata = {
  title: "Technology",
  description: "How Mycelic discovers, verifies, distributes, and learns across an organization without moving raw data.",
};

const STEPS = [
  { k: "Local intelligence", h: "Reason where information lives.", p: "Every participant, whether a person's assistant, an agent, or a device, keeps its records on its own node and reasons over them there. Raw data does not move to a central store. What leaves a node is structured: a claim, the evidence it rests on, and a sketch of what the node knows about." },
  { k: "Lineage", h: "Every finding remains connected to its evidence.", p: "A conclusion is never detached from the observations behind it. Lineage records which agents, teams, and layers contributed, and in what order, so that any finding can be traced and re-checked. When evidence is retracted, every conclusion that depended on it is withdrawn and re-evaluated on what remains." },
  { k: "Independent support", h: "Separate sources can strengthen or challenge a hypothesis.", p: "Two reports that share an origin are one report. Mycelic tracks whether supporting signals are actually independent, and treats contradiction as information rather than noise. Corroboration requirements can span organizational units: a conclusion can require evidence from two regions before it is allowed to form." },
  { k: "Hypothesis formation", h: "Signals that would be weak alone can become meaningful together.", p: "No per-record feature identifies a weak signal; a facet of a distributed problem is indistinguishable from ordinary chatter where it is observed. Hypotheses are formed at the level of entities and relations, from how signals line up across nodes, not from ranking documents." },
  { k: "Continual questioning", h: "The system identifies what it still needs to know.", p: "A hypothesis carries its own uncertainty. Mycelic turns that uncertainty into targeted questions: which source could confirm this, what would contradict it, what has changed since. Questions are budgeted and aimed; a question only counts if its answer could move a conclusion." },
  { k: "Verification", h: "Questions return to the relevant sources.", p: "Verification is downward. The question travels to the node that holds the original evidence and is answered there, against the raw records, without those records leaving. This is where the work is: in our benchmark, upward summarisation alone finds almost nothing, and targeted questioning back down the hierarchy is what makes discovery possible." },
  { k: "Distribution", h: "Verified knowledge is propagated back through the organization.", p: "A verified discovery is routed to the people, agents, and systems that need it, with its evidence and lineage attached. Routing is scoped: each unit sees what it is entitled to see, with redaction where needed, and no one receives a conclusion without the means to check it." },
  { k: "Recursive learning", h: "New knowledge changes what the system investigates next.", p: "Distribution changes the state of the network: new edges, new priorities, new uncertainty. That state shapes the next round of questions. The output of one discovery is the input of the next, which is what makes Mycelic a learning system rather than a reporting system." },
];

export default function Technology() {
  return (
    <>
      <section className="page-head wrap">
        <div className="label signal">Technology</div>
        <h1 className="display">A system that learns across the organization.</h1>
        <p className="lede">
          Explained from the concept downward. Mycelic is a distributed systems thesis before it is a set of components: reasoning
          stays where data lives, findings carry their evidence, and verified knowledge changes the network that produced it.
        </p>
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
            <div className="label">The loop, as a system</div>
            <h2 className="h2" style={{ maxWidth: "18ch" }}>From local sources to the next investigation.</h2>
          </div>
          <div className="reveal"><SystemVisual /></div>
        </div>
      </section>

      <section className="section">
        <div className="wrap">
          <div className="section-head reveal">
            <div className="label">Implementation</div>
            <h2 className="h2" style={{ maxWidth: "20ch" }}>What runs underneath.</h2>
            <p className="lede">Two components carry the thesis. Both are open and both are measured.</p>
          </div>
          <div className="grid-2 reveal">
            <div className="stack hairline-top">
              <div className="label signal">NeuralGraph</div>
              <h3 className="h3">Local memory with structure and provenance.</h3>
              <p className="body">
                The per-participant memory layer. Each agent keeps its own knowledge as a graph of observations, relations, and
                conversational pairs, and routes a question to the participant most likely to hold the answer. On the LoCoMo
                benchmark, per-agent routing with pair back-fill moved accuracy from 64.9% to 73.8% on the same 282 questions under
                the same judge, and the gain holds under every grader tried.
              </p>
            </div>
            <div className="stack hairline-top">
              <div className="label signal">Tesseract</div>
              <h3 className="h3">The retrieval engine under the memory.</h3>
              <p className="body">
                Hybrid retrieval over the local graph: embeddings, lexical match, time chains, and speaker links, with a fixed
                context budget. Retrieval decides; reranking only reorders. The campaign that produced it published every negative
                result alongside the positive ones, including the mechanisms that improved recall and hurt answers.
              </p>
            </div>
            <div className="stack hairline-top">
              <div className="label">Transport and lineage</div>
              <h3 className="h3">A durable event log, not a database of record.</h3>
              <p className="body">
                Shared memories move through the organization as signed events on a file-backed stream, agent to team to department
                to region to enterprise. Lose the service database and it rebuilds from the stream; lose the broker and agents keep
                writing through a local outbox. Every derived memory carries the full lineage of what produced it.
              </p>
            </div>
            <div className="stack hairline-top">
              <div className="label">Composable rules</div>
              <h3 className="h3">Conclusions that higher rules consume.</h3>
              <p className="body">
                A conclusion exposes a slot. Higher-level rules consume slots, require corroboration across units, and compose
                multi-step derivations: a strategy resting on regional conclusions resting on agents&rsquo; observations. When evidence is
                retracted, dependent conclusions are withdrawn and re-evaluated, and return as new versions when fresh evidence arrives.
              </p>
            </div>
          </div>
          <div className="link-row reveal">
            <Link className="link" href="/research">The research behind it</Link>
            <a className="link" href="https://github.com/anovruzov/NeuralGraph" rel="noopener">Source and benchmarks</a>
          </div>
        </div>
      </section>
    </>
  );
}
