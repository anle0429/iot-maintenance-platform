# API lấy feature và dự đoán theo mã máy

Người gọi gửi `machine_id`; API lấy tám feature qua **Feast SDK/Redis**, kiểm thời gian và trạng thái vận hành rồi gọi KServe. Người gọi không phải tự tạo vector hoặc gửi nhãn. [FeatureRuntime](../services/feature_api/runtime.py) chứa quy tắc nghiệp vụ, `FeastKServeBackend` là adapter và [FastAPI](../services/feature_api/app.py) xử lý HTTP/async.

## Kiểm chứng trên Kubernetes

[Native verification](evidence/feature-api/deployment/verification.json) **PASS11:47:31UTC27/10/2026**:200máy đọc feature thành công,195máy operating nhận prediction,5máy failed bị chặn409; máy lạ404, mã sai422. [Đối chiếu độc lập](evidence/feature-api/deployment/offline-comparison.json) đọc nguồn Parquet bằng DuckDB, kiểm mọi feature của200máy và tính lại195điểm từ portable model; maxdelta**0**. Hai [response mẫu](evidence/feature-api/capture/selected-http-probes.json) là trích từ request thật; toàn200response giữ trên VM, hash nằm trong [capture-state](evidence/feature-api/capture/capture-state.json).

Image`sha256:3eee3fb0764df3b440835af9c42e1e64df0789413b255a9365a218200180ee90`,1.080.059.467byte inspect, dùng lại SDKFeast0.67.0 đã khóa. [Dockerfile](../infra/feature-api/Dockerfile)/ignore chỉ gửi code nhỏ, không gửi dữ liệu hoặc secrets. [Pod thực](evidence/feature-api/capture/current-pods.json):UID/GID10001, readonlyroot/dropALL/no service-account token, API1CPU/1GiB, Redis500m/256MiB. Services ClusterIP. Redis demo riêng dùng bộ nhớ tạm; không đổi Redis/registry của pipeline huấn luyện và materialization cũ.

Docker default buildx xuất **OCI index**; cần đi qua manifest linux/amd64 rồi mới tới config digest của pod. [Capture repair](evidence/feature-api/capture/capture-repair.json) giữ lỗi đọc nhầm index ban đầu và quan hệ digest đã kiểm; không sửa source image hay số đo để khớp ID.

![Gửi mã máy và nhận HTTP200 từ luồng Feast→KServe](evidence/feature-api/screenshots/machine-prediction-http200.jpg)

![Feature trace có cả event/received time và simulation clock](evidence/feature-api/screenshots/arrival-aware-feature-trace.jpg)

## Hai đồng hồ và điều kiện được dự đoán

Demo dùng `as_of=2026-11-29T23:50:00+00:00`, **thời gian mô phỏng** trong nguồn đã khóa; không phải tình trạng máy tại giờ thực27/10. [Preparation](../feature_store/serving.py) tạo namespace Feast riêng, so200vector với nguồn immutable và lấy operating context chỉ từ telemetry có cả`event_ts<=as_of` và`received_ts<=as_of`. Không dùng future failures hoặc nhãn để xác định trạng thái hiện tại.

Mỗi feature phải có native status PRESENT, đúng machine, cùng timestamp và tuổi0–600giây tại đồng hồ mô phỏng. Context vận hành cũng phải đến kịp và không cũ quá600giây. Máy stopped vẫn có thể xem feature nhưng không được dự đoán sự cố sắp bắt đầu. KServe response phải khớp model/version/protocol/portable hash và ngưỡng0,7.

Redis key TTL**3600giây theo giờ thực** giới hạn thời gian giữ bản demo. API còn kiểm lease `<3600giây` từ lần chuẩn bị; điều này khác freshness600giây theo event time. Hết lease hoặc thiếu key trả503; readiness không còn xanh. Replay cùng checkpoint không tự làm dữ liệu mới hoặc gia hạn TTL. Sau reboot Redis tạm mất key, cần chuẩn bị/kiểm lại state demo trước khi dùng; không sửa timestamp cũ để giả freshness.

Lượt chuẩn bị đầu bị chặn vì chọn cutoff vượt horizon protocol; [log lỗi](evidence/feature-api/preparation-rejected/console.txt) giữ nguyên. Lượt hợp lệ lấy cutoff bằng observation_end trừ10phút. [Native fixture](../tests/fixtures/feature-api-native-online.json) là response SDK0.67 thật từ namespace demo riêng, không phải response tự dựng.

## Tests và sức mạnh kiểm thử

[PC hiện tại](evidence/feature-api/pc/tests-console.txt):**127passed/1skipped**, skip chỉ kiểm timezone POSIX không có trên Windows. [Coverage](evidence/feature-api/pc/coverage.json) hai API hiện có:211statements/56branches**100%**. Riêng feature API107statements/34branches; native Linux baseline**57/57passed**, gồm timezone test. Fixtures/mock dựa trên response Feast thật; tích hợp SDK/Redis/KServe còn được kiểm riêng bằng HTTP, không thay bằng mock.

![Coverage HTML từ lượt test thật](evidence/feature-api/screenshots/api-coverage.jpg)

[Tests](../tests/api/test_feature_api.py) kiểm boundary/partition:0/600/601giây, timestamp tương lai, lease0/3599/3600, field thiếu/thừa, không hữu hạn, unknown/stopped, upstream lỗi và provenance. Hypothesis50ví dụ kiểm replay/key order.32request đồng thời dùng threadpool kiểm số backend operation không vượt16, đồng thời health hoàn thành trước các lookup chậm.

Mutation chạy trên Linux bằng mutmut3.8, chỉ mutate function bodies của **feature API mới**, không mutate predictor/ML không đổi. Lượt đầu[345/489=70,55%](evidence/feature-api/mutation-initial/verification.json) **chưa đạt**,144survivors được giữ. Bổ sung8case kiểm đủ provenance/default clock/SDK config/metadata alignment/concurrency; không sửa production code. Lượt sau[413/489=84,46%](evidence/feature-api/mutation/verification.json),76survivors,0no-tests/timeout/suspicious/skipped, không exclusions. Tất cả489mutant chạy lại. Predictor có gate riêng209/246=84,96%; không gộp mẫu số để làm đẹp kết quả. Coverage100% không tự chứng minh test đủ mạnh.

## Load test thật

[Locust2.47.0](https://docs.locust.io/en/stable/installation.html), mỗi workload60giây, paced0,1giây/user, ramp2user/giây. SLA khóa trong [load code](../tests/load/feature_api.py) trước đo:ít nhất100request,0lỗi,p95≤500ms. Cùng VM chia sẻ với KFP và các platform services; API1CPU1GiB/Redis500m256MiB. Chỉ đo GETfeature, không đo prediction, mạng Internet hoặc công suất tối đa.

| Users | Requests | Failures | Req/s | p50 / p95 / p99 ms | HTML gốc |
|---:|---:|---:|---:|---|---|
| 1 | 597 | 0 | 10,015 | 6 / 7 / 9 | [Locust1user](evidence/feature-api/load/users-1.html) |
| 10 | 5770 | 0 | 96,655 | 20 / 32 / 35 | [Locust10users](evidence/feature-api/load/users-10.html) |

[Verification](evidence/feature-api/load/verification.json) và CSV/console cùng thư mục giữ số đo thật; đây là hai mức tải, không gọi là baseline/optimized của hai phiên bản code.

## Tự chạy và phần còn thiếu

Trên VM có runtime/data riêng: kiểm source hashes, dùng [preparation CLI](../feature_store/serving.py) với thư mục mới và cutoff trong protocol. SDK image dùng lockFeast; image API chỉ thêm adapter, không tải package khi startup. [Redis YAML](../infra/feature-api/redis.yaml) và [API YAML](../infra/feature-api/deployment.yaml) dùng ConfigMap seed được sinh từ state đã seal, không lưu state/registry vào Git. Với state demo đã đúng và pod Ready:

```bash
sudo k3s kubectl get pods,svc -n iot-features
sudo k3s kubectl port-forward -n iot-features svc/feature-api --address=127.0.0.1 18082:80
```

PC/laptop forward qua IAP tới VM127.0.0.1:18082, rồi xem`/docs`, GET`/v1/machines/P-001/features` hoặc POST`/v1/machines/P-001:predict`. Cài test tooling từ [requirements-test](../infra/feature-api/requirements-test.txt), chạy`python -m pytest tests/api --cov=services.predictor --cov=services.feature_api --cov-branch`. [Offline checker](../scripts/verify-feature-api-offline.py) chạy trong image SDK với workspace inputs readonly; chỉ output proof được ghi.

[Helm rolling/atomic rollback](api-deployment.md) của release mới`iot-feature-api` và [API drift](drift-detection.md) đã kiểm thật. Autoscale, gateway/auth/HTTPS, CI và dashboard còn thiếu theo [final tracker](final-rubric-tracker.md). KServe/API vẫn pin version1; model3 candidate đã có [incrementaldata link](incremental-data-versioning.md).63nativefile của lượt ban đầu được gắn [manifest](evidence/feature-api/capture/manifest.json)/[mapping](evidence/feature-api/capture/evidence-map.json); chứng cứ Helm mới nằm trong drift evidence. Rawdata/credentials/helpers không có trong Git.

## Ảnh báo cáo tải thật

![Locust 10 người dùng: 5.770 request, 0 lỗi, p95 32 ms](evidence/feature-api/screenshots/locust-10-users-html.jpg)

![Locust 1 người dùng: 597 request, 0 lỗi, p95 7 ms](evidence/feature-api/screenshots/locust-1-user-html.jpg)

Ảnh chụp trực tiếp hai HTML gốc, không sửa pixel. Đồng hồ trong trình duyệt là America/Chicago; report gốc và receipt ghi UTC. [Ràng buộc ảnh/HTML](evidence/feature-api/screenshots/locust-capture.json). Đây là tải có pacing trong 60 giây trên cùng VM, không đo công suất tối đa hay tốc độ dự đoán.
