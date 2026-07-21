import sys
from pathlib import Path

def main():
    if len(sys.argv) < 2:
        sys.exit(1)

    pulse_path = Path(sys.argv[1])
    notebook_id = "6f67ad69-1c92-4db4-b06f-f6938a88e4b1" # JARVIS Venture Intelligence
    
    print(f"PREPARED_VENTURE: Ingesting {pulse_path.name} into Notebook {notebook_id}")

if __name__ == "__main__":
    main()
