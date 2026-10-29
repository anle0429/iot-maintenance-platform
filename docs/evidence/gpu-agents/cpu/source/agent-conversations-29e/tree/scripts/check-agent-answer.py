"""Reject incomplete or unsupported compact specialist answers using their original native A2A tool history."""
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agents.answer_acceptance import verify_specialist


def check(path, role, machine_id, window_id):
    """Return a failed report for rejected original bytes without editing them or describing fixtures as native proof."""
    try:
        return verify_specialist(json.loads(path.read_bytes()), role, machine_id=machine_id, window_id=window_id)
    except (ValueError, KeyError, TypeError, OSError) as error:
        return {'passed': False, 'role': role, 'reason': str(error), 'coursework_complete': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('response', type=Path)
    parser.add_argument('--role', choices=['retrieval', 'drift'], required=True)
    parser.add_argument('--machine-id', default='P-001')
    parser.add_argument('--window-id', default='agent-demo-29')
    args = parser.parse_args()
    report = check(args.response, args.role, args.machine_id, args.window_id)
    print(json.dumps(report, indent=2)); raise SystemExit(0 if report['passed'] else 1)
