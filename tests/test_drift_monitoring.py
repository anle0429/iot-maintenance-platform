"""Check declared clock boundaries, fixed grouping semantics and freshness without a simulated native success."""
from copy import deepcopy
from datetime import timedelta
import unittest
from ml.drift_monitoring import window, utc, push_url, exposition, dashboard, FIELDS


def config():
    """Use an explicit fixture replay clock after the frozen training boundary."""
    return {'format': 'iot-offline-drift-clock-v1', 'wall_origin': '2026-10-28T03:00:00Z',
        'event_origin': '2026-11-22T01:00:00Z', 'width_seconds': 3600, 'data_start': '2026-10-01T00:00:00Z',
        'data_end': '2026-11-30T00:00:00Z', 'reference_train_end': '2026-11-12T00:00:00Z',
        'sample_limit': 1024, 'source_sha256': 'a' * 64, 'reference_sha256': 'b' * 64}


class DriftMonitoringTests(unittest.TestCase):
    'Group known-answer checks for drift monitoring.'
    def test_clock_mapping_advances_closed_window_without_using_wall_now(self):
        'Verify clock mapping advances closed window without using wall now.'
        start, end = window(config(), '2026-10-28T03:05:00+00:00')
        self.assertEqual(start.isoformat(), '2026-11-22T00:05:00+00:00')
        self.assertEqual(end - start, timedelta(hours=1))
        self.assertEqual(window(config(), '2026-10-28T03:05:00Z'), (start, end))

    def test_train_and_data_boundaries_are_inclusive_for_half_open_window(self):
        'Verify train and data boundaries are inclusive for half open window.'
        c = config(); c['event_origin'] = '2026-11-12T01:00:00Z'
        self.assertEqual(window(c, c['wall_origin'])[0], utc(c['reference_train_end']))
        c['event_origin'] = '2026-11-12T00:59:59Z'
        with self.assertRaises(ValueError): window(c, c['wall_origin'])
        c['event_origin'] = c['data_end']
        self.assertEqual(window(c, c['wall_origin'])[1], utc(c['data_end']))
        with self.assertRaises(ValueError): window(c, '2026-10-28T03:00:01Z')

    def test_unknown_clock_naive_timestamp_and_bad_sample_counts_fail(self):
        'Verify unknown clock naive timestamp and bad sample counts fail.'
        for name, value in [('sample_limit',31),('sample_limit',2049),('sample_limit',True),
                            ('width_seconds',299),('source_sha256','x'*64),('format','guess')]:
            c=config(); c[name]=value
            with self.subTest(name=name,value=value), self.assertRaises(ValueError): window(c,c['wall_origin'])
        for value in ['2026-10-28T03:00:00','2026-10-28T03:00:00+01:00','2026-10-28T02:59:59Z']:
            with self.subTest(value=value), self.assertRaises(ValueError): window(config(),value)

    def test_publisher_has_one_stable_private_group(self):
        'Verify publisher has one stable private group.'
        self.assertEqual(push_url('http://127.0.0.1:19092/'),'http://127.0.0.1:19092/metrics/job/iot_offline_drift')
        for endpoint in ['http://example.com:19092','http://127.0.0.1:19092/a','http://u:p@127.0.0.1:19092',
                         'http://127.0.0.1:19092?x=1','http://127.0.0.1:19091','https://127.0.0.1:19092']:
            with self.subTest(endpoint=endpoint),self.assertRaises(ValueError):push_url(endpoint)

    def test_gauges_preserve_known_scores_and_separate_two_clocks(self):
        'Verify gauges preserve known scores and separate two clocks.'
        report={'features':{name:{'psi':i/10,'drifted':i>=2} for i,name in enumerate(FIELDS)},
                'unique_rows':32,'drift_detected':True,'drifted_feature_fraction':.75,
                'reference_id':'b'*64,'start':'2026-11-22T00:00:00Z','end':'2026-11-22T01:00:00Z'}
        text=exposition(report,'2026-10-28T03:03:00Z')
        values=[row for row in text.splitlines() if not row.startswith('#')]
        self.assertEqual(len(values),23)
        self.assertIn('iot_offline_drift_psi{feature="temperature_mean_10m"} 0.0',values)
        self.assertIn('iot_offline_drift_psi{feature="prior_failures_24h"} 0.7',values)
        self.assertIn('iot_offline_drift_feature_fraction 0.75',values)
        self.assertIn('iot_offline_drift_last_success_seconds '+str(utc('2026-10-28T03:03:00Z').timestamp()),values)
        self.assertNotIn('window_id=',text);self.assertNotIn('machine_id=',text)
        bad=deepcopy(report);bad['features'][FIELDS[0]]['psi']=float('nan')
        with self.assertRaises(ValueError):exposition(bad,'2026-10-28T03:03:00Z')

    def test_dashboard_shows_missing_data_and_two_event_clock_boundaries(self):
        'Verify dashboard shows missing data and two event clock boundaries.'
        panels=dashboard()['panels'];self.assertEqual(len(panels),6)
        self.assertIn('time()-',panels[3]['targets'][0]['expr'])
        self.assertIn('SIMULATED',panels[4]['title']);self.assertIn('SIMULATED',panels[5]['title'])
        self.assertTrue(all('or vector(0)' not in p['targets'][0]['expr'] for p in panels))
        # Grafana date units require milliseconds; source Prometheus metrics remain seconds.
        for panel in panels[4:]:
            self.assertTrue(panel['targets'][0]['expr'].endswith(' * 1000'))
            self.assertEqual(panel['fieldConfig']['defaults']['unit'],'dateTimeAsIso')
            self.assertEqual(panel['fieldConfig']['defaults']['color'],{'mode':'fixed','fixedColor':'blue'})
        self.assertEqual(panels[2]['fieldConfig']['defaults']['unit'],'none')
        self.assertEqual(panels[3]['fieldConfig']['defaults']['thresholds']['steps'][-1]['value'],600)


if __name__=='__main__':unittest.main()
