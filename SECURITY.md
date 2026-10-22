# Security

NeuralGraph is local-first: one process, one SQLite file, on your machine. This page states what the memory
server (`neuralgraph-memory`) enforces, which test checks it, and what it does **not** do.

## Reporting a vulnerability

Use GitHub's private reporting: the **Security** tab of this repository → **Report a vulnerability**. If that
button is not there, open an issue that asks for a private contact and contains no details. Please do not describe
a security problem in a public issue. Include the version (`git rev-parse HEAD`), the command line you ran, and the
smallest request or input that shows the problem.

## What the server enforces

| Control | Behaviour | Where |
|---|---|---|
| Bind address | `serve` binds `127.0.0.1` unless `--host` says otherwise; a non-loopback bind without tokens prints a warning | `serve` in `src/cli.rs` (not covered by a test) |
| MCP token | `--mcp-token` / `NEURALGRAPH_MCP_TOKEN` requires `Authorization: Bearer <token>` on `/mcp`; constant-time comparison | test `test_auth_session_and_json_response` |
| API token | `--api-token` / `NEURALGRAPH_API_TOKEN` does the same for `/api/*` | test `test_api_token_when_configured` |
| | on a non-loopback bind without `--api-token`, `/api/*` is protected with the MCP token | `serve` in `src/cli.rs` (not covered by a test) |
| DNS rebinding | `--allowed-host` restricts accepted `Host` headers (loopback names by default on `127.0.0.1`); `--allowed-origin` restricts `Origin` on `/mcp` | tests `test_cors_allow_list_and_host_check`, `golden.rs` (REST fixtures) |
| CSRF | state-changing requests from a foreign `Origin` are refused, and JSON endpoints require `Content-Type: application/json` | test `test_cross_site_writes_are_refused` |
| CORS | off on `/api/*` unless opted into with `--cors-origin` | test `test_cors_allow_list_and_host_check` |

Paths are under `crates/neuralgraph-memory/`. Run them with `cargo test -p neuralgraph-memory`.

## What leaves your machine

Requests to the model server you configure (`LLM_BASE_URL`; Ollama or LM Studio, normally on the same machine), and
nothing else. There is no telemetry, no account and no call to a hosted service.

## What it does not do

* **No authentication by default.** Without a token, anything that can reach the port can read and write your
  memory. That is acceptable on `127.0.0.1` and not otherwise: set both tokens before binding elsewhere.
* **No TLS.** The server speaks plain HTTP. To expose it beyond your machine, put a TLS-terminating proxy in front.
* **No encryption at rest.** The database (`~/.neuralgraph/chat_memory.db` by default) is a plain SQLite file;
  protect it with your disk encryption and file permissions. Deleting the file deletes the memory.
* **No isolation between users.** One server is one person's memory. Run one server and one database per user.
* **Stored content is what you said.** Memories are extracted from your conversations, so they can contain anything
  you typed, including secrets. `memory_forget` retracts a memory; it is recorded in the audit log.

The full operator guide is [`crates/neuralgraph-memory/README.md` §12](crates/neuralgraph-memory/README.md#12-security).
