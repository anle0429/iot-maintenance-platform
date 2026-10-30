# Reference ingestion and retrieval

The knowledge corpus describes this simulator and its software contracts. Documents are versioned, chunked and encoded with a pinned CPU MiniLM model. The resulting snapshot connects each embedding to the exact passage and source identity that an agent can cite.

| File | Responsibility |
|---|---|
| [corpus.py](corpus.py) | Load and validate document/catalog identities |
| [pipeline.py](pipeline.py) | Prepare chunks, embeddings and governed publication outputs |
| [embeddings.py](embeddings.py) | Pinned encoder, input limits and normalized vectors |
| [store.py](store.py), [retrieval.py](retrieval.py) | Versioned Feast/Milvus storage and ordered source-bound retrieval |
| [quality.py](quality.py), [metadata_publish.py](metadata_publish.py) | Persist quality checks, assertions and lineage |
| [airflow_tasks.py](airflow_tasks.py), [ci_tasks.py](ci_tasks.py) | Native scheduled/release task boundaries |
| [corpus_drift.py](corpus_drift.py), [drift_ci.py](drift_ci.py) | Document-weighted MMD review before publication |

[Six-task runtime](../docs/rag-orchestration.md) · [Grounded responses](../docs/llm-grounding.md) · [Corpus drift](../docs/rag-corpus-drift.md) · [Class responsibilities](../docs/llm-class-design.md)
