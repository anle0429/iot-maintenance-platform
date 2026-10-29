"""Reject incomplete or inflated observer mutation results independently of the mutation engine's labels."""
from importlib.util import module_from_spec,spec_from_file_location
from pathlib import Path
import unittest

SPEC=spec_from_file_location('observer_quality',Path(__file__).resolve().parents[1]/'scripts/test-llm-observer.py')
QUALITY=module_from_spec(SPEC);SPEC.loader.exec_module(QUALITY)


class ObserverQualityTests(unittest.TestCase):
    """Keep every mutation outcome in scope and distinguish actual test failures from engine errors."""
    def fixture(self,killed=5):
        """Create a complete five-module inventory whose normal exits are independently declared."""
        names=[n.removesuffix('.py').replace('/','.')+'.x_sample__mutmut_1' for n in sorted(QUALITY.MODULES)]
        stats={n:0 for n in ['killed','survived','no_tests','skipped','suspicious','timeout','check_was_interrupted_by_user','segfault']}
        stats.update(killed=killed,survived=5-killed,total=5)
        codes={name:int(i<killed) for i,name in enumerate(names)}
        text='\n'.join(name+': '+('killed' if codes[name] else 'survived') for name in names)
        return stats,text,codes

    def test_complete_normal_test_failures_pass(self):
        """Accept a complete inventory above the strict threshold when every claimed kill has exit one."""
        self.assertEqual(QUALITY.mutation_gate(*self.fixture()),100)

    def test_eighty_percent_is_insufficient(self):
        """The coursework requires more than eighty percent, so equality does not pass."""
        with self.assertRaisesRegex(ValueError,'exceed'):QUALITY.mutation_gate(*self.fixture(4))

    def test_internal_errors_and_missing_exits_cannot_inflate_kills(self):
        """Mutmut labels pytest exit three as killed; this gate explicitly refuses that label."""
        for code in [3,2,37,None,True]:
            with self.subTest(code=code):
                stats,text,codes=self.fixture();codes[next(iter(codes))]=code
                with self.assertRaises(ValueError):QUALITY.mutation_gate(stats,text,codes)
        stats,text,codes=self.fixture();codes.pop(next(iter(codes)))
        with self.assertRaises(ValueError):QUALITY.mutation_gate(stats,text,codes)

    def test_non_test_outcomes_remain_unresolved(self):
        """Timeouts and omitted modules cannot be silently removed from the denominator."""
        stats,text,codes=self.fixture();stats.update(killed=4,timeout=1)
        with self.assertRaises(ValueError):QUALITY.mutation_gate(stats,text,codes)
        stats,text,codes=self.fixture();text=text.split('\n',1)[1]
        with self.assertRaises(ValueError):QUALITY.mutation_gate(stats,text,codes)


if __name__=='__main__':unittest.main()
