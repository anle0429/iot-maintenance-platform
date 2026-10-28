# Bàn giao kiểm chứng và trình bày

Ngày28/10/2026 tiếp tục trên cùng VM8CPU/64GiB, không khởi động lại coursework/lịch10ngày. Deadline tạm31/10 giữ nguyên. Chuẩn chính là [57dòng final-coursework vàA2](final-rubric-tracker.md); không tự cộng điểm từ tracker93dòng cũ.

## Tự kiểm kết quả đã lưu

Từ repo root, `python -S scripts/verify-final-handover.py` dùng standard library và Git để kiểm:

1. Repo: source frozen307file,732tests/292subtests/7skips,15verifier evidence và audit docstrings.
2. Airflow: ba DAG thành công, tám task, graph lấy từ task definitions qua API thật; đối chiếu lại hai run streaming đã có trong CI.
3. Feature/model:200máy,195dự đoán tính lại từ portable model6,5máy dừng409, trace/arrival/age/lineage và cache TTL thật.

Đây là kiểm artifact, chạy được khi VM đã STOP. Để chạy tests mới xem [repo quality](repository-quality.md); để kiểm live cần đúng runtime, dữ liệu và phiên VM còn hạn.

## Phân biệt ba quy mô dữ liệu

| Phần | Đã chạy thật | Không suy ra từ kết quả này |
|---|---|---|
| Nguồn lớn |102.447.847.856byte JSONL offline; Spark323,136triệu Bronze/316,8triệu Silver,13checks | Chưa Gold/features/training toàn100GB |
| Feature/ML |200máy×60ngày,3.456.000snapshots/3.132.184training rows, protocol/split cố định | Không ghép thành full100GBfeature pipeline |
| Streaming push |Kafka/Flink live nhỏ; CI hai job dùng ba cửa sổ biết đáp án giữ lại từ luồng đó | Không phải nguồn cảm biến chạy liên tục hay distributed exactly-once |

[Nguồn100GB](full-scale-ingest.md), [feature60ngày](medium-feature-dataset.md), [CI streaming](ci-stream-push.md) giữ số đo/giới hạn riêng. Không sinh hoặc train lại100GB để chụp ảnh.

## Ba pipeline feature

[TTL và thứ tự stage](feature-jobs-and-ttl.md) giải thích rolling view10phút, monitoring view1giờ, Redis3600giây thật và lease3600giây thật. Materialization có bốn task; offline/online stream mỗi DAG hai task `push_finalized_windows → replay_without_new_writes`. Cả ba run đã hoàn tất; hai job streaming chạy tuần tự theo pool một slot.

![Readback Airflow API thật](evidence/airflow-final-readback/screenshots/three-pipelines-api.jpg)

Ảnh là trình xem **API chỉ đọc tự tạo**, đọc trực tiếp run/task/graph từ Airflow; không giả giao diện Airflow gốc. [Mười response gốc](evidence/airflow-final-readback/native-manifest.json) và [snapshot](evidence/airflow-final-readback/snapshot.json) giữ nguyên. [Ảnh UI Airflow materialization](feast-materialization.md) và ảnh Jenkins hai job có riêng; ảnh UI Airflow gốc của hai DAG streaming vẫn cần bổ sung nếu yêu cầu hình thức chấm đòi UI đó. Không gọi ảnh run đã hoàn tất là ảnh hai job đang RUNNING đồng thời.

## Demo feature vừa làm mới

Materialization thật lúc **22:10:04UTC ngày28/10** sau khi cache cũ tự hết hạn; không sửa timestamp/lease. Model vẫn6/run`be3b27fa0960493d92b45be69b20690e`, model7 giữ candidate. Native200máy có195request200/mathdifference0 và5request409 (`P-006`, `P-010`, `P-030`, `P-041`, `P-053`). [Receipt](evidence/final-demo-linux/verification.json) và [capture HTTP đầy đủ](evidence/final-demo-linux/http-all-machines.json).

![Feature HTTP mới](evidence/final-demo-linux/screenshots/fresh-feature-http.jpg)

Ví dụ P-001 có `risk_score=0.10318092377929837`, threshold0,7 nên quyết định0. Score từ logistic có class weighting **chưa được calibration**; không trình bày nó là xác suất hỏng10,3% đã được kiểm định. `as_of`29/11 là đồng hồ mô phỏng; ngày chuẩn bị22:10 là đồng hồ máy thật. Đây không phải trạng thái máy bơm vật lý hiện tại.

Trong phiên review hiện tại có cầu nối private qua IAP tại `http://127.0.0.1:19404`: chỉ GET docs/health/features và POST endpoint prediction cho machine ID cố định. Nó giữ kiểm CA/SNI và credential gateway ở phía upstream; trình duyệt nhìn HTTP loopback, không nhận browser-trusted public TLS.

```powershell
$base = 'http://127.0.0.1:19404'
Invoke-RestMethod "$base/health/ready"
Invoke-RestMethod "$base/v1/machines/P-001/features"
Invoke-RestMethod -Method Post "$base/v1/machines/P-001:predict" -ContentType 'application/json' -Body '{}'
# P-006 đang dừng nên phải nhận HTTP409.
Invoke-RestMethod -Method Post "$base/v1/machines/P-006:predict" -ContentType 'application/json' -Body '{}'
```

Cầu nối/tunnel review là trạng thái tạm cục bộ, không có khi clone repo. Cầu nối tự dừng trước lease; lease hết **23:10:04UTC**, VM tự STOP **23:13:58UTC** của phiên đã duyệt. Khi hết hạn, giữ lỗi readiness/TTL đúng thực tế, chuẩn bị một namespace/seed mới bằng luồng thật theo [freshness guide](active-feature-refresh.md); không sửa receipt để tiếp tục dùng snapshot cũ. Secrets/runtime helper nằm ngoài Git.

## Mạch trình bày khoảng10phút

- Bài toán: một dòng là một máy tại một thời điểm; dự đoán sự cố bắt đầu trong giờ tới chỉ cho máy đang hoạt động.
- Data: generator có seed/physics, duplicate/late/schema evolution; Spark dedup có kiểm multiset, Flink dùng event time và outbox kiểm replay. Nêu rõ phần nào100GB/phần nào nhỏ.
- Feature/ML: event time khác availability; split theo thời gian và purge cửa sổ nhãn; Feast SDK trong job/API, offline Parquet/online Redis. Model6 có lineage tới data/protocol/run, không tự promote candidate7.
- API: từ machine ID tới native feature và KServe; máy dừng409, feature stale503, schema lỗi422. Trình bày boundary/partition, Hypothesis, coverage/mutation/load bằng đúng scope từng evidence.
- Vận hành: Airflow chặn validation lỗi, DataHub lineage/assertions, CI gates/A-B/rollback, metrics/logs/traces, gateway/Vault/mesh có phép thử từ chối thật.
- Hai ứng viên bổ sung: [MMD](joint-cohort.md) kiểm quan hệ giữa cảm biến; [OPA](deployment-policy.md) chặn cấu hình/model envelope sai trước deploy. Ngoài nội dung lớp vẫn chờ người dùng xác nhận.

## Phần còn cần chốt

Hai ý tưởng ngoài lớp chưa được tự nhận hoàn tất chỉ vì tên không có trong lịch học. Ảnh UI Airflow gốc của hai DAG streaming chưa bổ sung; trình xem API ghi rõ nguồn và không được gọi là UI gốc. Laptop chưa được kiểm thực tế. Chứng chỉ lab/Vault/gateway có hạn ngắn; kiểm và renew thật trước buổi demo sau, không tắt xác minh TLS. Không có claim HA nhiều máy, API MMD/autoretrain hoặc toànplatform đã Ansible hóa.

Code/config mẫu/tài liệu kỹ thuật/evidence cần thiết lên GitHub private. Dataset lớn, venv, Docker volumes, khóa/token, helper review và file điều khiển chat/handoff vẫn cục bộ. Tài liệu kỹ thuật và verifier trong repo dùng trực tiếp từ clone.
