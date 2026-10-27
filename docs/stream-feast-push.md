# Flink → Feast: ghi cửa sổ vào offline và online store

Luồng đã chạy thật trên VM hiện có ngày 27/10/2026: Kafka → Flink 1.20.3 → cửa sổ đã chốt trong SQLite → worker → Feast 0.67.0 → Parquet offline và Redis online. Worker đọc cửa sổ đã commit khi Flink còn chạy, không đợi bản export cuối job. Đây là tập biết đáp án để kiểm đúng trước khi mở rộng.

## Feature và kết quả

View `machine_tumbling_final_10m` có bốn trường `reading_count`, `temperature_sum`, `temperature_mean`, `vibration_max`; service `machine_window_monitoring`. Cửa sổ tumbling `[start,end)` này phục vụ giám sát. Mô hình dự đoán dùng tám rolling feature riêng trong [protocol ML](ml-evaluation-protocol.md); không đưa bốn trường này vào model đó.

Fixture gửi 22 Kafka records, có sáu event ID riêng. Chính sách late loại `E`; năm reading được nhận tạo ba cửa sổ. [Lượt cuối](evidence/stream-push/warm/feast/verification.json) qua cả kiểm tra lúc đang chạy và readback cuối. [Quan sát trực tiếp](evidence/stream-push/warm/feast/observed-live-push.json) ghi hai cửa sổ đã được xác nhận, hai container đang chạy, REST Flink `RUNNING` và chưa tồn tại post-job export. Job ID `3a2019574b0ae772cd8f6414858a046f`.

| Máy | Window end UTC | Count | Sum | Mean | Vibration max |
|---|---|---:|---:|---:|---:|
| P-001 | 2026-10-01 00:10 | 3 | 60 | 20 | 3 |
| P-001 | 2026-10-01 00:20 | 1 | 40 | 40 | 4 |
| P-002 | 2026-10-01 00:10 | 1 | 100 | 100 | 10 |

Offline giữ cả ba hàng; online giữ hàng mới nhất của mỗi máy, tương ứng mean 40 và 100. [Lệnh kiểm tra độc lập](../feature_store/inspect_windows.py) đã đọc thật cả hai store ở lượt đầu, [giá trị đầy đủ](evidence/stream-push/first-live/feast/inspection.json), nguồn offline giữ nguyên SHA. Lượt cuối cũng kiểm native historical cả ba hàng và online cả hai máy trong [worker report](evidence/stream-push/warm/feast/report.json).

`event_timestamp` là thời gian mô phỏng ở cuối cửa sổ; `created` là thời gian thực Flink chốt dữ liệu ngày 27/10. Không dùng hai đồng hồ này như cùng thời gian vận hành. Native historical equality tắt bộ lọc `created`; phép so sánh này không chứng minh dữ liệu đã có tại simulated window end.

## Retry và hai lần ghi

Feast native `PushMode.ONLINE_AND_OFFLINE` ghi online trước rồi append offline. DuckDB offline provider hiện ghi lại cả Parquet khi append; hai store không có transaction chung. [Outbox](../feature_store/outbox.py) giữ intent và acknowledgement bằng SQLite WAL, `synchronous=FULL`, khóa ổn định `(machine_id,event_timestamp)`. [Pusher](../feature_store/push.py) kiểm giá trị tuyệt đối, khóa một writer và đối chiếu offline trước retry:

- Nếu lỗi sau online, trước offline: retry native cả hai store, offline xuất hiện đúng một hàng.
- Nếu lỗi sau cả hai store, trước acknowledgement: offline đã có giá trị đúng thì chỉ reconcile online, không append lại.
- Intent đã hoàn tất thì replay không ghi; cùng khóa nhưng khác giá trị bị từ chối. Cửa sổ cũ mới được bổ sung vào lịch sử không làm online lùi thời gian.

[Sáu tiến trình SDK riêng](evidence/stream-push/native/verification.json) có exit thực tế `3,0,3,0,0,0`; mã 3 là hai lỗi chủ động được mong đợi. [Kết quả cuối](evidence/stream-push/native/finish.json) giữ bốn hàng và replay không đổi SHA. [Baseline trực tiếp](evidence/stream-push/native/baseline.json) push cùng bốn cửa sổ hai lần tạo tám hàng/ bốn khóa, dù online vẫn đúng. Đây là so sánh tính đúng trên cùng fixture, chưa phải benchmark tốc độ hay hiệu quả ở quy mô lớn.

140 unit tests qua trên PC và VM, gồm bốn kiểm tra outbox biết đáp án; [log VM](evidence/stream-push/native/unit-tests-vm.txt). Native SDK/Redis/Parquet được kiểm riêng bằng sáu tiến trình trên. Số unit test không thay cho API coverage hoặc mutation score.

## Lỗi đã giữ và sửa

| Lượt | Kết quả toàn controller | Bằng chứng và ý nghĩa |
|---|---|---|
| `feast-stream-live-27` | PASS | [Verification](evidence/stream-push/first-live/feast/verification.json), source còn chạy khi hai window đã được ack |
| `feast-stream-ui-27` | FAIL | [Console](evidence/stream-push/ui-negative/controller-console.txt): store cuối đúng nhưng worker sẵn sàng quá muộn; không quan sát được ack lúc source còn chạy. Không có controller verification |
| `feast-stream-warm-27` | PASS | [Readiness](evidence/stream-push/warm/feast/worker-ready.json): chờ worker 19,514 giây, progress đầu tiên 0 hàng, source container chưa start; sau đó mới chạy Flink. [Verification](evidence/stream-push/warm/feast/verification.json) qua |

Launcher hiện tại SHA `9b9be8b6f798227e2a0017070d6aa24d7d5f3c15af4fc3f8b2853a139a66112e` thêm readiness gate. Hai lượt trước dùng [bản đã thực chạy](evidence/stream-push/prewarm/run-stream-push.py.txt) SHA `3dfe9ca3e74c7dde6f0db6a195c355743e3fc066faa95ac5ee40e0dc11163af2`. Không sửa report cũ thành PASS. Marker `_STREAM_PUSH_SUCCESS` chỉ xác nhận worker; controller còn phải kiểm quan sát live và nguồn, nên marker này vẫn tồn tại ở lượt overall FAIL.

Native fixture V1 từng gặp lỗi serialize `numpy.bool` sau khi đã tiêm lỗi online thật; giữ [console](evidence/stream-push/native-initial/online-fault-console.txt) và [snapshot helper](evidence/stream-push/native-initial/failed-push-checks.py.txt). V2 sửa cách xuất boolean và tách namespace fixture; outbox/pusher giữ nguyên bytes. Sáu bước V2 đều có report/hash/exit riêng.

Hai ảnh dưới được chụp trực tiếp từ UI của **lượt bổ sung `feast-stream-ui-27`**, khi job đang RUNNING. Chúng minh họa topology/checkpoint, không thay cho bằng chứng controller cuối thành công ở lượt warm:

![Flink đang chạy bốn operator, Kafka đã đọc dữ liệu](evidence/stream-push/screenshots/flink-live-window-push.jpg)

![Bộ đếm checkpoint thực tại thời điểm quan sát](evidence/stream-push/screenshots/flink-checkpoint-observation.jpg)

Ảnh checkpoint ghi Triggered 14, Completed 1, Failed 12, In progress 1, Restored 0. [REST snapshot sớm hơn](evidence/stream-push/ui-negative/corrected-rest-checkpoints.json) ghi Completed 0, Failed 12, In progress 1. Hai thời điểm khác nhau; chưa xác định nguyên nhân bộ đếm Failed bằng log. Không nhận tất cả checkpoint thành công hoặc đã chứng minh khôi phục checkpoint.

## Tài nguyên và phạm vi

Chạy trên cùng VM 8 vCPU/64 GiB khi Spark 100 GB vẫn hoạt động; không tạo VM/đĩa mới. [Preflight](evidence/stream-push/warm/feast/preflight.json) ghi RAM khả dụng 50.660.491.264 byte, mẫu CPU nhàn 5,116 cores. Giới hạn actual: Flink 1 CPU/3 GiB, Kafka 0,5 CPU/768 MiB, worker 0,5 CPU/8 GiB. Feast image `113eda…`, Flink image `decb7a…`; [build proof](evidence/stream-push/image/verification.json), builder 1 CPU/6 GiB. Builder đã stop, cache còn trên SSD.

Worker dùng đúng UID/GID 9999 của Flink image trong lượt này, SQLite query `mode=ro`. Volume ledger vẫn RW để SQLite phối hợp WAL/shared memory; worker không sửa bảng nghiệp vụ của Flink. Registry/outbox/Parquet nằm trong state riêng của từng run. Redis chỉ mạng nội bộ. Không đổi 19 file đang được full Spark bind; [checkpoint chụp cùng bằng chứng](evidence/stream-push/capture/runtime-checkpoint.json).

Chưa kiểm power loss giữa lần rewrite Parquet, nhiều writer phân tán, transaction cùng checkpoint Flink, scale streaming 100 GB, continuous production deployment hoặc SLA freshness. Lượt này chỉ hai máy/ba cửa sổ; không thay cho streaming toàn bộ mini-coursework. M2-12 vẫn đang làm vì còn cần ảnh terminal/job thành công và tích hợp orchestration/metadata cho view streaming. Full Spark 102,45 GB cũng chưa có marker cuối tại thời điểm chụp.

## Tự chạy trên Linux VM

Từ repo root, dùng các image/Redis đã chuẩn bị theo [Feast guide](feast-feature-store.md) và [Flink guide](day3-streaming.md). Dữ liệu/runtime không tự có khi clone Git. Chọn tên và thư mục state mới; không ghi đè lượt đã có. Native proof bên dưới phải được tạo lại nếu sửa code được bind:

```bash
mkdir -p outputs/push-practice
for step in online-fault recover-online ack-fault recover-ack finish baseline; do
  state=push-practice
  if [ "$step" = baseline ]; then state=push-practice-baseline; fi
  docker compose -f infra/feast.compose.yaml run --rm tools \
    -m feature_store.push_checks --step "$step" \
    --state "/workspace/data/feast/$state" \
    --output "/workspace/outputs/push-practice/$step.json"
  code=$?
  case "$step" in online-fault|ack-fault) expected=3;; *) expected=0;; esac
  test "$code" -eq "$expected" || exit 1
done
```

Controller còn yêu cầu preflight/verification binding của sáu bước như [report thực](evidence/stream-push/native/verification.json). Trên VM đã kiểm có sẵn `outputs/push-native-27-v2/verification.json`. Với code đúng SHA của proof, chạy fixture mới, thay deadline bằng giờ tự STOP thực của phiên hiện tại:

```bash
sudo data/runtime/scale-venv/bin/python scripts/run-stream-push.py \
  --run stream-practice-1 --deadline-utc '<actual-stop-time-UTC>' \
  --native-proof outputs/push-native-27-v2/verification.json \
  --ingest-fence outputs/scale-ingest-100gb-27/preflight.json
cat outputs/stream-practice-1/feast/verification.json
```

`--ingest-fence` dùng khi full Spark tương ứng còn hoạt động. Controller từ chối nếu thiếu 20 phút, RAM 20 GiB, SSD 40 GB, HDD 35 GB hoặc mẫu CPU nhàn 2,25 cores; không bỏ gate. Chạy trong phiên quản lý nền hiện có nếu đóng SSH. Lệnh kiểm dữ liệu sau khi worker hoàn tất:

```bash
docker compose -f infra/feast.compose.yaml run --rm tools \
  -m feature_store.inspect_windows \
  --state /workspace/data/feast/stream-stream-practice-1 \
  --worker-report /workspace/outputs/stream-practice-1/feast/report.json \
  --output /workspace/outputs/stream-practice-1/feast/inspection.json
```

Redis TTL có thể hết sau khi run kết thúc; inspection từ chối dữ liệu thiếu, không tự điền số hoặc giả làm prediction. Xem [Push source](https://docs.feast.dev/reference/data-sources/push), [DuckDB offline](https://docs.feast.dev/reference/offline-stores/duckdb), [Redis online](https://docs.feast.dev/reference/online-stores/redis) và [Docker builder limits](https://docs.docker.com/build/builders/drivers/docker-container/) cho hành vi SDK/hạ tầng; bằng chứng tại repo là số đo của phiên bản thực chạy.
