"""Compute a strict MCP mutation score from the complete original Cosmic Ray inventory and its unfiltered results."""
from collections import Counter
from hashlib import sha256
from pathlib import Path
import argparse
import json

ROOT = Path(__file__).resolve().parents[1]
MODULES = {'services/mcp_tools/auth.py', 'services/mcp_tools/contracts.py',
           'services/mcp_tools/runtime.py', 'services/mcp_tools/app.py'}
CATEGORIES = ('killed', 'survived', 'timeout', 'incompetent', 'worker_error', 'no_test', 'skipped', 'pending')


def ledger(path, initial=False):
    """Read every first-order work item exactly once, rejecting duplicates, unexpected scope and a retrospective inventory."""
    values = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, list) or len(row) != 2:
            raise ValueError('Each Cosmic Ray line must contain one work item and one result')
        work, result = row
        if not isinstance(work, dict) or not isinstance(work.get('job_id'), str) or not work['job_id']:
            raise ValueError('The work item has no attributable job ID')
        if work['job_id'] in values:
            raise ValueError('Duplicate mutation job ID')
        mutations = work.get('mutations', [])
        if len(mutations) != 1:
            raise ValueError('The declared profile requires exactly one mutation per job')
        module = mutations[0].get('module_path', '').replace('\\', '/')
        if module not in MODULES:
            raise ValueError('Mutation module is outside the declared four-module API scope')
        if initial and result is not None:
            raise ValueError('The initial inventory must be captured before mutation results exist')
        values[work['job_id']] = (work, result, module)
    if not values or {entry[2] for entry in values.values()} != MODULES:
        raise ValueError('The complete four-module initial inventory is required')
    return values


def category(result):
    """Keep timeout, tool errors, skipped work and pending work out of the killed numerator without dropping their denominator."""
    if result is None:
        return 'pending'
    if not isinstance(result, dict):
        raise ValueError('A mutation result must be an object or null')
    worker = result.get('worker_outcome'); outcome = result.get('test_outcome')
    if worker in {'exception', 'abnormal'}:
        return 'worker_error'
    if worker == 'no-test':
        return 'no_test'
    if worker == 'skipped':
        return 'skipped'
    if worker != 'normal':
        raise ValueError('Unknown worker outcome')
    if outcome == 'incompetent':
        return 'incompetent'
    if outcome == 'killed':
        return 'timeout' if result.get('output', '').strip() == 'timeout' else 'killed'
    if outcome == 'survived':
        return 'survived'
    raise ValueError('Unknown or missing normal test outcome')


def summarize(inventory, results):
    """Compare the exact initial work ledger with every result before applying the strict score threshold of more than 80 percent."""
    initial = ledger(inventory, initial=True); observed = ledger(results)
    if set(initial) != set(observed):
        raise ValueError('Result job IDs differ from the initial inventory; omitted work cannot improve the score')
    counts = Counter({name: 0 for name in CATEGORIES}); modules = {name: Counter() for name in sorted(MODULES)}
    for identity, (expected, _, module) in initial.items():
        work, result, actual_module = observed[identity]
        if work != expected or module != actual_module:
            raise ValueError('A mutation descriptor changed after initialization')
        name = category(result); counts[name] += 1; modules[module][name] += 1
    total = len(initial)
    complete = counts['pending'] == counts['skipped'] == 0
    accepted = complete and counts['killed'] * 5 > total * 4
    return {'passed': accepted, 'mutation_gate_passed': accepted, 'total': total, 'counts': dict(counts),
        'strict_score_percent': 100 * counts['killed'] / total, 'required_score_percent_exclusive': 80,
        'complete_inventory': complete, 'module_counts': {k: dict(v) for k, v in modules.items()},
        'inventory_sha256': sha256(inventory.read_bytes()).hexdigest(),
        'results_sha256': sha256(results.read_bytes()).hexdigest(),
        'baseline_and_source_authentication_checked': False, 'deployment_verified': False, 'coursework_complete': False,
        'scope': 'Arithmetic and exact work-ledger reconciliation only. All generated work remains in the denominator. Baseline, source seals, process state and native execution need separate evidence.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True); parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(); report = summarize(args.inventory, args.results)
    if args.output:
        target = args.output.resolve()
        if not target.is_relative_to(ROOT / 'outputs') or target.exists():
            raise ValueError('Write a fresh report under outputs without replacing earlier results')
        target.parent.mkdir(parents=True, exist_ok=True); target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2)); raise SystemExit(0 if report['passed'] else 1)
