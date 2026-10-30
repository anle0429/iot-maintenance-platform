"""Check exact MMD identities, resource boundaries and document weighting independently of a downloaded model."""
from copy import deepcopy
import math
import unittest
from rag.corpus_drift import decide, document_vectors, mmd_squared, scenario_documents


class CorpusDistanceTests(unittest.TestCase):
    """Use known unit vectors to test mathematical contracts rather than encode a second implementation."""
    def test_exact_singletons_and_symmetry(self):
        """Orthogonal singletons have the analytic squared norm 2(1-exp(-1))."""
        a = [[1., 0.]]; b = [[0., 1.]]
        self.assertAlmostEqual(mmd_squared(a, b, 1), 2*(1-math.exp(-1)), places=14)
        self.assertEqual(mmd_squared(a, b, 1), mmd_squared(b, a, 1))
        self.assertEqual(mmd_squared(a, a, 1), 0.)

    def test_permutations_and_duplicate_entire_population(self):
        """Changing order or repeating the entire empirical population cannot change its distribution."""
        a = [[1., 0.], [0., 1.]]; b = [[-1., 0.]]
        self.assertEqual(mmd_squared(a, list(reversed(a)), 1), 0.)
        self.assertAlmostEqual(mmd_squared(a, b, 1), mmd_squared(a*2, b*3, 1))

    def test_reject_invalid_vector_and_bandwidth(self):
        """Empty, malformed, nonfinite, nonunit and excessive inputs fail before arithmetic."""
        for bad in [[], [[1.]], [[math.nan, 0]], [[True, 0]], [[0., 0.]], [[1., 0.]]*129]:
            with self.subTest(bad=str(bad)[:30]), self.assertRaises(ValueError): mmd_squared([[1., 0.]], bad, 1)
        for sigma in [0, -1, math.inf, math.nan, True]:
            with self.subTest(sigma=sigma), self.assertRaises(ValueError): mmd_squared([[1., 0.]], [[1., 0.]], sigma)

    def test_review_boundary_and_invalid_tolerance(self):
        """Equality passes; a smaller predeclared tolerance requires review."""
        a = {'documents': [{'vector': [1., 0.]}]}; b = {'documents': [{'vector': [0., 1.]}]}
        value = mmd_squared([[1., 0.]], [[0., 1.]], 1)
        self.assertTrue(decide(a, b, {'sigma': 1, 'review_mmd_squared': value})['allowed'])
        self.assertTrue(decide(a, b, {'sigma': 1, 'review_mmd_squared': value-.0001})['requires_review'])
        for tolerance in [True, 0, -1, 2, math.nan, math.inf]:
            with self.assertRaises(ValueError): decide(a, b, {'sigma': 1, 'review_mmd_squared': tolerance})

    def test_centroids_keep_document_equal_weight(self):
        """A long document creates more chunks but still contributes exactly one normalized point."""
        documents = [{'document_id': name, 'revision': 1, 'sha256': name, 'file': name+'.md', 'text': text}
                     for name, text in [('short', '# Short\nA short document.\n'), ('long', '# Long\n'+'Many words. '*180)]]
        class Encoder:
            """Supply known orthogonal vectors for the document-weighting contract."""
            def encode(self, texts):
                """Represent the short document separately from every long-document chunk."""
                return [[1., 0.] if 'Short' in t else [0., 1.] for t in texts]
        result = document_vectors(documents, {'dimensions': 2, 'chunk_max_chars': 200, 'overlap_chars': 30}, Encoder())
        self.assertEqual(len(result['documents']), 2)
        self.assertGreater(result['documents'][0]['chunk_count'], 1)
        self.assertEqual([d['vector'] for d in result['documents']], [[0., 1.], [1., 0.]])
        with self.assertRaises(ValueError): document_vectors(documents+documents, {}, Encoder())

    def test_scenarios_do_not_mutate_source(self):
        """Controlled foreign texts are in-memory only and retain a separate source identity."""
        original = [{'document_id': str(i), 'text': '# Test\nText.', 'sha256': 'old'} for i in range(4)]
        saved = deepcopy(original); changed = scenario_documents(original, 'out-of-domain')
        self.assertEqual(original, saved); self.assertNotEqual(changed, original)
        with self.assertRaises(ValueError): scenario_documents(original, 'unknown')


if __name__ == '__main__': unittest.main()
