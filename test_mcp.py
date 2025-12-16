import sys
sys.path.insert(0, "C:/Users/anovr/Desktop/MemMachine-main/src")

try:
    from memmachine.mcp.server import create_server
    print("SUCCESS: MCP Server imports OK")

    # Try to create the server
    server = create_server()
    print(f"SUCCESS: Server created with name: {server.name}")

except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
