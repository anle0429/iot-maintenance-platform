"""Encode source chunks and queries with one pinned, normalized CPU embedding model."""
import math


class TextEncoder:
    """Load the same model revision for indexing and query-time retrieval without remote executable model code."""
    def __init__(self, config):
        """Resolve only the declared model/revision and keep CPU inference bounded to two threads."""
        from sentence_transformers import SentenceTransformer
        import torch
        torch.set_num_threads(2)
        self.config = config
        self.model = SentenceTransformer(config['model_id'], revision=config['revision'], device='cpu', trust_remote_code=False)

    def encode(self, texts):
        """Reject empty/oversized input and require finite, unit-length vectors of the declared dimension."""
        if not texts or any(not text.strip() or len(text) > 8000 for text in texts): raise ValueError('Nonempty bounded texts are required')
        tokenized = self.model.tokenizer(texts, truncation=False, padding=False)
        if any(len(ids) > self.model.max_seq_length for ids in tokenized['input_ids']):
            raise ValueError('Text exceeds the embedding model token limit; make smaller chunks rather than silently truncate')
        vectors = self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False, batch_size=16).tolist()
        for vector in vectors:
            if len(vector) != self.config['dimensions'] or not all(math.isfinite(value) for value in vector): raise ValueError('Invalid embedding dimensions or values')
            if abs(sum(value * value for value in vector) - 1) > .001: raise ValueError('Embeddings must be normalized')
        return vectors
