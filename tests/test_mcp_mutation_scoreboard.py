"""Reject inflated mutation percentages from dropped jobs, timeouts, hidden skips and an incomplete API scope."""
from copy import deepcopy
from pathlib import Path
import importlib.util
import json
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('mcp_mutation_scoreboard', ROOT / 'scripts/summarize-mcp-mutations.py')
CHECKER = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(CHECKER)
MODULES = ['services/mcp_tools/auth.py', 'services/mcp_tools/contracts.py', 'services/mcp_tools/runtime.py',
           'services/mcp_tools/app.py', 'services/mcp_tools/contracts.py']


def fixture_rows(outcomes):
    """Specify five independent work items, including all four original API modules and one extra validator case."""
    initial = []; results = []
    for index, (module, outcome) in enumerate(zip(MODULES, outcomes)):
        work = {'job_id': 'job-' + str(index), 'mutations': [{'module_path': module,
            'operator_name': 'core/NumberReplacer', 'occurrence': index, 'start_pos': [10, 5], 'end_pos': [10, 6]}]}
        initial.append([deepcopy(work), None])
        if outcome == 'pending': value = None
        elif outcome in {'exception', 'skipped', 'no-test'}:
            value = {'worker_outcome': outcome, 'test_outcome': None}
        else:
            value = {'worker_outcome': 'normal', 'test_outcome': 'killed' if outcome == 'timeout' else outcome,
                     'output': 'timeout' if outcome == 'timeout' else 'test execution receipt'}
        results.append([work, value])
    return initial, results


def calculate(initial, results):
    """Persist protocol-shaped fixtures so the real parser and hash calculations are exercised."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory); first = root / 'initial.jsonl'; last = root / 'results.jsonl'
        first.write_text(''.join(json.dumps(row) + '\n' for row in initial), encoding='utf-8')
        last.write_text(''.join(json.dumps(row) + '\n' for row in results), encoding='utf-8')
        return CHECKER.summarize(first, last)


class MutationScoreboardTests(unittest.TestCase):
    """Use independently known ratios and ledger contradictions rather than reproduce a tool's permissive score property."""

    def test_full_kill_scope_is_still_not_deployment_proof(self):
        """Five killed jobs calculate 100 percent but do not authenticate baseline, native or deployment evidence."""
        report = calculate(*fixture_rows(['killed'] * 5))
        self.assertTrue(report['passed']); self.assertEqual(report['strict_score_percent'], 100)
        for name in ['baseline_and_source_authentication_checked', 'deployment_verified', 'coursework_complete']:
            self.assertFalse(report[name])

    def test_exactly_eighty_percent_is_not_greater_than_eighty(self):
        """Four of five kills meet 80 percent and therefore fail the rubric's strict boundary."""
        report = calculate(*fixture_rows(['killed'] * 4 + ['survived']))
        self.assertEqual(report['strict_score_percent'], 80)
        self.assertFalse(report['passed']); self.assertTrue(report['complete_inventory'])

    def test_timeout_and_errors_never_become_kills(self):
        """Retain each non-killed result in the full denominator, even when Cosmic Ray labels a timeout killed."""
        for value, category in [('timeout', 'timeout'), ('incompetent', 'incompetent'), ('exception', 'worker_error'),
                                ('no-test', 'no_test'), ('skipped', 'skipped'), ('pending', 'pending')]:
            with self.subTest(outcome=value):
                report = calculate(*fixture_rows(['killed'] * 4 + [value]))
                self.assertEqual(report['total'], 5); self.assertEqual(report['counts']['killed'], 4)
                self.assertEqual(report['counts'][category], 1); self.assertEqual(report['strict_score_percent'], 80)
                self.assertFalse(report['passed'])

    def test_omitted_jobs_are_rejected_instead_of_removed_from_denominator(self):
        """Dropping the only survivor cannot turn an 80 percent run into 100 percent."""
        initial, results = fixture_rows(['killed'] * 4 + ['survived']); results.pop()
        with self.assertRaisesRegex(ValueError, 'job IDs'): calculate(initial, results)

    def test_relabelled_operator_or_duplicate_job_is_rejected(self):
        """A different mutation or repeated result cannot be substituted for a declared original job."""
        for mode in ['operator', 'duplicate']:
            initial, results = fixture_rows(['killed'] * 5)
            if mode == 'operator': results[0][0]['mutations'][0]['operator_name'] = 'another-operator'
            else: results.append(deepcopy(results[0]))
            with self.subTest(mode=mode), self.assertRaises(ValueError): calculate(initial, results)

    def test_validator_module_cannot_be_dropped(self):
        """A result over three API modules cannot represent the original four-module profile."""
        initial, results = fixture_rows(['killed'] * 5)
        initial = [row for row in initial if 'contracts.py' not in row[0]['mutations'][0]['module_path']]
        results = [row for row in results if 'contracts.py' not in row[0]['mutations'][0]['module_path']]
        with self.assertRaisesRegex(ValueError, 'four-module'): calculate(initial, results)

    def test_inventory_cannot_be_reconstructed_after_results_exist(self):
        """The ledger supplied as initial work must contain no preselected killed results."""
        initial, results = fixture_rows(['killed'] * 5); initial[0][1] = deepcopy(results[0][1])
        with self.assertRaisesRegex(ValueError, 'before mutation results'): calculate(initial, results)

    def test_higher_order_or_unknown_scope_is_not_this_profile(self):
        """Do not compare a compound mutation or another codebase with this first-order API gate."""
        for mode in ['compound', 'scope']:
            initial, results = fixture_rows(['killed'] * 5)
            if mode == 'compound': initial[0][0]['mutations'].append(deepcopy(initial[0][0]['mutations'][0]))
            else: initial[0][0]['mutations'][0]['module_path'] = 'unrelated.py'
            with self.subTest(mode=mode), self.assertRaises(ValueError): calculate(initial, results)
