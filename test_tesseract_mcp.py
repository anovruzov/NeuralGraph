"""Test Tesseract 4D retrieval through MCP server."""
import sys
import json
import subprocess
import time
import os
import uuid

def send_request(proc, request, wait=1.0):
    request_str = json.dumps(request) + "\n"
    proc.stdin.write(request_str.encode())
    proc.stdin.flush()
    time.sleep(wait)
    line = proc.stdout.readline().decode().strip()
    return json.loads(line) if line else None

def call_tool(proc, name, args, wait=2.0, id=1):
    resp = send_request(proc, {
        "jsonrpc": "2.0", "id": id, "method": "tools/call",
        "params": {"name": name, "arguments": args}
    }, wait=wait)
    if resp and "result" in resp:
        return resp["result"].get("structuredContent", {})
    return resp

def main():
    env = os.environ.copy()
    env.update({
        'PYTHONPATH': r'C:\Users\anovr\Desktop\MemMachine-main\src',
        'MEMMACHINE_DB': r'C:\Users\anovr\.memmachine\memories.db',
        'OLLAMA_BASE_URL': 'http://localhost:11434',
        'EMBEDDING_MODEL': 'nomic-embed-text'
    })

    print("=" * 70)
    print("   TESSERACT 4D MEMORY - MCP INTEGRATION TEST")
    print("=" * 70)

    proc = subprocess.Popen(
        [r'C:\Users\anovr\Desktop\MemMachine-main\.venv\Scripts\python.exe', '-m', 'memmachine.mcp.server'],
        cwd=r'C:\Users\anovr\Desktop\MemMachine-main',
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
    )

    try:
        # Initialize
        send_request(proc, {
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1.0"}}
        })

        # Use unique session to avoid stale data
        session = f"tesseract_test_{uuid.uuid4().hex[:8]}"
        print(f"\n[SESSION] {session}")

        # Store diverse memories
        memories = [
            ("Last week I went hiking in the Rocky Mountains with my dog.", ["outdoor", "hiking", "pets"]),
            ("I celebrated my birthday in June 2023 at an Italian restaurant.", ["personal", "birthday", "food"]),
            ("My sister Sarah works as a nurse at City Hospital.", ["family", "career"]),
            ("Yesterday I bought a new Tesla Model 3 electric car.", ["purchase", "car", "technology"]),
            ("I'm learning to play guitar and practice every evening.", ["hobby", "music"]),
        ]

        print("\n[STORING 5 MEMORIES]")
        for i, (content, tags) in enumerate(memories, 1):
            result = call_tool(proc, "store_memory", {
                "content": content, "session_key": session, "tags": tags
            }, wait=2, id=i)
            print(f"  [{i}] {content[:50]}...")

        # Test different query types
        print("\n[TESSERACT 4D RETRIEVAL TESTS]")

        tests = [
            # Temporal queries
            ("When did I go hiking?", "temporal", "Rocky Mountains/last week"),
            ("When was my birthday?", "temporal", "June 2023"),
            ("What did I buy yesterday?", "temporal", "Tesla"),

            # Entity queries
            ("What does Sarah do for work?", "entity", "nurse"),
            ("What car do I own?", "entity", "Tesla Model 3"),

            # Multi-hop queries
            ("What are my hobbies?", "multi-hop", "hiking, guitar"),
        ]

        passed = 0
        for q, qtype, expected in tests:
            result = call_tool(proc, "search_semantic", {
                "query": q, "session_key": session, "limit": 3
            }, wait=3, id=100)

            top_result = result.get("results", [{}])[0] if result.get("results") else {}
            content = top_result.get("content", "")[:60]
            score = top_result.get("similarity", 0)

            # Check if expected keyword is in result
            match = any(kw.lower() in content.lower() for kw in expected.split("/"))
            status = "PASS" if match else "FAIL"
            if match:
                passed += 1

            print(f"\n  [{qtype.upper()}] {q}")
            print(f"  Expected: {expected}")
            print(f"  Got: {content}...")
            print(f"  Score: {score:.1%} [{status}]")

        print(f"\n[RESULTS] {passed}/{len(tests)} tests passed")
        print("=" * 70)

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        proc.terminate()
        proc.wait()

if __name__ == "__main__":
    main()
