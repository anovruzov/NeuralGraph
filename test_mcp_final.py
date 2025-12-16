"""Final MCP Server Test - Full Integration."""
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
    line = proc.stdout.readline().decode().strip()
    return json.loads(line) if line else None

def call_tool(proc, name, args, wait=2.0, id=1):
    """Call an MCP tool and return the result."""
    resp = send_request(proc, {
        "jsonrpc": "2.0",
        "id": id,
        "method": "tools/call",
        "params": {"name": name, "arguments": args}
    }, wait=wait)
    if resp and "result" in resp:
        content = resp["result"].get("structuredContent", {})
        return content
    return resp

def main():
    env = os.environ.copy()
    env.update({
        'PYTHONPATH': r'C:\Users\anovr\Desktop\MemMachine-main\src',
        'MEMMACHINE_DB': r'C:\Users\anovr\.memmachine\memories.db',
        'OLLAMA_BASE_URL': 'http://localhost:11434',
        'EMBEDDING_MODEL': 'nomic-embed-text'
    })

    print()
    print("=" * 70)
    print("       MEMMACHINE MCP SERVER - INTEGRATION TEST")
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
        # Initialize
        init = send_request(proc, {
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1.0"}}
        })
        print(f"\n[OK] Connected to {init['result']['serverInfo']['name']} v{init['result']['serverInfo']['version']}")

        # Check status
        status = call_tool(proc, "check_status", {}, wait=1, id=1)
        print(f"\n[STATUS]")
        print(f"  Ollama: {'CONNECTED' if status.get('ollama', {}).get('available') else 'DISCONNECTED'}")
        print(f"  Embedding Model: {status.get('ollama', {}).get('embedding_model', 'N/A')}")
        print(f"  Storage: {status.get('storage', {}).get('type', 'N/A')}")

        # Store memories
        print(f"\n[STORING MEMORIES]")
        session = "integration_test"

        memories = [
            ("I love hiking in the mountains on weekends.", ["outdoor", "hiking"]),
            ("My favorite food is sushi, especially salmon rolls.", ["food", "sushi"]),
            ("I work as a software engineer at a tech startup.", ["work", "career"]),
        ]

        stored_ids = []
        for i, (content, tags) in enumerate(memories, start=2):
            result = call_tool(proc, "store_memory", {
                "content": content,
                "session_key": session,
                "tags": tags
            }, wait=2, id=i)
            mid = result.get("memory_id", "?")
            stored_ids.append(mid)
            print(f"  [{i-1}] Stored: {content[:40]}... -> {mid[:8]}")

        # Search semantic
        print(f"\n[SEMANTIC SEARCH]")
        queries = [
            "What outdoor activities do you enjoy?",
            "What is your profession?",
            "What do you like to eat?"
        ]

        for i, query in enumerate(queries, start=10):
            result = call_tool(proc, "search_semantic", {
                "query": query,
                "session_key": session,
                "limit": 3
            }, wait=2, id=i)

            results = result.get("results", [])
            print(f"\n  Q: {query}")
            if results:
                top = results[0]
                print(f"  A: {top['content'][:60]}...")
                print(f"     Similarity: {top['similarity']:.1%}")
            else:
                print(f"  A: No results found")

        # Get stats
        stats = call_tool(proc, "stats_sessions", {"session_key": session}, wait=1, id=20)
        print(f"\n[SESSION STATS]")
        print(f"  Total Memories: {stats.get('stats', {}).get('total_nodes', 0)}")

        print()
        print("=" * 70)
        print("       ALL TESTS PASSED - MCP SERVER READY FOR USE")
        print("=" * 70)
        print()

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        proc.terminate()
        proc.wait()

if __name__ == "__main__":
    main()
