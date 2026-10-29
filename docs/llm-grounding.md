# Grounded answers with traceable references

The assistant retrieves passages through Feast before sending them to the private llm-d endpoint. The recorded acceptance run answers three questions about the synthetic pump system. Every supplied passage is checked against its exact chunk ID, source revision and content hash.

## Recorded configuration

The current backend is **Qwen2.5-7B-Instruct, Q4_K_M**, using the [pinned two-shard model](../infra/llm/assistant.lock.json). The verified downloads total 4,683,073,632 bytes. The backend has a four-CPU/10-GiB limit, a 4,096-token context and one generation slot. It replaces the 1.5B prototype in the existing Helm release; both model files remain outside Git. The llm-d router and private network layout are unchanged.

The [1.5B thread benchmark](llm-inference.md) remains a historical comparison of that prototype. Its throughput figures do not describe the 7B backend. This milestone evaluates bounded reference handling; it does not establish a 7B throughput SLA or general question-answering accuracy.

## Why the retrieval adapter exists

During the original 7B probe, one Feast vector response returned ranked `chunk_id` values in a different order from its text, metadata and distance columns. The initial probe checked the ranked IDs and the cited document, so its success flag missed this inconsistency. A later independent source check exposed it. The [original response](evidence/llm-grounding/misaligned-columns/native/proof/result/responses.json) is preserved unchanged.

The [adapter](../rag/retrieval.py) keeps the native vector-search IDs, then reads each candidate individually through Feast `get_online_features`. It verifies the requested identity, recomputes cosine similarity from that record's embedding and checks ranking order. The [probe](../rag/probe.py) also compares all returned text/provenance fields with the corresponding source chunk and independently ranks all 18 source vectors. It refuses generation if these checks disagree. This avoids silently joining independently ordered columns; it does not replace Feast with a separate search engine.

## Actual responses

The corrected run is `rag-grounded-29c` on `iot-cw-vm`. Its retained container exited zero without OOM. It inspected a copy of the original native snapshot, found exactly 18 live entities after replay and left all six original state-file hashes unchanged.

| Question | Observed answer | Citation |
|---|---|---|
| Does high vibration by itself prove a pump failure? | No. Compare vibration with RPM, current, temperature and operating state. | Monitoring, “Vibration and operating load” |
| Why does prediction return HTTP 409 for a stopped pump? | The prediction target applies only to operating machines. | Prediction, “Stopped machines” |
| Does input drift prove prediction accuracy has decreased? | No. Accuracy needs observed labels and a valid evaluation protocol. | Drift reference |

These are paraphrases of the [three original model responses](evidence/llm-grounding/grounded/native/proof/result/responses.json), inspected against the authored sources. Source labels such as `[D1]` are local to each request. A valid label alone is not proof that an answer is supported.

The [answer builder](../rag/answer.py) places reference passages in a data message, separate from system instructions, limits question/context size and rejects missing or invented citation labels. This role separation is one control; adversarial prompt-injection evaluation remains separate work.

An earlier 1.5B response began with a contradictory “Yes” and cited the general assistant policy instead of the question-specific monitoring document. Its [failed receipt](evidence/llm-grounding/small-model-rejected/native/proof/result/answer-0.json) and exit state are retained. The smaller model was not promoted on the strength of fluent wording.

## Check the recorded result locally

From the repository root, no VM or model download is required:

```powershell
python -S scripts/verify-llm-grounding.py
python -m unittest tests.test_grounded_answer tests.test_consistent_retrieval -v
```

The verifier pins all [145 captured payloads](evidence/llm-grounding/manifest.json), including model/configuration receipts, exact uploaded source, rejected predecessors, native singleton reads and original generated replies. It recomputes vector rankings and source relationships. It verifies historical records, not current endpoint readiness.

## Repeat the live probe on the retained VM

The approved VM must still be running, with the existing dependency image and model cache. Run from its repository root, choose a new run name, and keep the original source/state directories intact. The source snapshot below is the exact corrected version used for this evidence.

```bash
IOT_ROOT="$(pwd)"
IOT_RUN=rag-practice-01
IOT_ROUTER_IP=$(sudo k3s kubectl get service iot-router-epp -n iot-llm -o jsonpath='{.spec.clusterIP}')
test ! -e "outputs/$IOT_RUN" || exit 1
sudo install -d -o 10001 -g 10001 "outputs/$IOT_RUN"
sudo docker run --name "iot-$IOT_RUN" --init --read-only --network host \
  --cpus=2 --memory=4g --memory-swap=4g --pids-limit=256 --cap-drop=ALL \
  --security-opt=no-new-privileges --tmpfs /tmp:rw,nosuid,size=512m -e HF_HUB_OFFLINE=1 \
  --mount "type=bind,source=$IOT_ROOT/data/llm-source/rag-probe-29c/tree,target=/workspace,readonly" \
  --mount "type=bind,source=$IOT_ROOT/data/rag/rag-foundation-29a,target=/state,readonly" \
  --mount "type=bind,source=$IOT_ROOT/outputs/$IOT_RUN,target=/proof" \
  --mount type=bind,source=/srv/iot-fast/models/iot-rag-embeddings,target=/models \
  --entrypoint /usr/bin/timeout iot-coursework-rag:deps-c3b696c3955c \
  --signal=TERM --kill-after=10s 480 python -m rag.probe \
  --source /workspace --state /state --output /proof/result \
  --base-url "http://$IOT_ROUTER_IP:8081" --model iot-qwen2.5-7b
```

Inspect `outputs/<new-run>/result/verification.json`, `responses.json` and `answer-0.json` through `answer-2.json`. A stopped VM, missing retained image/state or expired grant requires restoring the lab through its documented workflow; cloning Git does not copy models, databases or a running Service.

## Next integration boundary

The [Airflow/DataHub milestone](rag-orchestration.md) covers ingestion and governance. MCP execution, authenticated tools, specialist/coordinator agents, registry, sandbox, warm-up, CI and full telemetry remain listed in the [LLM tracker](final-llm-rubric-tracker.md). The current answer probe is not an agent runtime.
