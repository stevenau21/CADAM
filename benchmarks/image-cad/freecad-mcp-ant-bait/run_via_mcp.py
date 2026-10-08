"""Send model_ant_bait.py to FreeCAD through the FreeCAD-MCP stdio tool."""
import json
import os
import subprocess
from pathlib import Path

repo = Path(os.environ.get("FREECAD_MCP_REPO", r"F:/projects/3D/apps/freecad-mcp"))
source = Path(__file__).resolve().with_name("model_ant_bait.py")
output_dir = source.parent.resolve()
proc = subprocess.Popen(
    [str(repo / ".venv/Scripts/freecad-mcp.exe"), "--host", "127.0.0.1", "--only-text-feedback"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)


def send(message):
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()


def read_response(request_id):
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("MCP server exited before responding: " + proc.stderr.read())
        response = json.loads(line)
        if response.get("id") == request_id:
            return response


try:
    send({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "freecad-ant-bait-test", "version": "0.1"},
        },
    })
    read_response(1)
    send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    send({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {
            "name": "execute_code",
            "arguments": {
                "code": source.read_text(encoding="utf-8").replace(
                    'OUT = r"F:/projects/3D/freecad-ant-bait-test"',
                    f"OUT = {str(output_dir)!r}",
                    1,
                ),
                "include_screenshot": False,
                "timeout": 1200,
            },
        },
    })
    response = read_response(2)
    print(json.dumps(response, indent=2))
    if response.get("result", {}).get("isError"):
        raise SystemExit("FreeCAD-MCP execute_code reported an error")
finally:
    proc.terminate()
    proc.wait(timeout=15)
