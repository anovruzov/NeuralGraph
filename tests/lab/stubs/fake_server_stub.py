"""A stand-in for the model server binary: a Python launcher with its configuration baked in.

:func:`write_launcher` writes an executable file (shebang ``#!<sys.executable>``) that puts the repository root on
``sys.path`` and calls :func:`main` with the code-owned argv and the baked-in config. It starts fast (only the
standard library is imported until the first chat) and records every invocation's argv and environment as
``<record_dir>/argv-<n>.json`` and ``environ-<n>.json``.

It accepts exactly the flags the lab passes (an unknown flag prints ``error: invalid argument: <flag>`` and exits 1;
``--version`` prints ``version: 0 (stub)``) and serves ``/health`` (503 ``health_503`` times, then 200), ``/props``
(``n_ctx = c // np``), ``/v1/models``, ``/metrics``, ``/apply-template`` (a tiny template), ``/tokenize`` (one token per
four characters, plus one) and ``/v1/chat/completions`` (plain and SSE with usage). A chat is answered by schema name:
with ``config["pack"]`` by the lab's responder, else with fixed schema-valid replies; an unknown name gets 400.
Every reply carries ``X-Mycelic-Fake: 1`` unless ``no_fake_header``.

Faults (config keys; counters that must survive a restart live in ``record_dir``): ``crash_on_start``,
``never_healthy``, ``health_delay_s``, ``bind_fail`` (times; exits 1 with the real bind error line), ``reject_flag``,
``exit_after_chats`` with ``exit_times`` (SIGKILL itself once that many chats were answered), ``unhealthy_after_chats``,
``wrong_alias``, ``wrong_n_ctx``, ``wrong_slots``, ``wrong_model_path``, ``finish_length`` (task names),
``reasoning_content``, ``http_error`` (``{task: status}``), ``nondeterministic``, ``chat_delay_s`` (each chat waits
that long before it is answered) and ``version_fail``.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
VALUE_FLAGS = ("-m", "--alias", "--host", "--port", "-t", "-tb", "-np", "-c", "--seed", "--cache-ram", "--fit",
               "--reasoning", "--reasoning-budget")
BARE_FLAGS = ("--offline", "--no-webui", "--metrics", "--jinja", "--no-jinja", "--version")
E3_NAMES = ("e3_extraction_like", "e3_short_answer")


def write_launcher(path: Path, config: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n"
                    "import json, sys\n"
                    f"sys.path.insert(0, {str(ROOT)!r})\n"
                    "from tests.lab.stubs.fake_server_stub import main\n"
                    f"sys.exit(main(sys.argv[1:], json.loads({json.dumps(config)!r})))\n", encoding="utf-8")
    path.chmod(0o755)
    return path


# --------------------------------------------------------------------------------------------------- counters

def _record(record_dir: Path, kind: str, obj: Any) -> None:
    for n in range(10000):
        try:
            fd = os.open(record_dir / f"{kind}-{n}.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        return


def _bump(record_dir: Path, name: str) -> int:
    """Increment a counter file and return its value before the increment."""
    path = record_dir / f"{name}.count"
    value = int(path.read_text()) if path.exists() else 0
    path.write_text(str(value + 1))
    return value


def _count(record_dir: Path, name: str) -> int:
    path = record_dir / f"{name}.count"
    return int(path.read_text()) if path.exists() else 0


# --------------------------------------------------------------------------------------------------- the server

class _State:
    def __init__(self, args: dict[str, str], config: dict[str, Any]) -> None:
        self.args = args
        self.config = config
        self.record_dir = Path(config["record_dir"])
        self.t0 = time.monotonic()
        self.health_calls = 0
        self.chats = 0
        self.lock = threading.Lock()
        self.responder: Any = None

    def reply_for(self, name: str, request: dict[str, Any]) -> dict[str, Any] | None:
        if self.config.get("pack"):
            with self.lock:
                if self.responder is None:
                    from lab.responder import Responder
                    from mycelic.collective.packs.loader import load_pack
                    self.responder = Responder(load_pack(self.config["pack"]))
            try:
                return self.responder(request)
            except Exception:  # noqa: BLE001 - the stub answers 400 to anything its responder refuses
                return None
        if name == "e3_extraction_like":
            return {"facts": ["the text lists routine equipment checks"]}
        if name == "e3_short_answer":
            return {"answer": "The text is routine maintenance filler."}
        if name == "extract_claims":
            return {"claims": []}
        if name == "judge_record":
            return {"mentions_entity": "unclear", "describes_predicate": "unclear"}
        if name.startswith("judge_candidate"):
            return {"score": 0}
        return None


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    state: _State

    def handle_error(self, request: Any, client_address: Any) -> None:
        pass


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "server-stub"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - BaseHTTPRequestHandler's signature
        pass

    def _json(self, status: int, obj: Any) -> None:
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if not self.server.state.config.get("no_fake_header"):
            self.send_header("X-Mycelic-Fake", "1")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length)) if length else None
        except ValueError:
            return None

    def do_GET(self) -> None:
        state = self.server.state
        config, args = state.config, state.args
        if self.path == "/health":
            with state.lock:
                state.health_calls += 1
                calls = state.health_calls
                unhealthy = (config.get("unhealthy_after_chats") is not None
                             and state.chats >= config["unhealthy_after_chats"])
            loading = (config.get("never_healthy") or calls <= config.get("health_503", 1)
                       or time.monotonic() - state.t0 < config.get("health_delay_s", 0) or unhealthy)
            return self._json(503 if loading else 200, {"status": "loading model" if loading else "ok"})
        np_ = int(args.get("-np", "1"))
        if self.path == "/props":
            return self._json(200, {
                "default_generation_settings": {"n_ctx": config.get("wrong_n_ctx", int(args.get("-c", "0")) // np_)},
                "total_slots": config.get("wrong_slots", np_),
                "model_path": config.get("wrong_model_path", args.get("-m")),
                "build_info": f"{config.get('tag', 'b0')}-stub", "model_alias": args.get("--alias")})
        if self.path == "/v1/models":
            entry: dict[str, Any] = {"id": "wrong-alias" if config.get("wrong_alias") else args.get("--alias"),
                                     "object": "model"}
            if not config.get("no_fake_header"):
                entry["fake"] = True
            return self._json(200, {"object": "list", "data": [entry]})
        if self.path == "/metrics":
            data = b"stub_requests_total 0\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return None
        return self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        state = self.server.state
        body = self._body()
        if self.path == "/apply-template":
            messages = body.get("messages") if isinstance(body, dict) else None
            if not isinstance(messages, list):
                return self._json(400, {"error": "messages"})
            prompt = "".join(f"<|{m.get('role')}|>\n{m.get('content')}\n" for m in messages) + "<|assistant|>\n"
            return self._json(200, {"prompt": prompt})
        if self.path == "/tokenize":
            content = body.get("content") if isinstance(body, dict) else None
            if not isinstance(content, str):
                return self._json(400, {"error": "content"})
            return self._json(200, {"tokens": list(range(len(content) // 4 + 1))})
        if self.path == "/v1/chat/completions":
            return self._chat(state, body if isinstance(body, dict) else {})
        return self._json(404, {"error": "not found"})

    def _chat(self, state: _State, request: dict[str, Any]) -> None:
        config, args = state.config, state.args
        with state.lock:
            answered = state.chats
            state.chats += 1
        _record(state.record_dir, "chat", request)
        if config.get("chat_delay_s"):
            time.sleep(config["chat_delay_s"])
        limit = config.get("exit_after_chats")
        if limit is not None and answered >= limit and _count(state.record_dir, "exit") < config.get("exit_times", 1):
            _bump(state.record_dir, "exit")
            os.kill(os.getpid(), signal.SIGKILL)
        fmt = request.get("response_format")
        spec = fmt.get("json_schema") if isinstance(fmt, dict) else None
        name = spec.get("name") if isinstance(spec, dict) else None
        if not isinstance(name, str):
            return self._json(400, {"error": "response_format"})
        if name in config.get("http_error", {}):
            return self._json(config["http_error"][name], {"error": "scripted"})
        reply = state.reply_for(name, request)
        if reply is None:
            return self._json(400, {"error": "unknown schema"})
        content = json.dumps(reply)
        if config.get("nondeterministic"):
            content += " " * (answered % 2 + 1)
        finish = "stop"
        if name in config.get("finish_length", []):
            content, finish = content[: len(content) // 2], "length"
        model = "wrong-alias" if config.get("wrong_alias") else args.get("--alias")
        prompt_tokens = sum(len(str(m.get("content"))) for m in request.get("messages", [])) // 4 + 1
        usage = {"prompt_tokens": prompt_tokens, "completion_tokens": len(content) // 4 + 1}
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        message: dict[str, Any] = {"role": "assistant", "content": content}
        if config.get("reasoning_content"):
            message["reasoning_content"] = "thinking about the reply"
        if request.get("stream") is True:
            return self._stream(request, model, content, finish, usage)
        self._json(200, {"id": "chatcmpl-stub", "object": "chat.completion", "created": 0, "model": model,
                         "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": usage})

    def _stream(self, request: dict[str, Any], model: str, content: str, finish: str, usage: dict[str, int]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        if not self.server.state.config.get("no_fake_header"):
            self.send_header("X-Mycelic-Fake", "1")
        self.end_headers()

        def event(obj: Any) -> None:
            data = obj if isinstance(obj, bytes) else json.dumps(obj).encode("utf-8")
            self.wfile.write(b"data: " + data + b"\n\n")
            self.wfile.flush()

        def chunk(delta: dict[str, Any], reason: str | None = None) -> dict[str, Any]:
            return {"id": "chatcmpl-stub", "object": "chat.completion.chunk", "created": 0, "model": model,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}

        pieces = [content[i:i + 8] for i in range(0, len(content), 8)] or [""]
        for i, piece in enumerate(pieces):
            event(chunk({"role": "assistant", "content": piece} if i == 0 else {"content": piece}))
        event(chunk({}, finish))
        options = request.get("stream_options")
        if isinstance(options, dict) and options.get("include_usage") is True:
            event({"id": "chatcmpl-stub", "object": "chat.completion.chunk", "created": 0, "model": model,
                   "choices": [], "usage": usage})
        event(b"[DONE]")


# --------------------------------------------------------------------------------------------------- main

def _parse(argv: list[str], config: dict[str, Any]) -> tuple[dict[str, str], str | None]:
    args: dict[str, str] = {}
    i = 0
    while i < len(argv):
        flag = argv[i]
        if flag == config.get("reject_flag") or (flag not in VALUE_FLAGS and flag not in BARE_FLAGS):
            return args, flag
        if flag in VALUE_FLAGS:
            if i + 1 >= len(argv):
                return args, flag
            args[flag] = argv[i + 1]
            i += 2
        else:
            args[flag] = ""
            i += 1
    return args, None


def main(argv: list[str], config: dict[str, Any]) -> int:
    record_dir = Path(config["record_dir"])
    record_dir.mkdir(parents=True, exist_ok=True)
    _record(record_dir, "argv", argv)
    _record(record_dir, "environ", dict(os.environ))
    args, bad = _parse(argv, config)
    if bad is not None:
        print(f"error: invalid argument: {bad}", file=sys.stderr, flush=True)
        return 1
    if "--version" in args:
        if config.get("version_fail"):
            print("error: the stub was told to fail --version", file=sys.stderr, flush=True)
            return 1
        print("version: 0 (stub)", flush=True)
        return 0
    print("stub: loading model", flush=True)
    if config.get("crash_on_start"):
        print("stub: fatal: failed to load the model file", flush=True)
        return 3
    port = int(args.get("--port", "0"))
    if _count(record_dir, "bind_fail") < config.get("bind_fail", 0):
        _bump(record_dir, "bind_fail")
        print(f"couldn't bind HTTP server socket, hostname: {args.get('--host')}, port: {port}", flush=True)
        return 1
    print("load_backend: loaded CPU backend from libstub-base.so", flush=True)
    print(f"system_info: n_threads = {args.get('-t')} (n_threads_batch = {args.get('-tb')}) | STUB = 1", flush=True)
    server = _Server((args.get("--host", "127.0.0.1"), port), _Handler)
    server.state = _State(args, config)
    print(f"main: server is listening on http://{args.get('--host')}:{port}", flush=True)
    server.serve_forever(poll_interval=0.05)
    return 0
