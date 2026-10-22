"""Provide generate, inspect and byte-level reproducibility commands for PowerShell users."""

import argparse
from pathlib import Path
import sys

from generator.config import Config
from generator.quality import inspect_source
from generator.simulation import generate
from generator.storage import write_dataset, write_json


def main() -> int:
    """Execute one small-source command and return a nonzero status for invalid evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("generate", help="Create a new immutable source directory")
    create.add_argument("--config", type=Path, default=Path("generator/configs/dev.json"))
    create.add_argument("--output", type=Path, required=True)
    inspect = commands.add_parser("inspect", help="Read persisted files and validate source contracts")
    inspect.add_argument("--source", type=Path, required=True)
    inspect.add_argument("--report", type=Path, required=True)
    compare = commands.add_parser("compare", help="Verify two runs against their manifests and compare all source bytes")
    compare.add_argument("--left", type=Path, required=True)
    compare.add_argument("--right", type=Path, required=True)
    compare.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command != "generate":
            sources = [args.source] if args.command == "inspect" else [args.left, args.right]
            for source in sources:
                if source.resolve() == args.report.resolve() or source.resolve() in args.report.resolve().parents:
                    raise ValueError("report must be outside every source directory")
        if args.command == "generate":
            if args.output.exists():
                raise FileExistsError(f"source already exists: {args.output}")
            config = Config.load(args.config)
            manifest = write_dataset(generate(config), config, args.output)
            print(f"Generated {config.unique_rows:,} unique events; {sum(f['bytes'] for f in manifest['files']):,} payload bytes in {args.output}")
        elif args.command == "inspect":
            report = inspect_source(args.source)
            write_json(args.report, report)
            failed = [name for name, passed in report["checks"].items() if not passed]
            print(f"{'PASS' if report['passed'] else 'FAIL'}: {sum(report['checks'].values())}/{len(report['checks'])} checks; report={args.report}")
            if failed:
                print("Failed: " + ", ".join(failed), file=sys.stderr)
                return 1
        else:
            left, right = inspect_source(args.left), inspect_source(args.right)
            left_paths = {p.relative_to(args.left).as_posix() for p in args.left.rglob("*") if p.is_file()}
            right_paths = {p.relative_to(args.right).as_posix() for p in args.right.rglob("*") if p.is_file()}
            same = left_paths == right_paths and all((args.left / name).read_bytes() == (args.right / name).read_bytes() for name in left_paths)
            report = {"passed": left["passed"] and right["passed"] and same,
                      "all_source_bytes_identical": same, "compared_files": sorted(left_paths),
                      "left_source_valid": left["passed"], "right_source_valid": right["passed"]}
            write_json(args.report, report)
            print(f"Reproducibility {'PASS' if report['passed'] else 'FAIL'}: {len(left_paths)} files; report={args.report}")
            return 0 if report["passed"] else 1
        return 0
    except (ValueError, TypeError, KeyError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
