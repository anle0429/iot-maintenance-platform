# Metrics và trace của ba API trên VM

Ngày28/10/2026, feature API, drift API và KServe predictor đã chạy wrapper OpenTelemetry thật trên VM `iot-cw-vm`8CPU/64GiB. Grafana có6panel; Jaeger có trace nối feature→HTTP client→predictor. Serving giữ model6; model7 vẫn candidate. Đây là lab mô phỏng trên cùng VM, không suy ra chất lượng mô hình hoặc khả năng chịu tải tối đa.

## Kết quả đã kiểm

| Phép kiểm | Kết quả thực | Proof gốc |
|---|---|---|
| Regression Linux |296test chính +14subtest PASS/0skip/28,66giây |[Console](evidence/api-observability-linux/gate/current/api-tests.txt), [JUnit](evidence/api-observability-linux/gate/current/junit.xml) |
| Coverage wrapper |74/74statements,16/16branches100%,0excluded |[HTML](evidence/api-observability-linux/gate/current/coverage-html/index.html), [ảnh](evidence/api-observability-linux/screenshots/native-coverage.jpg) |
| Mutation wrapper |221/250=88,4%,29survived; mọi trạng thái chưa xong bằng0 |[Meta](evidence/api-observability-linux/gate/current/observed.py.meta), [inventory](evidence/api-observability-linux/gate/current/mutmut-all-results.txt), [ảnh gate](evidence/api-observability-linux/screenshots/native-gate.jpg) |
| Rollout |3API Ready/image hashes khớp;200máy:195prediction đúng công thức,5stopped409 |[Receipt](evidence/api-observability-linux/native/api-rollout/verification.json), [200HTTP](evidence/api-observability-linux/native/api-rollout/http-all-machines.json) |
| Workload60giây |747request client:741×200,3×422,2×404,1×409;932SDK requests gồm185downstream |[Ledger](evidence/api-observability-linux/native/workload/run/requests.jsonl), [deltas](evidence/api-observability-linux/native/workload/run/actual-deltas.json) |
| Replay |185lời gọi cùng cửa sổ32quan sát; stored_windows6→7 |[Trước](evidence/api-observability-linux/native/workload/drift-ready-before.json), [sau](evidence/api-observability-linux/native/workload/drift-ready-after.json) |
| Metrics |6panel/28giá trị mỗi engine khớp SDK và phép tính độc lập |[Readbacks](evidence/api-observability-linux/native/metrics-readback/panel-readbacks.json), [overview](evidence/api-observability-linux/screenshots/api-dashboard-overview.jpg), [details](evidence/api-observability-linux/screenshots/api-dashboard-details.jpg) |
| Traces |5trace/15span, mỗi trace3span nối đúng parent/trace ID qua2API |[OTLP](evidence/api-observability-linux/native/trace-readback/searched-traces.json), [graph](evidence/api-observability-linux/screenshots/jaeger-three-spans.jpg), [metadata](evidence/api-observability-linux/screenshots/jaeger-span-details.jpg) |

JUnit có296phần tử `testcase`; thuộc tính `tests=310` của suite tính thêm14subtest. Field `regression_junit_testcases=310` và `gate/case-count-scope.json` gốc đặt tên/diễn giải chưa chính xác:310 là suite count, không phải số phần tử testcase. Giữ nguyên byte gốc và kiểm hai số riêng, không sửa report. Runner hiện đã sửa thành hai field `regression_main_testcase_elements` và `regression_suite_reported_tests`, đồng thời chặn testcase lỗi/skip dù suite counter ghi0. Bản sửa reader được kiểm PC51tests/21subtests; chưa có lượt Linux mới cho reader sửa, không thay kết quả native2f bên trên.

Lỗi biên được tạo có chủ đích. Counter409 tuyệt đối của feature là6:5máy stopped ở lượt kiểm200máy và1request stopped của workload. Tổng counter sau workload: feature774/predictor567/drift186, gồm traffic kiểm chứng trước đó. Delta932 khác747request client vì feature prediction gọi tiếp predictor.

## Source và triển khai

[Wrapper](../services/observed.py) bọc factory hiện có;15module nghiệp vụ giữ nguyên byte so với servingCI8/source99d6dcc. Source test/build/deploy `2fceb321e61af5a455df466b1fc27836d1cbf0e5`,96file seal trước/sau. Workload commit `4ea64cf305e15e2b514d1e5ffaa134bec3c39743`; config monitoring/Jaeger10a750e. [API manifest](evidence/api-observability-linux/native/source/api.json), [config manifest](evidence/api-observability-linux/native/source/config.json), [image proofs](evidence/api-observability-linux/native/image-build/verification.json) giữ SHA256 đầy đủ.

SDK1.45.1/instrumentation0.66b1/[dependency lock](../infra/observability/requirements.lock), pip check/nonroot10001. Ba image tag `observe-28b-v2` đã kiểm mã/model assets, Docker ID→CRI config digest và Pod thật. Docker image ID và CRI digest có thể khác nhau; verifier dùng đúng config digest. Model6/run `be3b27fa0960493d92b45be69b20690e`/portable `60dff76d4d27a9e248bc08a80c4da80a3212701774c00f89f0b16139e1609981` giữ nguyên.

Baseline materialize thật07:41:43UTC, Redis0→200key/minTTL3.576.943ms, seed ConfigMap mới bất biến. Rollout07:48:11 và workload08:06:09 còn trong lease1giờ. Không chỉnh timestamp/TTL hoặc ghi đè state cũ. Lease hết08:41:43UTC; demo cần prepare mới.

Gate chạy container2CPU/4GiB/hai workers/không mạng, đủ250mutant module mới; mutation serving cũ giữ scope riêng. **Rollout vận hành riêng sau gate, chưa phải Jenkins serving build mới.** CI8 chỉ chứng minh source99; cần tích hợp overlay/dependencies vào CI. Locust1/10users của CI8 là số đo source cũ; **chưa chạy lại Locust trên wrapper**. Workload hiện tại kiểm counter/trace/replay, không thay SLA/load test.

## Metrics và privacy

Mỗi API một worker/registry; scrape `/telemetry/metrics`, loại health/scrape khỏi business count. Histogram pin cũ dùng `http_target` là route template, không tạo series theo từng mã máy. View giữ method/status/template. Prometheus khám phá Pod Ready/Running cổng8080, bỏ init container và giữ PodUID.

Service account chỉ get/list/watch Pods ở hai namespace; kiểm thật2quyền hợp lệ/3quyền bị từ chối (Secrets, Nodes, default namespace). TLS dùng CA public từ ConfigMap, không tắt xác minh. Token0400 riêng/hạn14:48:31UTC không nằm trong Git/proof; cần cấp lại cho phiên mới.

Sáu panel: tổng request, req/s, lỗi4xx/5xx, P95, mean duration, route/status count. Verifier đọc SDK raw và matrix, tự tính reset/extrapolation/histogram interpolation;28giá trị qua mỗi engine khớp tại **08:06:15UTC**. Ảnh dùng cửa sổ08:04:30–08:06:30UTC; Last ở cuối cửa sổ có thể khác rate/P95 của verifier, không nhận là cùng eval.

Jaeger2.22.0/[asset pin](../infra/observability/jaeger-assets.json)/[manifest](../infra/observability/jaeger.yaml) chạy privateClusterIP/1CPU/512MiB/tối đa2.000trace memory, mất khi restart; OTLP4318/query16686/APIv3. Clock adjustment tắt, raw readback `rawTraces=true`; AI Assistant disabled. Jaeger UI dùng America/Chicago:03:06:05=08:06:05UTC; Grafana ảnh dùng UTC.

Trace chọn `77eac3a48d7878f1d678cb3a54e1e38b`,9,66588ms: featureSERVER→urllibCLIENT→predictorSERVER. Exporter chỉ giữ method/status/route, service/environment, ID/time; bỏ URL/query/header/payload/events/links/status description. Năm nativePOST trace không chứa feature/canary. **CanaryGET riêng chưa được tìm trong năm POST trace này**; unit test kiểm projection/canary riêng. Chưa instrument nội bộ Feast/Redis; queue hữu hạn/timeout2giây không bảo đảm không mất trace khi backend lỗi/quá tải.

## Lỗi gốc và khôi phục

Mutation đầu lỗi chép fixture; lượt hai200/250=80% chưa đạt **>80%**. Sửa parser multiline và thêm test có ý nghĩa, giữ ngưỡng; hai lượt sau221/250. Overlay đầu bị pip check chặn thiếu packaging ở drift base; thêm26.3 rồi gate/build mới. [History80%](evidence/api-observability-linux/gate/history/score-equal-eighty/results/tests/mutmut-cicd-stats.json), [build lỗi](evidence/api-observability-linux/native/history/missing-packaging/drift-build.txt) giữ riêng.

Jaeger probe đầu404 ở `/health/status`, không OOM; sửa `/status` theo Collector0.160.0 rồi Ready. API cũ404 đổi sangv3 thật. Monitoring đầu dùng sai CA path do K3s data directory riêng; tiếp tục bằng CA ConfigMap, không tạo lại token/tắt TLS.

Sau STOP/START, Airflow còn tham chiếu Docker network ID cũ. Đã dùng Compose gốc/env riêng/volume DB hiện có, bridge `iot-metadata-restored-28b`, recreate4container; không resetDB/migrate/đổi credentials. Lúc14:45UTC database/scheduler/dag processor healthy, lịch drift unpause đúng trạng thái gốc. [Readback](evidence/api-observability-linux/runtime-restore/readback.json). DataHub/Jenkins cũ chưa khôi phục trong phép kiểm này; Airflow healthy không chứng minh toàn platform healthy.

## Tự kiểm từ clone

Không cần VM/dependency ngoài standard library:

```powershell
python -S scripts/verify-api-observability.py
```

Verifier kiểm199payload/submanifests, Git source, mutation meta/inventory, JUnit/coverage,200HTTP/math, image/Pod identity, ledger/deltas/replay,6panel/28giá trị mỗi engine,5trace/15span và Airflow. Sáu JPEG là screenshot trình duyệt gốc, không sửa pixel. Unit mục tiêu trong môi trường API có dependency overlay:

```powershell
python -m pytest tests/api/test_observed.py tests/test_api_observability_gate.py -q
```

Kiểm live cần phiên VM được duyệt, token/CA còn hạn và seed mới. Không rerun helper đã tạo state/receipt cũ. Tiếp: Locust mới, CI overlay, rồi gateway/security/streamCI trong tracker final.
