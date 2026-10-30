"""Verify filtered history, metadata preservation and original source provenance without restoring removed notes."""
from pathlib import Path
import json
from history_proof import verify_history

if __name__ == '__main__':
    print(json.dumps(verify_history(Path(__file__).resolve().parents[1]), indent=2))
