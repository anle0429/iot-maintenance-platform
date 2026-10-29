# Versioned knowledge ingestion and native retrieval

This milestone verifies the core RAG data path on the existing VM: four authored sources become 18 chunks, a real CPU encoder produces 384-dimensional embeddings, and native Feast stores/retrieves the data through Milvus Lite. It is not yet an Airflow/DataHub or agent demonstration.

## Source and chunk contracts

The [catalog](../knowledge/catalog.json) lists explicit document IDs, revisions, provenance and SHA256 values. The documents explain synthetic monitoring, the failure-prediction contract, drift and assistant response boundaries. They are authored coursework references, not manufacturer manuals or real maintenance records.

The [chunker](../rag/corpus.py) preserves section boundaries and carries source identity into each chunk. IDs depend on document ID/revision/hash, section, position and text, so replay is stable. Paths cannot escape the declared corpus. Changed, empty, oversized or undeclared sources fail before embedding. Long sections have bounded overlap; token-limit checks reject silent encoder truncation. The current longest section is 413 characters.

## Native stages

| Stage | Recorded behavior |
|---|---|
| Fetch | Validate and capture four declared source documents |
| Chunk | Produce 18 unique, source-traceable section chunks |
| Embed | Encode with all-MiniLM-L6-v2 revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`; normalized 384-dimensional vectors |
| Store | Write Parquet, register a separate Feast project, write through the SDK to Milvus Lite and read every text back by key |
| Validate | Encode three questions and call `retrieve_online_documents_v2`; the relevant source appears in each top-three result |

The store uses exact FLAT/COSINE search for this tiny corpus. It is isolated from the ML feature registries and Redis instances. Chunk text, document/revision/hash, section and embedding revision travel with every result. The feature view has a documented 365-day TTL; explicit source revision/snapshot selection is still necessary for knowledge updates. This run does not demonstrate cache expiry behavior.

All five containers exited zero, without OOM, under UID 10001, read-only roots, two CPU and 4 GiB memory/swap limits. Only the embedding stage needed network access to populate the pinned model cache; store/validation used that retained model offline. Package checks passed with Feast 0.67.0, Milvus Lite 2.5.1, PyMilvus 2.6.2, sentence-transformers 5.1.2, transformers 4.57.1 and CPU torch 2.9.1. The [full native freeze](evidence/rag-foundation/native/pip-freeze.stdout) records transitive versions.

## What replay and retrieval prove

Writing the same frame a second time returned the same values for all 18 chunk keys. The report preserves both SDK responses. It does not claim a transaction across arbitrary stores, concurrent index updates or independently measured physical row counts.

The three native queries concern high vibration, prediction rejection for stopped pumps and the distinction between input drift and accuracy. Relevant documents appear in the top three. This is a small acceptance set, not a broad retrieval-quality benchmark or proof that a model generated a grounded answer. LLM answers and citations are the next integration boundary.

## Run and inspect

The [native runner](../scripts/run-rag-pipeline.py) creates a new `rag-*` state directory and checks remaining VM time, free memory and SSD. It uses a source-sealed snapshot and dependency-image hash, preserves failed outputs, and never resets an existing run. [Pipeline stages](../rag/pipeline.py) can be examined independently.

For a small dependency-free local check from the repository root:

```powershell
python -m unittest tests.test_rag_corpus -v
```

To check the recorded native run without Docker, models or a running VM:

```powershell
python -S scripts/verify-rag-foundation.py
```

The [69-payload capsule](evidence/rag-foundation/manifest.json) retains the original native containers, reports, readbacks, query results and uploaded source snapshot. Model weights, the Milvus database, registry state and environment remain outside Git.

## Remaining acceptance work

Schedule the stages in Airflow; publish actual DataHub lineage/assertions; validate a failure path; connect retrieved context to real llm-d responses; then expose the bounded retrieval path through authenticated, validated MCP tools. Keep L08/L09 open until the required Airflow/governance evidence exists. See the [learning guide](llm-learning-guide.md) and [LLM tracker](final-llm-rubric-tracker.md).

The implementation follows the [Feast vector-RAG API](https://docs.feast.dev/tutorials/rag-with-docling) using the project's authored Markdown corpus and section chunker; it does not claim to have executed the tutorial's Docling/PDF example.
