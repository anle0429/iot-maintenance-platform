"""Check genuine source projection, known finalized window answers and separate CI release boundaries."""
from pathlib import Path
import unittest
from feature_store.staging_contract import fixture,release


class StagingContractTests(unittest.TestCase):
    """Use native retained input values, with no Feast or Redis mocks."""
    def test_exact_source_and_known_five_readings_three_windows(self):
        value=fixture(Path(__file__).resolve().parents[1]);rows=value['windows']
        actual=sorted((r['machine_id'],r['event_timestamp'],r['reading_count'],r['temperature_sum'],r['temperature_mean'],r['vibration_max']) for r in rows)
        self.assertEqual([(r[0],r[2:]) for r in actual],[('P-001',(3,60.0,20.0,3.0)),('P-001',(1,40.0,40.0,4.0)),('P-002',(1,100.0,100.0,10.0))])
        self.assertEqual(sum(r['reading_count'] for r in rows),5)

    def test_job_numbers_and_destinations_cannot_share_a_release(self):
        self.assertNotEqual(release('offline','1'),release('online','1'))
        for mode,build in [('both','1'),('online','0'),('offline','../1'),('offline','01')]:
            with self.subTest(mode=mode,build=build),self.assertRaises(ValueError):release(mode,build)


if __name__=='__main__':unittest.main()
