import Link from "next/link";
export default function NotFound() {
  return (
    <section className="page-head wrap">
      <div className="label">404</div>
      <h1 className="display">Not found.</h1>
      <div className="link-row"><Link className="link" href="/">Home</Link></div>
    </section>
  );
}
