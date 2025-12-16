"""Debug MCP server tool calls with full response logging."""
import sys
import json
import subprocess
import time
import os

def send_request(proc, request, wait=1.0):
    """Send a JSON-RPC request and return response."""
    request_str = json.dumps(request) + "\n"
    proc.stdin.write(request_str.encode())
    proc.stdin.flush()
    time.sleep(wait)

    # Read response line
    line = proc.stdout.readline().decode().strip()
    if line:
        return json.loads(line)
    return None

def test_mcp_debug():
    """Debug MCP tool responses."""

    env = os.environ.copy()
    env.update({
        'PYTHONPATH': r'C:\Users\anovr\Desktop\MemMachine-main\src',
        'MEMMACHINE_DB': r'C:\Users\anovr\.memmachine\memories.db',
        'OLLAMA_BASE_URL': 'http://localhost:11434',
        'EMBEDDING_MODEL': 'nomic-embed-text'
    })

    print("=" * 70)
    print("MCP DEBUG TEST")
    print("=" * 70)

    proc = subprocess.Popen(
        [r'C:\Users\anovr\Desktop\MemMachine-main\.venv\Scripts\python.exe', '-m', 'memmachine.mcp.server'],
        cwd=r'C:\Users\anovr\Desktop\MemMachine-main',
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env
    )

    try:
        # 1. Initialize
        print("\n1. Initialize")
        init_resp = send_request(proc, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"}
            }
        })
        print(f"OK: {init_resp['result']['serverInfo']}")

        # 2. Store memory with longer wait
        print("\n2. Store memory")
        store_resp = send_request(proc, {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "store_memory",
                "arguments": {
                    "content": "My favorite programming language is Python. I use it for machine learning and AI projects.",
                    "session_key": "debug_test",
                    "tags": ["python", "programming", "ai"]
                }
            }
        }, wait=3.0)  # Wait longer for embedding

        print(f"Full response: {json.dumps(store_resp, indent=2)[:500]}")

        if "result" in store_resp:
            content = store_resp["result"].get("content", [])
            if content and len(content) > 0:
                text = content[0].get("text", "{}")
                data = json.loads(text)
                memory_id = data.get("memory_id")
                print(f"Memory ID: {memory_id}")

        # 3. Search with more time
        print("\n3. Semantic search")
        time.sleep(2)  # Extra wait for embedding storage

        search_resp = send_request(proc, {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "search_semantic",
                "arguments": {
                    "query": "What programming language do you use?",
                    "session_key": "debug_test",
                    "limit": 5
                }
            }
        }, wait=3.0)

        print(f"Search response: {json.dumps(search_resp, indent=2)[:800]}")

        # 4. Get stats
        print("\n4. Session stats")
        stats_resp = send_request(proc, {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "stats_sessions",
                "arguments": {"session_key": "debug_test"}
            }
        }, wait=1.0)

        print(f"Stats response: {json.dumps(stats_resp, indent=2)[:500]}")

        # 5. Check status
        print("\n5. Check status")
        status_resp = send_request(proc, {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "check_status",
                "arguments": {}
            }
        }, wait=1.0)

        print(f"Status response: {json.dumps(status_resp, indent=2)[:500]}")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        proc.terminate()
        proc.wait()

if __name__ == "__main__":
    test_mcp_debug()
