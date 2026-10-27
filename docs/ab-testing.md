# A/B hai phiên bản model và giám sát không cần nhãn

**Đã chạy trên PC; chưa triển khai A/B qua CI trên VM.** Source `adaba5ab5eb6bee14fddd575d1f717e5afe94396`, Windows/Python3.13.14. [Bằng chứng](evidence/ab-routing-pc/verification.json) gồm20test A/B,227test trong gói nguồn cô lập và400dự đoán qua ba dịch vụ HTTP thật. Registry và Feast là fixture; hai identity model dùng cùng portable weights version1. Phép kiểm này chứng minh routing/định danh/replay/monitoring, không chứng minh model mới tốt hơn.

## Thành phần làm gì?

[A/B API](../services/ab_api/app.py) nhận `machine_id`, header `X-Request-ID` và body `{}`. Nó dùng [FeatureRuntime](../services/feature_api/runtime.py) hiện có để kiểm cả thời gian sự kiện, thời điểm dữ liệu có sẵn, lease3600giây và trạng thái operating trước khi gọi một trong hai predictor. Caller không được gửi raw feature, nhãn hoặc tự chọn variant/URL.

[Router và ledger](../services/ab_api/runtime.py) chia nhóm bằng byte đầu SHA256 của `experiment + NUL + machine_id`:0–127 vào baseline,128–255 vào candidate. Mỗi máy giữ một nhóm trong suốt thí nghiệm; đổi request ID không đổi nhóm. Đây là chia hash ổn định, không bảo đảm số máy của hai nhóm bằng nhau ở một tập hữu hạn.

SQLite lưu từng request ID đúng một lần trong transaction, kể cả kết quả409/503. Retry cùng ID/máy trả cùng JSON bytes, không gọi predictor hoặc tăng số mẫu; dùng ID đó cho máy khác nhận409. Cùng database có thể được hai worker đọc/ghi qua kết nối riêng; database được bind với experiment, cả hai model pin, context, reference và capacity. Dùng lại database cho cấu hình khác bị chặn. Tối đa2.048trial, không tự xóa bằng chứng cũ. Cached prediction cũng bị chặn khi lease hết hạn; lỗi đã ghi không được biến thành một trial thành công khi retry cùng ID.

Writes được tuần tự hóa và giữ transaction trong lúc gọi model. Đây là lựa chọn cho thí nghiệm riêng có giới hạn; chưa phải kiến trúc serving throughput cao. Chờ khóa có timeout5giây, upstream HTTP timeout3giây, tối đa4tác vụ backend đồng thời. Latency metric trong ledger đo phần gọi model/feature sau khi lấy khóa, không gồm thời gian chờ khóa hoặc mạng phía client.

## Drift và metrics được tính ra sao?

`GET /v1/experiment` đọc ledger và lấy snapshot duy nhất theo `machine_id + feature_event_ts`. Hai request khác ID trên cùng snapshot được tính là hai trial, nhưng chỉ một mẫu drift. Nếu cùng snapshot ID có feature khác nhau, monitoring từ chối thay vì âm thầm chọn một bản.

Cả hai nhóm dùng một reference cố định có cùng protocol. Mỗi nhóm cần ít nhất32snapshot duy nhất để tính PSI8feature bằng [detector hiện có](../services/drift_api/detector.py). Cửa sổ dùng event time, availability upper bound là `as_of` đã kiểm, và end-exclusive sau `as_of` một microsecond. Không nhận nhãn, không fit lại reference/bins theo nhóm. PSI chỉ mô tả marginal covariate drift; chưa đo accuracy, concept drift hoặc khẳng định hiệu quả model.

`GET /metrics` dùng [collector](../services/ab_api/metrics.py) đọc một snapshot ledger nhất quán: số trial, số outcome không200, histogram thời gian xử lý, mean risk score, số snapshot và PSI. Labels chỉ có experiment/variant/version/feature, không chứa machine/request ID. Counters của trial được đọc từ database nên restart worker không làm mất số đếm. Các request bị chặn trước khi thành trial, như label422 hoặc ID conflict409, không được cộng vào counters này; đây không phải counter của mọi HTTP request.

[Grafana JSON6panel](../infra/ab-api/grafana-dashboard.json) có counts, failures, unique snapshots, P95 của toàn thí nghiệm, mean score và PSI theo variant/feature. Đã kiểm metric names/selector so với scrape thật trên PC. [Prometheus và Grafana đã chạy thật trênPC](ab-monitoring.md):6panel được provision,26series đối chiếu qua mỗi query engine, có ảnh gốc. Chưa dashboard trênVM hoặc A/B CI native. Mean score không phải accuracy; không dùng dashboard này để tự promote model.

## Kết quả đã kiểm

- **20test A/B PASS**,181statements100%,51/54branches **94,44%**,0excluded. Có boundary ID/capacity/lease, model/context/reference mismatch, stopped/error outcomes, concurrent replay qua hai kết nối, Hypothesis15generated action sequences với set-counting oracle, PSI arithmetic độc lập, async health và startup adapter. JSON chứaNaN/Infinity hoặc sai cấu trúc trả422 an toàn, không echo input làm hỏng JSON lỗi.
- **Gói Git69file**, archive SHA256 `0142f1b6c0e3bc58ab80828ffd7e9270506cd2e73cdab6aaa6ed20423943358e`; [manifest](evidence/ab-routing-pc/source.manifest.json). Bản copy46file theo allowlist test đã chạy **227PASS/1POSIXtimezone skip/38subtest**. Predictor+feature+A/B đạt425statements100%,125/128branches **97,66%**. Đây là filesystem closure test, chưa chứng minh Docker builder semantics.
- [Integration test](../tests/integration/test_ab_routing_http.py) khởi động ba Uvicorn server trên các port loopback tạm: predictor1, predictor2fixture và A/B. **400HTTP predictions PASS**:baseline186/candidate214. Counter thật ở từng predictor tăng tương ứng186/214, đối chiếu độc lập với ledger.40retry HTTP đồng thời trả cùng bytes và không tăng counter/ledger. Drift chỉ dùng93/107snapshot, tổng200. Conflict409, unknown404, label/variant override422; restart worker đọc cùng report. Mọi server được stop và thread được join sau test.
- [HTTP readback nguyên bản](evidence/ab-routing-pc/http-readback.json), [metrics](evidence/ab-routing-pc/ab-metrics.txt), [kết quả](evidence/ab-routing-pc/http-verification.json). Client mean8,932ms/max34,528ms trên PC ở lượt này chỉ là số đo loopback có fixture; không phải Locust/SLA hoặc so sánh tốc độ hai model.
- Helm3.20.0 lint/template thực choK8s1.35.9 dựng Service/Deployment với private ClusterIP, một replica, nonroot/readOnlyRootFilesystem, token automountfalse và ledgerPVC. [Rendered YAML](evidence/ab-routing-pc/helm-rendered.yaml) không có status/UID giả. **Chưa build image hoặc deploy resource.**

Source và payload hash gắn trong [manifest](evidence/ab-routing-pc/manifest.json),17payload/864.409byte. Ba console chỉ thay đường dẫn PC bằng `[REPO_ROOT]`, có raw/published hashes và số lần thay. JSON/XML/HTTP readback/metrics giữ bytes gốc. Các fixture clocks/snapshot hashes không được dùng để nhận đã chạy native.44file ML guard/protocol không đổi; gói serving lịch sử d811/56file vẫn được tái tạo byte-exact.

## Tự chạy và phần tiếp theo

Trong môi trường kiểm thử API của dự án, dependencies theo [lock hiện có](../infra/ci/requirements-serving-tests.lock):

```powershell
python -m pytest tests/api/test_ab_api.py -q
$env:IOT_AB_PROOF_DIR = Join-Path (Get-Location).Path 'outputs/ab-demo'
try { python -m pytest tests/integration/test_ab_routing_http.py -q }
finally { Remove-Item Env:IOT_AB_PROOF_DIR }
```

Output directory phải chưa tồn tại; integration test từ chối overwrite. Test mở ba server loopback rồi tự đóng, không chạy Docker/cloud hay gọi registry thật. Chạy từ laptop cần môi trường Python riêng; Git không chuyển venv hoặc dataset/native services.

[Dockerfile](../infra/ab-api/Dockerfile) tái dùng Feast SDK đã có; [Helm values](../infra/ab-api/helm-values.yaml) chuẩn bị mounts cho config hai pin/URL, seed, fixed reference và ledgerPVC. Các tên hiện tại là giá trị chuẩn bị; native CI cần tạo ConfigMap immutable/PVC/tags riêng đúng release. Không deploy trực tiếp values này vào state cũ.

[Serving CI](ci-serving.md) đã đưa A/B vào test packet, coverage runner và phạm vi mutation dự kiến **7module**:4module version pin còn chờ native gate, cộng3module A/B mới. Chưa chạy mutation Linux. [Phase build/deploy/probe A/B trong Jenkins](ci-ab-deployment.md) đã nối mã ở source da1b180 và qua kiểm PC; chưa có native report/CI SUCCESS. Chốt full cutover vẫn đòi actualpod/volume/SQL identities và receipt mới. Tiếp theo kiểm native model counters/readbacks/reference/SQL, rồi Grafana trong phiên VM được phép. Không nhận report fixture có UID giả thành quyền đổi production traffic.

Tham chiếu: [Prometheus custom collectors](https://prometheus.github.io/client_python/collector/custom/), [Grafana dashboard JSON](https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/view-dashboard-json-model/). Các số đo ở trên là kết quả của dự án, tách khỏi tài liệu API.
