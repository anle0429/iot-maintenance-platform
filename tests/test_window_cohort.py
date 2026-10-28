"""Check retained full-source lineage, known prefix answers and rejection before native store construction."""
from pathlib import Path
from hashlib import sha256
import importlib.util,json,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1];spec=importlib.util.spec_from_file_location('window_cohort',ROOT/'scripts/replay-window-cohort.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
PARENT=ROOT/'docs/evidence/day3/stream-report.json';PIN='48fca351b74c88a746611b2e103ac9023cd30bbcad321bf933c597eea33db5bf'


class WindowCohortTests(unittest.TestCase):
    """Use the original full Kafka/Flink report as input while independently checking bounded selection and corruptions."""
    def test_known_first_row_and_complete_cohort(self):
        """Require the known P001 first-window count/sum and all5760keys/57600readings/20machines."""
        first,binding=module.load_windows(PARENT,PIN,1);self.assertEqual(first[0]['machine_id'],'P-001');self.assertEqual(first[0]['reading_count'],10);self.assertAlmostEqual(first[0]['temperature_sum'],485.454,places=9)
        full,binding=module.load_windows(PARENT,PIN,5760);self.assertEqual(len(full),5760);self.assertEqual(binding['selected_readings'],57600);self.assertEqual(binding['selected_machines'],20)

    def test_prefix_is_reproducible_and_rejects_bad_bounds(self):
        """Keep the pilot prefix identical to the full source and reject empty, oversized or boolean counts."""
        full,_=module.load_windows(PARENT,PIN,5760);prefix,binding=module.load_windows(PARENT,PIN,256);self.assertEqual(prefix,full[:256]);self.assertEqual(binding['selected_readings'],2560)
        for n in [0,5761,True]:
            with self.subTest(rows=n),self.assertRaises(ValueError):module.load_windows(PARENT,PIN,n)

    def test_wrong_parent_hash_is_rejected(self):
        """Reject a changed artifact before parsing or creating any store state."""
        with self.assertRaisesRegex(ValueError,'hash differs'):module.load_windows(PARENT,'0'*64,256)

    def test_duplicate_keys_and_incomplete_source_are_rejected(self):
        """Reject complete-looking metadata with duplicated windows or a missing source window."""
        original=json.loads(PARENT.read_bytes())
        for kind in ['duplicate','missing']:
            changed=json.loads(json.dumps(original))
            if kind=='duplicate':changed['generated']['windows'][1]=changed['generated']['windows'][0]
            else:changed['generated']['windows'].pop()
            with tempfile.TemporaryDirectory() as d:
                p=Path(d)/'report.json';raw=json.dumps(changed).encode();p.write_bytes(raw)
                with self.subTest(kind=kind),self.assertRaises(ValueError):module.load_windows(p,sha256(raw).hexdigest(),256)
