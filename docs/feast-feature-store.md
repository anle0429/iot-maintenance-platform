# Feast: feature offline, online và tập huấn luyện

Feast quản lý tên/schema feature và cách lấy cùng một bộ input cho huấn luyện hoặc dự đoán. Mốc này chạy thật trên VM hiện có, dùng nguồn trung gian 200 máy × 60 ngày; nguồn DE 102,45 GB đang có lượt Spark riêng. Chưa có huấn luyện mới, Kubeflow Pipelines, Airflow materialization DAG hoặc streaming push từ mốc Feast này.

## Thành phần đã chạy

- [SDK image](../infra/feast/Dockerfile): Python3.12.16, Feast0.67.0, DuckDB2.0.0, Ibis12.0.0, NumPy2.5.4, pandas2.3.3, PyArrow26.0.0. [Lock](../infra/feast/requirements.lock) và [pip freeze thực tế](evidence/feast/image/pip-freeze.txt) tách khỏi Spark/Airflow để tránh xung đột NumPy.
- [Compose](../infra/feast.compose.yaml): tools1CPU/8GiB; Redis7.2.17 0,5CPU/256MiB, AOF và maxmemory128MiB/noeviction. [Runtime](evidence/feast/online-runtime.json) xác nhận network nội bộ, không mở cổng host.
- [Repository](../feature_store/repository.py): entity `machine_id`, view `machine_rolling_10m`, service `iot_failure_prediction_v1`, đúng 8 field trong [protocol đã đóng băng](../ml/protocols/medium-60d-v1.json). Nhãn và thông tin sự cố tương lai ở ngoài view. Registry gắn SHA nguồn/protocol/feature/compact.
- Offline là file Parquet bất biến; online là Redis. TTL view10phút giới hạn PIT theo event time; Redis key TTL3600giây theo đồng hồ chạy thật. Hai TTL có ý nghĩa khác nhau.

## Bằng chứng đúng trước khi tăng quy mô

[Fixture độc lập](../feature_store/checks.py) chạy native SDK, không giả lập provider: trước bản ghi đầu, đúng timestamp, dữ liệu đến muộn, correction chưa/đã có, TTL tại biên10phút và quá1microsecond, máy không tồn tại. [8 checks](evidence/feast/native/report.json) qua, gồm older write không làm lùi vector, replay không đổi giá trị và key Redis hết hạn thật sau3giây. Timestamp dưới microsecond bị từ chối để tránh cắt mất độ chính xác khi Spark đọc.

Fixture cũng cho thấy PIT mặc định có thể dùng correction hoặc reading chưa có tại thời điểm truy vấn; với cột `available_at` đúng nghĩa, `filter_by_created_timestamp=True` trả lời đúng đáp án. Với backfill60ngày, `created` là thời điểm dựng dataset thật, còn event/arrival là thời gian mô phỏng. Vì vậy lookup backfill dùng `False` rõ ràng; tính khả dụng đã được kiểm upstream bằng `received_ts <= t` trong [feature builder](medium-feature-dataset.md). Không coi build clock là availability lịch sử hoặc tuyên bố hệ thống từng phục vụ online ở những ngày mô phỏng.

## Số đo trên dữ liệu thật

| Công việc | Kết quả đã kiểm |
|---|---|
| Compact feature | 3.456.000 rows;128 file/128 row groups →1 file/13 row groups |
| Byte Snappy Parquet | 186.720.189 →157.481.024 B; giảm29.239.165 B, khoảng15,66% |
| Kiểm compaction | Hai chiều `EXCEPT ALL` toàn bộ dòng bằng nhau; SHA128 input trước/sau không đổi |
| Native historical probes | 1.000 rows trong1,902s;4.000 trong2,066s, trải suốt60ngày |
| Materialization | Initial tới29/11 23:00 và incremental tới30/11 00:00 (UTC mô phỏng); cả200máy khớp latest source ở mỗi cutoff |
| Native historical load đầy đủ | 3.132.184 eligible entities,60 daily Parquet parts, toàn bộ đi qua registered Feast service |
| Exact global verification | Cả8 feature, entity/timestamp, nhãn, episode, split và label availability khớp nguồn theo multiset hai chiều |
| Retry | Reuse3.132.184rows;0 native rows mới;60 file gốc giữ nguyên SHA |
| Corruption negative check | Lật1byte trong bản sao tách biệt; từ chối `Sealed day part changed; refuse retry`, không tạo success marker |

[Batch report](evidence/feast/batch/report.json), [compaction](evidence/feast/batch/compaction.json), [registration](evidence/feast/batch/registration.json), [full load](evidence/feast/full-load/result/report.json), [exact proof](evidence/feast/audit/exact.json) và [retry/corruption proof](evidence/feast/audit/verification.json) lưu nguyên byte từ VM. Timing probe là một quan sát cho mỗi cỡ, không phải benchmark throughput hoặc SLA. Compaction này chưa có transactional lakehouse hay phép đo query nhanh hơn, không phải feature100GB.

| Split cố định | Rows | Positive rows |
|---|---:|---:|
| Train | 2.195.668 | 93.603 |
| Validation | 468.132 | 19.828 |
| Test | 468.384 | 19.957 |

Class support giữ nguyên upstream; chưa tính metric mô hình mới. [Loader](../feature_store/load.py) kiểm protocol, upstream markers, input inventory/SHA, SDK/lock/code trước khi lookup; chỉ tái sử dụng daily part đã sealed cùng binding. Part dở dang không có marker sẽ bị từ chối. Khả năng retry này chỉ áp dụng Feast loader; Spark ingest lớn không có resume.

## Tự chạy

Từ repo root trên Linux có Docker, dùng tập feature đã được [chuẩn bị và kiểm chứng](medium-feature-dataset.md). Clone Git không mang theo dataset, Redis hoặc registry. Chọn tên mới cho report/state, giữ lại lượt lỗi để điều tra. Các lệnh sau tạo một phiên bản Feast mới, không ghi đè phiên bản đã chạy:

```bash
sudo install -d -o10001 -g10001 data/feast outputs/my-feast
export IOT_FEAST_SCRATCH=/srv/iot-fast/feast-scratch
docker compose -f infra/feast.compose.yaml build tools
docker compose -f infra/feast.compose.yaml up -d online
docker compose -f infra/feast.compose.yaml run --rm tools \
  -m feature_store.checks --output /workspace/outputs/my-feast/native
docker compose -f infra/feast.compose.yaml run --rm tools \
  -m feature_store.batch \
  --artifacts /workspace/data/feature-datasets/feature-medium-27-v2/artifacts \
  --feature-report /workspace/outputs/feature-medium-27-v2/feature-report.json \
  --state /workspace/data/feast/my-batch \
  --reports /workspace/outputs/my-feast/batch \
  --protocol /workspace/ml/protocols/medium-60d-v1.json
docker compose -f infra/feast.compose.yaml run --rm tools \
  -m feature_store.load \
  --state /workspace/data/feast/my-batch \
  --batch-report /workspace/outputs/my-feast/batch/report.json \
  --artifacts /workspace/data/feature-datasets/feature-medium-27-v2/artifacts \
  --feature-report /workspace/outputs/feature-medium-27-v2/feature-report.json \
  --protocol /workspace/ml/protocols/medium-60d-v1.json \
  --destination /workspace/data/feast/my-training \
  --reports /workspace/outputs/my-feast/load
docker compose -f infra/feast.compose.yaml run --rm tools \
  -m feature_store.verify \
  --dataset /workspace/data/feast/my-training \
  --load-report /workspace/outputs/my-feast/load/report.json \
  --artifacts /workspace/data/feature-datasets/feature-medium-27-v2/artifacts \
  --state /workspace/data/feast/my-batch \
  --protocol /workspace/ml/protocols/medium-60d-v1.json \
  --output /workspace/outputs/my-feast/exact.json
```

Scratch cần thư mục có quyền ghi choUID10001. Trên máy khác bỏ biến scratch để dùng fallback trong `data/feast`, rồi tính lại ngân sách. Full loader dùng shared registry lock; batch registration dùng exclusive lock. Chạy lại load với cùng destination và report directory mới sẽ kiểm sealed parts trước reuse. Không chạy batch lần nữa vào state đã tồn tại; model training sau đó dùng dataset/version đã sealed.

## Phần tiếp theo và giới hạn

Cần Airflow DAG materialize có lượt thành công/ảnh thật, streaming push cả offline/online, DataHub lineage cho feature store, KFP ít nhất4steps, MLflow và KServe. Nguồn offline tĩnh dừng ở29/11 23:55; sau Redis TTL theo wall clock, gọi incremental với cutoff không có dữ liệu mới không đảm bảo refresh key cũ. Serving phải kiểm freshness và phân biệt simulated time; chưa coi đây là dịch vụ dự đoán đang cập nhật liên tục.

Khi trình bày: feature builder quyết định thông tin nào có tại `t`; Feast quản lý lookup/PIT và online storage. Một PIT join hoặc số dòng khớp không tự chứng minh tránh mọi leakage, nên mốc này có fixture availability, upstream arrival checks, exact toàn bộ giá trị và corruption test riêng.

Tài liệu chính thức: [Feast0.67.0](https://pypi.org/project/feast/0.67.0/), [DuckDB offline](https://docs.feast.dev/reference/offline-stores/duckdb), [Redis online](https://docs.feast.dev/reference/online-stores/redis), [PIT joins](https://docs.feast.dev/getting-started/concepts/point-in-time-joins).
