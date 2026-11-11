import type { Metadata } from "next";
import Metrics from "@/components/Metrics";
import { locomoAB, locomoSlice, locomoLatency, emergence, recursion } from "./metrics";

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
          Three tracks, one problem. Every number is read from a repository artifact and labeled with its scope.
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
                <div>
                  <div className="label" style={{ marginBottom: 12 }}>The controlled A/B · same 282 questions, same judge, only retrieval changed</div>
                  <Metrics items={locomoAB} />
                </div>
                <div>
                  <div className="label" style={{ marginBottom: 12 }}>The shipped configuration · conversations 1–5, 744 of 1,540 questions, Gemma lenient judge</div>
                  <Metrics items={locomoSlice} />
                </div>
                <div>
                  <div className="label" style={{ marginBottom: 12 }}>Speed · the fast path trades the reranker for an 18× speedup</div>
                  <Metrics items={locomoLatency} />
                </div>
                <div className="finding">
                  <div className="label signal">The insight</div>
                  <p>Routing memory to the correct local participant and preserving conversational relationships materially improved retrieval and end-to-end performance. Per-agent routing matched oracle routing.</p>
                </div>
                <p className="negatives">
                  <strong>Labels:</strong> the campaign reports call the 282-question set &ldquo;single hop&rdquo;; LoCoMo&rsquo;s own taxonomy names it the other
                  way round, so the site names sets by category and size. <strong>Also found:</strong> the same answers score between 13.5% and
                  64.9% depending only on who grades them. Ten of eighteen experiments were null or harmful, and are published.
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
                  eighteen architectures for finding them. Model behaviour is simulated from a capability vector except where real
                  models were measured directly. The property under test is emergence: the global conclusion is present in no single
                  local state.
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
                  <strong>The case against:</strong> if centralising raw text is acceptable, reading 22% of all records in one place finds 78%
                  of the hidden problems against the hierarchy&rsquo;s 57%. The distributed design earns its cost where raw data must stay
                  local. It does not yet win on weak-signal sensitivity or lineage, and the report says so.
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
                  Observe, form hypotheses, identify uncertainty, ask targeted questions, verify, update, redistribute, ask better
                  questions. The output of one discovery is the input of the next.
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
                  <strong>Open:</strong> today this is a mechanism inside the emergence benchmark, not yet a measured track of its own. The
                  system&rsquo;s weakest point is stale evidence: it accepts 77% of decoys whose every link was later retracted. Information
                  gain, knowledge decay, and temporal change are the next experiments.
                </p>
              </div>
            </div>

          </div>
        </div>
      </section>

    </>
  );
}
