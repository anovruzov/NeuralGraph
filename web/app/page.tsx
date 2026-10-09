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
            <div className="label">01</div>
            <h2 className="h2" style={{ marginTop: 16 }}>Your company already has the signals.</h2>
          </div>
          <p className="lede">
            They are distributed across people, agents, software, infrastructure, and local systems. Most software stores what a
            company already knows. Mycelic finds what it does not.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="wrap split reveal">
          <div>
            <div className="label">02</div>
            <h2 className="h2" style={{ marginTop: 16 }}>What looks insignificant locally can become obvious collectively.</h2>
          </div>
          <div className="figure">
            <Convergence />
            <div className="figure-cap">
              <span className="label">Independent weak signals → one discovery</span>
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="wrap split rev reveal" style={{ alignItems: "center" }}>
          <div style={{ maxWidth: 480 }}>
            <Loop />
          </div>
          <div className="stack">
            <div className="label">03</div>
            <h2 className="h2">Every verified discovery is returned to the network.</h2>
            <p className="lede" style={{ marginTop: 8 }}>
              What the organization learns changes what Mycelic looks for next.
            </p>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="wrap">
          <div className="section-head reveal">
            <div className="label">04</div>
            <h2 className="h2" style={{ maxWidth: "18ch" }}>From local sources to the next investigation.</h2>
          </div>
          <div className="reveal">
            <SystemVisual />
          </div>
        </div>
      </section>

      <section className="section">
        <div className="wrap">
          <div className="pair reveal">
            <div>
              <div className="label">Memory</div>
              <h3>What does the organization already know?</h3>
            </div>
            <div>
              <div className="label signal">Discovery</div>
              <h3>What has it not connected yet?</h3>
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="wrap">
          <div className="section-head reveal">
            <div className="label">05</div>
            <h2 className="h2" style={{ maxWidth: "20ch" }}>None of them is sufficient alone.</h2>
          </div>
          <div className="ex-signals reveal">
            <div><div className="label">Support sees</div><p>A few tickets from one region about slow exports.</p></div>
            <div><div className="label">Sales sees</div><p>Two renewals in that region slipped a quarter.</p></div>
            <div><div className="label">Engineering sees</div><p>A storage rollback last week on one shard.</p></div>
            <div><div className="label">Telemetry sees</div><p>Export latency drifting up on that shard since the same day.</p></div>
          </div>
          <div className="ex-conclusion reveal">
            <div className="label signal">Mycelic</div>
            <p className="h3">
              Connects them, forms a hypothesis, verifies it against the shard&rsquo;s own records and the account history, and returns
              the verified finding to engineering, sales, and support with its lineage.
            </p>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="wrap">
          <div className="section-head reveal">
            <div className="label">Research</div>
            <h2 className="h2" style={{ maxWidth: "18ch" }}>How can an organization learn as a system?</h2>
          </div>
          <div className="prog reveal">
            <Link href="/research#memory">
              <div className="label">01 · Memory</div>
              <h3>How does knowledge survive?</h3>
            </Link>
            <Link href="/research#emergence">
              <div className="label">02 · Emergence</div>
              <h3>How does new knowledge appear?</h3>
            </Link>
            <Link href="/research#recursion">
              <div className="label">03 · Recursion</div>
              <h3>How does new knowledge change what is learned next?</h3>
            </Link>
          </div>
          <div className="link-row reveal">
            <Link className="link" href="/research">Research</Link>
            <Link className="link" href="/technology">Technology</Link>
          </div>
        </div>
      </section>
    </>
  );
}
