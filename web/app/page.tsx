import Link from "next/link";
import SystemVisual from "@/components/SystemVisual";
import Loop from "@/components/Loop";
import Convergence from "@/components/Convergence";

export default function Home() {
  return (
    <>
      <section className="hero wrap">
        <div className="label signal">Continuous discovery for organizations</div>
        <div className="hero-grid">
          <h1 className="display">Discover what your company is missing.</h1>
          <div className="hero-aside">
            <p className="lede">
              Mycelic connects signals across people, agents, and systems to discover what none of them can see alone.
            </p>
            <div className="hero-tags">
              <span>Private by design</span>
              <span>Distributed by default</span>
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="wrap split reveal">
          <div>
            <div className="label">01 · Signals</div>
            <h2 className="h2" style={{ marginTop: 16 }}>Your company already has the signals.</h2>
          </div>
          <div className="stack">
            <p className="lede">
              They are distributed across people, agents, software, infrastructure, and local systems. Each one sees a fragment.
            </p>
            <p className="body">
              Most software stores what a company already knows. Mycelic finds what it does not: it connects weak signals across the
              organization, checks each conclusion against the evidence behind it, and routes what it learns to the people and
              systems that need it.
            </p>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="wrap">
          <div className="split reveal">
            <div>
              <div className="label">02 · Convergence</div>
              <h2 className="h2" style={{ marginTop: 16 }}>What looks insignificant locally can become obvious collectively.</h2>
            </div>
            <div className="stack">
              <p className="lede">
                A weak signal is, by construction, indistinguishable from noise where it is observed. Independence is what turns four
                of them into one finding.
              </p>
              <div className="figure" style={{ marginTop: 12 }}>
                <Convergence />
                <div className="figure-cap">
                  <span className="label">Independent weak signals → one discovery</span>
                  <span className="label">The fifth is noise and stays noise</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="wrap split rev reveal" style={{ alignItems: "center" }}>
          <div style={{ maxWidth: 520 }}>
            <Loop />
          </div>
          <div className="stack">
            <div className="label">03 · The loop</div>
            <h2 className="h2">Every verified discovery is returned to the network.</h2>
            <p className="lede" style={{ marginTop: 8 }}>
              What the organization learns changes what Mycelic looks for next.
            </p>
            <p className="body">
              A discovery does not disappear into a dashboard. It propagates back through the organization with its evidence and
              lineage, changes the state of the network, and so changes the next question. That is what makes Mycelic recursive.
            </p>
            <div className="link-row">
              <Link className="link" href="/technology">How the system works</Link>
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="wrap">
          <div className="section-head reveal">
            <div className="label">04 · The system</div>
            <h2 className="h2" style={{ maxWidth: "18ch" }}>From local sources to the next investigation.</h2>
          </div>
          <div className="reveal">
            <SystemVisual />
          </div>
        </div>
      </section>

      <section className="section">
        <div className="wrap">
          <div className="section-head reveal">
            <div className="label">05 · The distinction</div>
          </div>
          <div className="pair reveal">
            <div>
              <div className="label">Memory</div>
              <h3>What does the organization already know?</h3>
              <p>Storage, search, retrieval. Necessary. A first primitive, not a destination.</p>
            </div>
            <div>
              <div className="label signal">Discovery</div>
              <h3>What has it not connected yet?</h3>
              <p>Hypotheses from distributed evidence, verified at the source, and propagated so the network can act on them.</p>
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="wrap">
          <div className="section-head reveal">
            <div className="label">06 · One example</div>
            <h2 className="h2" style={{ maxWidth: "20ch" }}>None of them is sufficient alone.</h2>
          </div>
          <div className="ex-signals reveal">
            <div>
              <div className="label">Support sees</div>
              <p>A handful of tickets from one region about slow exports. Each closed as resolved.</p>
            </div>
            <div>
              <div className="label">Sales sees</div>
              <p>Two renewals in the same region slipped a quarter. Different reasons given.</p>
            </div>
            <div>
              <div className="label">Engineering sees</div>
              <p>A storage rollback last week that touched one shard. Noted, closed.</p>
            </div>
            <div>
              <div className="label">Telemetry sees</div>
              <p>Export p99 latency drifting up on that shard since the same day.</p>
            </div>
          </div>
          <div className="ex-conclusion reveal">
            <div className="label signal">Mycelic</div>
            <div className="stack">
              <p className="h3">
                Connects the four, forms the hypothesis that the rollback is costing renewals in one region, verifies it against the
                shard&rsquo;s own records and the account history, and returns the verified finding to engineering, sales, and support with its lineage.
              </p>
              <p className="body">The example illustrates the primitive, not the product. The same loop runs over any set of functions, agents, and systems that each hold a fragment.</p>
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="wrap">
          <div className="section-head reveal">
            <div className="label">07 · Research</div>
            <h2 className="h2" style={{ maxWidth: "18ch" }}>How can an organization learn as a system?</h2>
            <p className="lede">Three connected investigations into the same problem. Memory is the first primitive. Emergence is the second. Recursive discovery is the destination.</p>
          </div>
          <div className="prog reveal">
            <Link href="/research#memory">
              <div className="label">01 · Memory</div>
              <h3>How does knowledge survive?</h3>
              <p>Long-horizon memory with structure, provenance, and local context. Tested on LoCoMo under named judges.</p>
              <span className="prog-arrow">Track 01 →</span>
            </Link>
            <Link href="/research#emergence">
              <div className="label">02 · Emergence</div>
              <h3>How does new knowledge appear?</h3>
              <p>Distributed agents finding an effect no single one of them has enough evidence to recognize.</p>
              <span className="prog-arrow">Track 02 →</span>
            </Link>
            <Link href="/research#recursion">
              <div className="label">03 · Recursion</div>
              <h3>How does new knowledge change what is learned next?</h3>
              <p>A system that learns which question to ask next, and whose output becomes its next input.</p>
              <span className="prog-arrow">Track 03 →</span>
            </Link>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="wrap split reveal">
          <div>
            <div className="label">Mycelic</div>
            <h2 className="h2" style={{ marginTop: 16 }}>Runs at the edge. Raw data stays local.</h2>
          </div>
          <div className="stack">
            <p className="lede">Findings travel with their evidence and lineage. Nothing else has to move.</p>
            <div className="link-row">
              <Link className="link" href="/technology">Technology</Link>
              <Link className="link" href="/company">Company</Link>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
