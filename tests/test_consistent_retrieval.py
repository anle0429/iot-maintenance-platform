"""Exercise deliberately permuted vector-search columns and keyed-read failures without loading an encoder."""
from unittest.mock import patch
import unittest
from rag.retrieval import retrieve_consistent
from rag.store import FEATURES


class ConsistentRetrievalTests(unittest.TestCase):
    """Treat native ranked IDs as lookup keys rather than zipping them with independently ordered columns."""
    def test_permuted_search_payload_is_rehydrated_by_exact_identity(self):
        """Two distinct references retain their own text and provenance even if vector-search columns are permuted."""
        rows = {'first': {'chunk_id': ['first'], 'embedding': [[1., 0.]]},
                'second': {'chunk_id': ['second'], 'embedding': [[0., 1.]]}}
        for identifier, row in rows.items():
            for name in FEATURES: row[name] = [identifier + '-' + name]
        class Store:
            """Return independent single-key records rather than a copy of the vector-search response."""
            def get_online_features(self, features, entity_rows):
                """Honor exactly the singleton key requested by the adapter."""
                result = rows[entity_rows[0]['chunk_id']]
                class Result:
                    """Expose the small subset of the Feast response contract used by the adapter."""
                    def to_dict(self):
                        """Return the actual keyed record fixture."""
                        return result
                return Result()
        raw = {'chunk_id': ['first', 'second'], 'text': ['second-text', 'first-text']}
        with patch('rag.retrieval.retrieve', return_value=raw):
            result, trace = retrieve_consistent(Store(), [1., 0.], 2)
        self.assertEqual(result['text'], ['first-text', 'second-text'])
        self.assertEqual(result['chunk_id'], ['first', 'second'])
        self.assertEqual(result['distance'], [1., 0.])
        self.assertEqual(trace['native_search'], raw)

    def test_duplicate_ranked_keys_fail_before_feature_reads(self):
        """Never mask an invalid native candidate set by silently dropping duplicates."""
        with patch('rag.retrieval.retrieve', return_value={'chunk_id': ['same', 'same']}):
            with self.assertRaises(ValueError): retrieve_consistent(None, [1., 0.], 2)
