import os
import json
import sys
from pathlib import Path

# NOTE: This script is designed to be called by the Antigravity Agent.
# It uses the 'notebooklm' MCP tools via the system's tool-calling interface.
# Since direct 'mcp' tool calls from Python requires the MCP client, 
# and the Agent IS the client, this script will primarily prepare the payload
# and signal the Agent to perform the ingestion.

def main():
    if len(sys.argv) < 2:
        print("Usage: python ingest_intelligence.py <pulse_file_path>")
        sys.exit(1)

    pulse_path = Path(sys.argv[1])
    if not pulse_path.exists():
        print(f"Error: Pulse file not found at {pulse_path}")
        sys.exit(1)

    notebook_id = "72d26b76-8394-459d-9695-0bc80c7e61e0" # JARVIS Strategic Intelligence
    
    print(f"PREPARED: Ingesting {pulse_path.name} into Notebook {notebook_id}")
    # The Agent will catch this 'PREPARED' signal and execute the MCP tool call.

if __name__ == "__main__":
    main()
