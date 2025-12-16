"""Test MCP server connection with JSON-RPC protocol."""
import sys
import json
import subprocess
import time
import os

def test_mcp_server():
    """Start MCP server and test basic communication."""

    # Environment setup
    env = os.environ.copy()
    env.update({
        'PYTHONPATH': r'C:\Users\anovr\Desktop\MemMachine-main\src',
        'MEMMACHINE_DB': r'C:\Users\anovr\.memmachine\memories.db',
        'OLLAMA_BASE_URL': 'http://localhost:11434',
        'EMBEDDING_MODEL': 'nomic-embed-text'
    })

    print("Starting MCP server...")
    proc = subprocess.Popen(
        [r'C:\Users\anovr\Desktop\MemMachine-main\.venv\Scripts\python.exe', '-m', 'memmachine.mcp.server'],
        cwd=r'C:\Users\anovr\Desktop\MemMachine-main',
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env
    )

    try:
        # Send initialize request
        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"}
            }
        }

        request_str = json.dumps(init_request) + "\n"
        print(f"Sending: {request_str.strip()}")
        proc.stdin.write(request_str.encode())
        proc.stdin.flush()

        # Wait for response
        time.sleep(2)

        # Check if process is still running
        if proc.poll() is None:
            print("Server is running!")

            # Send tools/list request
            tools_request = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {}
            }
            proc.stdin.write((json.dumps(tools_request) + "\n").encode())
            proc.stdin.flush()

            time.sleep(1)

            # Terminate gracefully
            proc.terminate()
            stdout, stderr = proc.communicate(timeout=5)

            print("\n=== STDOUT ===")
            if stdout:
                for line in stdout.decode().split('\n')[:20]:
                    if line.strip():
                        print(line)
            else:
                print("(empty)")

            print("\n=== STDERR ===")
            if stderr:
                for line in stderr.decode().split('\n')[:10]:
                    if line.strip():
                        print(line)
            else:
                print("(empty)")
        else:
            stdout, stderr = proc.communicate()
            print("Server exited early!")
            print("STDOUT:", stdout.decode()[:500])
            print("STDERR:", stderr.decode()[:500])

    except Exception as e:
        print(f"Error: {e}")
        proc.kill()
        stdout, stderr = proc.communicate()
        print("STDERR:", stderr.decode()[:500] if stderr else "(empty)")

if __name__ == "__main__":
    test_mcp_server()
