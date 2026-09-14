# The fabric: agents' memories as peers

`NeuralGraph/mcp/fabric.py` turns independent NeuralGraph memory servers into
a collective. Each agent keeps its own store. Nothing is merged, replicated
or synchronized. What crosses between peers is the coordination layer's typed
contracts (`ClaimEnvelope`, `RetrievalTrace`, `VerificationResult`), policy
filtered on the way out: private memories cross as payload-free denials.

## Transport

Peers are reached over the Model Context Protocol on stdio, each in its own
process (`NeuralGraph/coordination/mcp_peer.py::McpPeerAdapter`). The peer
side is the ordinary memory server with three fabric tools:
`describe_capability`, `export_claims(query, keys)`, `verify_support`. The
orchestrator side rebuilds every claim and trace from JSON through the
contracts' own validation, so nothing a peer sends is trusted unchecked.

A dead peer is a denial trace with `retrieval_operator="transport_failure"`,
never an exception. One retry reconnects; because a peer issues the same
claim ids for the same memories, a retried export deduplicates in the
coordinator. `test_mcp_fabric_transport.py` kills a peer with SIGKILL
mid-session and shows the collective still answering after the reconnect,
from the peer's store on disk.

NATS/JetStream remains unimplemented. The process boundary that exists is
MCP over stdio, which is also what Claude Code and Codex already speak.

## Collective recall

```
fabric.collective_recall("team environment", keys=["env.staging_db", "team.standup"])
```

- The bus supplies a route hint (peers that produced selected evidence for a
  similar query before).
- Every routed peer exports keyed claims (`slot = key`); the coordinator
  drops re-delivered duplicates, composes one value per key, and scores
  fragility: unique lineage roots, unique failure domains, and the
  **minimal failure-domain cut**, the number of domains whose loss forgets
  the answer.
- If a key is missing and the route was bounded, the repair planner runs
  the verification handshake against peers outside the route, excluding
  lineage roots known to be compromised, and re-executes with the first peer
  that has independent support.
- Without keys, it is federated recall: hits from all peers merged by
  lexical fingerprint, each with its producers, roots, domains, replica count
  and independent-domain count.

Every recall is appended to the hash-chained memory bus, persisted as JSON
lines when a path is configured.

## Forecast (the pre-mortem oracle on real memories)

```
fabric.forecast("team environment", keys=[...])
```

For every peer, mask its route; for every declared failure domain, mask
every lineage root seen in that domain (the benchmark's worst-single-domain
intervention); re-run collective recall with repair; report which keys are
lost. Masks live in the orchestrator's `FailureInjector` and are cleared
after each probe. No peer store is touched. The pinned demo shows a
same-domain replica correctly counted as no protection and an independent
domain counted as protection.

## As an MCP server

`python3.11 -m NeuralGraph.mcp.fabric_server` with `NEURALGRAPH_FABRIC`
pointing at a peers file (see the module docstring) exposes `peers`,
`collective_recall` and `forecast_forgetting` to Claude Code or Codex. An
agent can then ask the collective when its own memory has nothing.

## Pinned demo

`python3.11 -m NeuralGraph.mcp.fabric --demo` runs four in-process agents in
three failure domains with deterministic ids and clock and writes
`NeuralGraph/mcp/artifacts/fabric_demo.json`, byte-reproducible and checked
by `test_mcp_fabric.py`. It records: a three-key answer no single agent
holds; the forecast naming exactly one agent and one domain whose loss
forgets a key; a private key asked for and denied without payload; agent-b
offline and the collective still complete; an intact bus; and a
`private_memory_leaked: false` check over every output, trace and bus entry.

## Boundaries kept

- Peers' stores are never read across the boundary and never modified by the
  orchestrator; failure injection is a reversible mask at the orchestrator.
- Failure domains are the ones peers declare; nothing is inferred.
- `contracts.py` is not widened: keys travel through a fabric-owned lookup,
  not a new request field.
- Peer learning (accepting another peer's claim into one's own store) is not
  enabled; `propose_learning` is rejected with that reason.
