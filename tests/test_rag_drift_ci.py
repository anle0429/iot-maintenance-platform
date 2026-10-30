"""Exercise cross-release, edited-artifact and counterfactual rejection at the corpus CI boundary."""
from copy import deepcopy
from hashlib import sha256
import json
import unittest
from rag.drift_ci import require_distance_receipt


class DistanceBindingTests(unittest.TestCase):
    """Ensure an allowed mathematical result cannot be copied to a different deployment."""
    def test_production_binding_and_tampers(self):
        """Only the same release/source/image and unchanged production report can pass."""
        identity = {'release': 'llm-rag-jenkins-9', 'source_commit': 'a'*40, 'component': 'rag', 'build_number': '9'}
        build = {'image_id': 'sha256:abc', 'packaged_sha256': {'knowledge/catalog.json': 'catalog'}}
        report = {'production_catalog_sha256': 'catalog', 'candidates': {'production': {'decision': {'allowed': True, 'requires_review': False}}}}
        raw = json.dumps(report).encode()
        receipt = {'phase': 'corpus-distance', 'passed': True, **identity, 'source_digest': 'source',
                   'scenario': 'production', 'image_id': 'sha256:abc', 'report_sha256': sha256(raw).hexdigest()}
        self.assertEqual(require_distance_receipt(receipt, identity, 'source', build, raw), receipt)
        for field, value in [('phase', 'build'), ('passed', False), ('release', 'another'), ('source_digest', 'edited'),
                             ('scenario', 'out-of-domain'), ('image_id', 'wrong'), ('report_sha256', 'edited')]:
            changed = dict(receipt, **{field: value})
            with self.subTest(field=field), self.assertRaises(ValueError): require_distance_receipt(changed, identity, 'source', build, raw)
        for mutation in ['denied', 'foreign', 'catalog']:
            changed = deepcopy(report)
            if mutation == 'denied': changed['candidates']['production']['decision']['allowed'] = False
            elif mutation == 'foreign': changed['candidates']['out-of-domain'] = changed['candidates'].pop('production')
            else: changed['production_catalog_sha256'] = 'edited'
            body = json.dumps(changed).encode(); claimed = dict(receipt, report_sha256=sha256(body).hexdigest())
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): require_distance_receipt(claimed, identity, 'source', build, body)


if __name__ == '__main__': unittest.main()
