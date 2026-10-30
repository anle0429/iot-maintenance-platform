"""Restore original historical receipts once before running individual offline evidence checkers."""
import argparse, json
from evidence_bundle import materialize

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true', help='Check the archive and existing receipts without writing missing files')
    parser.add_argument('--check-git-source', action='store_true', help='Also bind every bundled file to its original published Git blob')
    args = parser.parse_args()
    print(json.dumps(materialize(verify_only=args.verify_only, verify_sources=args.check_git_source), indent=2))
