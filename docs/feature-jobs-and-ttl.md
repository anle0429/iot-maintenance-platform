# Ba đường ghi feature và chính sách TTL

Feature store có hai view với mục đích khác nhau. `machine_rolling_10m` chứa tám đầu vào cho mô hình dự đoán sự cố trong một giờ tới. `machine_tumbling_final_10m` giữ **bốn** aggregate giám sát của cửa sổ Flink đã chốt; không đưa trực tiếp chúng vào mô hình tám đầu vào.

| Thành phần | Thời hạn | Giải thích và bằng chứng |
|---|---|---|
| Feast `machine_rolling_10m` | 10 phút theo event time | Feature dựng mỗi 5 phút từ 10 phút quá khứ. Cho phép tối đa khoảng hai nhịp cập nhật; API chấp nhận age 0…600 giây, từ chối tương lai/601. Đây là lựa chọn vận hành, chưa đo tối ưu accuracy/freshness. [Source](../feature_store/repository.py), [biên](api-validation.md). |
| Feast `machine_tumbling_final_10m` | 1 giờ theo event time | Giám sát các cửa sổ không chồng lấn 10 phút đã final; giữ phạm vi khoảng sáu cửa sổ cho lookup lịch sử. Không dùng TTL này để nhận dữ liệu đã có ngay tại window end. [Source](../feature_store/push.py). |
| Redis online key | 3600 giây theo đồng hồ thật | Giới hạn tồn tại của giá trị online sau ghi. Replay đã ack không ghi lại nên không gia hạn key. API vẫn phải kiểm tuổi event; key chưa hết hạn không đảm bảo feature fresh. |
| Lease demo | 3600 giây theo đồng hồ thật | Backfill dùng thời gian mô phỏng, nên lease giới hạn thời gian phục vụ snapshot trên máy thật. Không sửa timestamp/lease để kéo dài demo; phải chuẩn bị dữ liệu lại bằng luồng thật. |
| Parquet offline | Không tự xóa theo TTL Redis | Lưu lịch sử phục vụ audit/training; FeatureView TTL giới hạn phép lookup, không phải lịch xóa file. |
| Fixture thử expiry | 3 giây | Chỉ rút ngắn thời gian chờ trong phép thử native, không phải TTL nghiệp vụ. |

Chân trời nhãn một giờ là khoảng tương lai muốn dự đoán; nó không quyết định TTL của feature. `created` là audit clock; `received_ts`/availability cùng event time mới quyết định thông tin thực sự có khi dự đoán.

## Batch incremental qua Airflow

`register_bound_source → materialize_incremental → validate_online → publish_to_datahub`.

[DAG và ảnh Airflow bốn task thành công](feast-materialization.md) có initial, incremental, replay và ca lùi thời gian bị chặn. Incremental đổi đúng 200/200 vector; replay không đổi vector và không thêm native interval; backward không đổi SHA checkpoint. [DataHub sau phục hồi](datahub-restore.md) đọc lại đúng lineage và sáu assertion thành công của run này. Đây là nguồn 200 máy đã bound, không phải materialize toàn bộ telemetry 100 GB.

## Hai job streaming độc lập

| Job thực tế | Stage Airflow | Kết quả đã đọc lại |
|---|---|---|
| `iot_ci_stream_offline`, run `stream-offline-jenkins-3` | `push_finalized_windows → replay_without_new_writes` | 19:31:58…19:32:17 UTC ngày 28/10; hai task success; Parquet giữ ba khóa/cửa sổ và timestamp microsecond đúng nguồn. [API gốc](evidence/ci-stream-push/offline-3/ci/airflow.json). |
| `iot_ci_stream_online`, run `stream-online-jenkins-2` | `push_finalized_windows → replay_without_new_writes` | 19:33:48…19:34:04 UTC; hai task success; online latest P-001 mean40, P-002 mean100; replay không tăng dữ liệu. [API gốc](evidence/ci-stream-push/online-2/ci/airflow.json). |

Tên task chính xác đối chiếu từ `tasks.task_instances` trong hai JSON gốc; thứ tự phụ thuộc được khai báo trong [DAG](../infra/ci/iot_ci_stream_push.py). [CI streaming](ci-stream-push.md) giữ ảnh Jenkins thành công của cả hai job, console, fault/recovery SDK và bằng chứng nguồn trước/sau. Một executor và pool `feast_jobs` một slot cố ý ghi tuần tự; không nhận ảnh hai job đang chạy đồng thời.

![Job offline thành công](evidence/ci-stream-push/screenshots/jenkins-offline3-success.jpg)

![Job online thành công](evidence/ci-stream-push/screenshots/jenkins-online2-success.jpg)

[Luồng Kafka/Flink live trước đó](stream-feast-push.md) chứng minh worker ack khi source còn RUNNING, cùng chính sách late/replay. Lượt CI dùng ba cửa sổ/two machines giữ lại từ luồng thật để kiểm triển khai nhỏ; không thay cho streaming lớn, distributed exactly-once hoặc nguồn chạy liên tục. Ảnh Airflow riêng cho hai DAG streaming vẫn cần bổ sung; API success và ảnh Jenkins không được gọi là ảnh UI Airflow.

Tự kiểm không cần VM: `python scripts/verify-ci-stream-push.py`; kiểm API biên: `python scripts/verify-focused-api.py`. Chạy native lại theo [hướng dẫn CI](ci-stream-push.md), chọn release mới và kiểm tài nguyên/phiên được duyệt. Nếu snapshot/Redis/lease đã hết hạn, ghi nhận đúng trạng thái hết hạn; không sửa receipt lịch sử.

## Readback bổ sung28/10

[Ba DAG/tám task và edges từ API thật](final-handover.md#ba-pipeline-feature) đã đọc lại success, có ảnh trình xem API chỉ đọc. Đây không phải UI Airflow gốc và không nhận hai job đồng thời RUNNING. [Full-source stream stage](ci-stream-push.md) hiện vẫn giới hạn fixture ba cửa sổ; mở rộng tới toàn bộ5.760cửa sổ mini-coursework là việc tiếp theo, không lấy3row làm bằng chứng toàn nguồn.
