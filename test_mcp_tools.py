"""Test MCP server tool calls - store and retrieve memory."""
import sys
import json
import subprocess
import time
import os

def send_request(proc, request):
    """Send a JSON-RPC request and return response."""
    request_str = json.dumps(request) + "\n"
    proc.stdin.write(request_str.encode())
    proc.stdin.flush()
    time.sleep(0.5)  # Wait for processing

    # Read response line
    line = proc.stdout.readline().decode().strip()
    if line:
        return json.loads(line)
    return None

def test_mcp_tools():
    """Test storing and retrieving memories via MCP."""

    env = os.environ.copy()
    env.update({
        'PYTHONPATH': r'C:\Users\anovr\Desktop\MemMachine-main\src',
        'MEMMACHINE_DB': r'C:\Users\anovr\.memmachine\memories.db',
        'OLLAMA_BASE_URL': 'http://localhost:11434',
        'EMBEDDING_MODEL': 'nomic-embed-text'
    })

    print("=" * 60)
    print("MCP TOOL CALL TEST")
    print("=" * 60)

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
        print("\n1. Initializing MCP connection...")
        init_resp = send_request(proc, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"}
            }
        })
        print(f"   Server: {init_resp['result']['serverInfo']['name']} v{init_resp['result']['serverInfo']['version']}")

        # 2. Store a memory
        print("\n2. Storing a test memory...")
        store_resp = send_request(proc, {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "store_memory",
                "arguments": {
                    "content": "The MCP server connection test was successful on December 15, 2024.",
                    "session_key": "mcp_test",
                    "tags": ["test", "mcp", "connection"]
                }
            }
        })

        if "result" in store_resp:
            result_content = store_resp["result"].get("content", [])
            if result_content:
                stored_data = json.loads(result_content[0].get("text", "{}"))
                memory_id = stored_data.get("memory_id")
                print(f"   Memory stored with ID: {memory_id}")
            else:
                print(f"   Response: {store_resp}")
        else:
            print(f"   Error: {store_resp.get('error', store_resp)}")

        # 3. Search for the memory
        print("\n3. Searching for the memory...")
        time.sleep(1)  # Wait for embedding to be stored
        search_resp = send_request(proc, {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "search_semantic",
                "arguments": {
                    "query": "MCP server test",
                    "session_key": "mcp_test",
                    "limit": 5
                }
            }
        })

        if "result" in search_resp:
            result_content = search_resp["result"].get("content", [])
            if result_content:
                search_data = json.loads(result_content[0].get("text", "{}"))
                memories = search_data.get("memories", [])
                print(f"   Found {len(memories)} memories:")
                for mem in memories[:3]:
                    print(f"   - [{mem.get('similarity', 0):.2f}] {mem.get('content', '')[:60]}...")
            else:
                print(f"   Response: {search_resp}")
        else:
            print(f"   Error: {search_resp.get('error', search_resp)}")

        # 4. Check status
        print("\n4. Checking server status...")
        status_resp = send_request(proc, {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "check_status",
                "arguments": {}
            }
        })

        if "result" in status_resp:
            result_content = status_resp["result"].get("content", [])
            if result_content:
                status_data = json.loads(result_content[0].get("text", "{}"))
                print(f"   Status: {status_data.get('status', 'unknown')}")
                print(f"   Memory Count: {status_data.get('memory_count', 0)}")
                print(f"   Ollama: {status_data.get('ollama_available', False)}")

        print("\n" + "=" * 60)
        print("MCP CONNECTION TEST COMPLETE!")
        print("=" * 60)

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        proc.terminate()
        proc.wait()

if __name__ == "__main__":
    test_mcp_tools()
