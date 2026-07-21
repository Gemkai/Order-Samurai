import sys
from pathlib import Path

def main():
    if len(sys.argv) < 2:
        sys.exit(1)

    pulse_path = Path(sys.argv[1])
    notebook_id = "cd2a8b8b-2729-4cc3-b737-d288d0fa2bda" # JARVIS Security Intelligence
    
    print(f"PREPARED_SECURITY: Ingesting {pulse_path.name} into Notebook {notebook_id}")

if __name__ == "__main__":
    main()
