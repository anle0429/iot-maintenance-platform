"""Keep matched startup comparisons honest when samples are missing, duplicated or unfavorable."""
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('agent_warmup',ROOT/'scripts/benchmark-agent-warmup.py')
benchmark=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(benchmark)


def sample(mode, pair, seconds):
    """Represent independent known measurements without claiming a live actor."""
    return {'role':'retrieval','mode':mode,'pair':pair,'resume_to_http_card_seconds':seconds,
            'actor_uid':mode+str(pair),'template_uid':'same-template','template_spec_sha256':'same-spec'}


class AgentWarmupTests(unittest.TestCase):
    """An honest benchmark can pass validation even if the proposed optimization is slower."""

    def test_actual_card_naming_still_requires_role_url_and_version(self):
        """Native card names use underscores; accepting that name cannot permit a different endpoint or version."""
        card={'name':'iot_retrieval','url':'http://iot-retrieval.iot-agents:8080','version':'0.2.2'}
        self.assertTrue(benchmark.validate_card(card,'retrieval'))
        for field,value in [('name','iot_drift'),('url','http://example.com'),('version','0.1.0')]:
            with self.subTest(field=field):self.assertFalse(benchmark.validate_card({**card,field:value},'retrieval'))

    def test_paired_medians_and_unfavorable_result(self):
        """Report known paired data without filtering out the slower warm configuration."""
        rows=[sample('cold',0,2),sample('warm',0,4),sample('warm',1,6),sample('cold',1,4)]
        self.assertEqual(benchmark.summarize(rows)['retrieval'],{'pairs':2,'cold_median_seconds':3,'warm_median_seconds':5,
                          'cold_over_warm_ratio':0.6,'warm_median_lower':False})

    def test_missing_duplicate_or_different_template_samples_fail(self):
        """Missing counterparts, reused actors and changed templates cannot make a matched experiment."""
        for case in ['missing','duplicate','template','spec','pair']:
            rows=[sample('cold',0,2),sample('warm',0,1)]
            if case=='missing':rows.pop()
            elif case=='duplicate':rows[1]['actor_uid']=rows[0]['actor_uid']
            elif case=='template':rows[1]['template_uid']='other'
            elif case=='spec':rows[1]['template_spec_sha256']='changed'
            else:rows[1]['pair']=1
            with self.subTest(case=case),self.assertRaises(ValueError):benchmark.summarize(rows)

    def test_non_measurements_and_repeated_pairs_fail(self):
        """Reject zero, negative, NaN, Boolean and duplicate-pair timing rows."""
        for value in [0,-1,float('nan'),float('inf'),True]:
            with self.subTest(value=value),self.assertRaises(ValueError):benchmark.summarize([sample('cold',0,2),sample('warm',0,value)])
        rows=[sample('cold',0,2),sample('warm',0,1),sample('cold',0,3),sample('warm',0,2)]
        rows[2]['actor_uid']='third';rows[3]['actor_uid']='fourth'
        with self.assertRaises(ValueError):benchmark.summarize(rows)


if __name__=='__main__':unittest.main()
