import type { Metadata } from "next";
import Metrics from "@/components/Metrics";
import { locomoAB, locomoSlice, emergence, recursion, runtime } from "./metrics";

export const metadata: Metadata = {
  title: "Research",
  description: "Three connected investigations into how an organization can learn as a system: memory, emergence, and recursion.",
};

export default function Research() {
  return (
    <>
      <section className="page-head wrap">
        <div className="label signal">Research</div>
        <h1 className="display">How can an organization learn as a system?</h1>
        <p className="lede">
          Three tracks, one problem. Each asks a falsifiable question, names its judge, and publishes what failed next to what
          worked. Every number below is read from a repository artifact and labeled with its scope; numbers from different scopes
          are not interchangeable.
        </p>
      </section>

      <section className="section" style={{ paddingTop: 0 }}>
        <div className="wrap">
          <div className="tracks">

            <div className="track reveal" id="memory">
              <div className="track-head">
                <div className="label">Track 01 · Memory</div>
                <h2>How does knowledge survive?</h2>
                <p className="q">How can an AI system preserve and retrieve knowledge over long interactions while maintaining structure, provenance, and local context?</p>
                <div className="label">NeuralGraph · long-horizon memory · LoCoMo testbed</div>
              </div>
              <div className="track-body">
                <p className="body">
                  LoCoMo is the empirical testbed: ten long conversations, 1,540 questions, answered from memory alone. The
                  campaign ran eighteen experiments on a leakage-free harness and re-scored the same answers under four graders.
                </p>
                <div>
                  <div className="label" style={{ marginBottom: 12 }}>The controlled A/B · same 282 questions, same judge, only retrieval changed</div>
                  <Metrics items={locomoAB} />
                </div>
                <div>
                  <div className="label" style={{ marginBottom: 12 }}>The shipped configuration · conversations 1–5, 744 of 1,540 questions, Gemma lenient judge</div>
                  <Metrics items={locomoSlice} />
                </div>
                <div className="finding">
                  <div className="label signal">The insight</div>
                  <p>Routing memory to the correct local participant and preserving conversational relationships materially improved retrieval and end-to-end performance. Per-agent routing matched oracle routing.</p>
                </div>
                <p className="negatives">
                  <strong>On labels:</strong> the campaign reports call the 282-question set &ldquo;single hop&rdquo; and the 841-question set
                  &ldquo;multi hop&rdquo;, inheriting a swap from earlier code; LoCoMo&rsquo;s own taxonomy names them the other way round. Aggregates are
                  unaffected, so the site names the sets by their LoCoMo category and size instead.
                </p>
                <p className="negatives">
                  <strong>Also found:</strong> the same answers score between 13.5% and 64.9% depending only on who grades them, so any memory
                  benchmark number without a named judge is unfalsifiable. <strong>What did not work:</strong> graph-neighbour expansion,
                  listwise reranking, wider and narrower context, an LLM router, a larger answer model, and two mechanisms that improved
                  recall and hurt answers by flooding the context with related non-answers.
                </p>
              </div>
            </div>

            <div className="track reveal" id="emergence">
              <div className="track-head">
                <div className="label">Track 02 · Emergence</div>
                <h2>How does new knowledge appear?</h2>
                <p className="q">Can distributed agents discover an effect that no individual agent has enough evidence to recognize?</p>
                <div className="label">Collective discovery · enterprise-hierarchy benchmark</div>
              </div>
              <div className="track-body">
                <p className="body">
                  A simulation benchmark: a synthetic enterprise with hidden problems planted in it, at 2,000 to 100,000 people, and
                  eighteen architectures for finding them, from one very large model reading a filtered subset of all notes to a
                  six-level hierarchy of agents mirroring the org chart. The org chart, corpus, routing, propagation, and questioning
                  are executed code; model behaviour is simulated from a capability vector, except where real models were measured
                  directly. The property under test is emergence: the global conclusion is not present in any single local state.
                </p>
                <ul className="track-list">
                  {["independent support", "hierarchy", "incomplete evidence", "adversarial decoys", "information loss", "constrained compute", "aggregation", "organizational scale", "distributed evidence"].map((t) => <li key={t}>{t}</li>)}
                </ul>
                <Metrics items={emergence} />
                <div className="finding">
                  <div className="label signal">The insight</div>
                  <p>A weak signal is invisible one record at a time. Discovery has to be relational, and the value of the hierarchy is in the downward path: questions returning to the sources, not summaries climbing the org chart.</p>
                </div>
                <p className="negatives">
                  <strong>The case against:</strong> if centralising raw text is acceptable, reading 22% of all records in one place finds more
                  hidden problems than the hierarchy does, at 2.6× the compute. The hierarchy earns its cost where sovereign local
                  memory is a requirement, and it delivers confidentiality, independent-support accuracy, and resistance to planted traps.
                  It does not yet deliver weak-signal sensitivity or end-to-end attribution, and the report says so.
                </p>
              </div>
            </div>

            <div className="track reveal" id="recursion">
              <div className="track-head">
                <div className="label">Track 03 · Recursion</div>
                <h2>How does new knowledge change what is learned next?</h2>
                <p className="q">Can a system learn what question to ask next?</p>
                <div className="label">Continual discovery · recursive learning</div>
              </div>
              <div className="track-body">
                <p className="body">
                  The bridge between memory and genuine organizational learning. The system observes, forms hypotheses, identifies
                  its uncertainty, asks targeted questions, verifies the answers, updates what it knows, redistributes the result,
                  and generates better questions. The output of one discovery is the input of the next.
                </p>
                <ul className="track-list">
                  {["information gain", "uncertainty", "contradiction", "temporal change", "knowledge decay", "verification", "lineage", "recursive questioning", "propagation"].map((t) => <li key={t}>{t}</li>)}
                </ul>
                <Metrics items={recursion} />
                <div className="finding">
                  <div className="label signal">The insight</div>
                  <p>Questioning the sources is where discovery comes from: 2% with upward summarisation and downward retrieval, 57% once the system asks. Remove questioning and discovery collapses; keep it and most questions move a conclusion.</p>
                </div>
                <p className="negatives">
                  <strong>Open:</strong> this track is a mechanism inside the emergence benchmark today, not yet a measured track of its own.
                  The ablation shows that asking matters and cannot yet show how much aiming matters; destroying question targeting did
                  not reach significance. The system&rsquo;s weakest point is stale evidence: it accepts 77% of decoys built from chains whose
                  every link was later retracted. Information gain, knowledge decay, contradiction, and temporal change are the next
                  experiments, studied as a systems problem in their own right.
                </p>
              </div>
            </div>

          </div>
        </div>
      </section>

      <section className="section">
        <div className="wrap split reveal">
          <div>
            <div className="label">In deployment</div>
            <h2 className="h2" style={{ marginTop: 16 }}>The same primitive, running.</h2>
          </div>
          <div className="stack">
            <p className="body">{runtime.smoke}</p>
            <p className="body">{runtime.strategic}</p>
            <p className="body">{runtime.scale}</p>
            <p className="label" style={{ marginTop: 8 }}>Every claim above is exercised by an automated test, not asserted in prose.</p>
            <div className="link-row">
              <a className="link" href="https://github.com/anovruzov/NeuralGraph/blob/main/docs/BENCHMARKS.md" rel="noopener">Benchmarks</a>
              <a className="link" href="https://github.com/anovruzov/NeuralGraph/blob/main/docs/MYCELIC_ENTERPRISE.md" rel="noopener">Enterprise report</a>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
