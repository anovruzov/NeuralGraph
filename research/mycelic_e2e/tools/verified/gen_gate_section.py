"""ARCHITECTURE_AUDIT §4 from each run's arch_gate.json. Usage: python gen_gate_section.py OUT.md RUN_DIR... (holdout dirs: aggregate counts only)."""
import json
import sys
from pathlib import Path

GATES = [f"G{i}" for i in range(1, 11)]
KEYS = [("holders_created", "holders"), ("holders_with_records", "with records"), ("holders_routed", "routed to"), ("holders_activated", "activated"),
        ("questions", "questions"), ("routes", "routes"), ("records_ingested", "records ingested"), ("ingest_rejections", "rejections")]


def main() -> None:
    out, runs = sys.argv[1], [Path(p) for p in sys.argv[2:]]
    lines = ["| run | " + " | ".join(GATES) + " | valid |", "|---|" + "---|" * (len(GATES) + 1)]
    counts = ["| run | " + " | ".join(k[1] for k in KEYS) + " | hyperedges (support / lineage / conflict / discovery) | rank methods (G4) |",
              "|---|" + "---:|" * len(KEYS) + "---|---|"]
    details = []
    for d in runs:
        p = d / "arch_gate.json"
        if not p.exists():
            continue
        g = json.loads(p.read_text())
        st = {k: (v.get("status") or ("pass" if v.get("ok") else "?")) for k, v in g["gates"].items()}
        lines.append(f"| {d.name} | " + " | ".join({"pass": "pass", "fail": "**FAIL**"}.get(st.get(k), st.get(k, "-")) for k in GATES)
                     + f" | {'yes' if g['valid'] else 'no: ' + ', '.join(g['failed'])} |")
        c = g.get("counts", {})
        he = c.get("hyperedges_by_kind", {})
        g4 = str(g["gates"].get("G4", {}).get("detail", ""))
        rm = g4[g4.find("rank_method"):].split(";")[0] if "rank_method" in g4 else "-"
        counts.append(f"| {d.name} | " + " | ".join(str(c.get(k[0], "-")) for k in KEYS)
                      + f" | {he.get('support', 0)} / {he.get('lineage', 0)} / {he.get('conflict', 0)} / {he.get('discovery', 0)} | {rm} |")
        if not d.name.startswith("H-"):
            details.append(f"- **{d.name}**: " + "; ".join(f"{k} {str(g['gates'][k].get('detail', ''))[:140]}" for k in GATES if k in g["gates"]))
    text = "\n".join(lines) + "\n\nKey counts from the same reports:\n\n" + "\n".join(counts) + "\n"
    if details:
        text += "\nGate details (dev runs; truncated to 140 characters per gate):\n\n" + "\n".join(details) + "\n"
    Path(out).write_text(text)
    print("wrote", out)


if __name__ == "__main__":
    main()
