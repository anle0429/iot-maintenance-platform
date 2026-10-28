# Airflow theo dõi drift từ Feast offline

Pipeline này đọc tám feature qua SDK Feast, so với mẫu train đã cố định bằng PSI, rồi PUT metric vào Pushgateway để Prometheus/Grafana đọc. Nó không đọc nhãn và không suy ra accuracy. Reference hiện thuộc model3 và tập train đã seal; kết quả mô tả thay đổi so với reference đó, chưa phải giám sát chất lượng model6 đang chuẩn bị deploy.

## Đồng hồ và phạm vi

Nguồn là Parquet mô phỏng bất biến đã có trong [Feast](feast-feature-store.md). Airflow chạy mỗi5phút, ánh xạ khoảng thời gian lịch chạy sang cửa sổ event-time mô phỏng dài1giờ, sau ranh giới train12/11. Config khai báo hai mốc đồng hồ riêng; khi vượt ngày cuối nguồn, task thất bại thay vì lặp cửa sổ cũ hoặc sinh thêm dữ liệu. `catchup=False`, một run hoạt động, không retry tự động.

Mỗi cửa sổ chọn tối đa1.024snapshot bằng SHA256 của khóa máy/thời gian; chỉ nhận reading_count≥1. SDK đọc registered feature service từ bản copy registry, rồi so grain và cả tám giá trị với truy vấn trực tiếp cùng nguồn. Parquet/reference/protocol được kiểm SHA trước/sau. Nhãn, trạng thái truth và bảng sự cố không nằm trong SQL hoặc payload. `created` là audit build clock của backfill, nên lookup khai báo `filter_by_created_timestamp=False`; tính khả dụng lịch sử được kiểm upstream bằng arrival≤snapshot. Đây không phải bằng chứng dữ liệu từng được phục vụ ở những ngày mô phỏng.

[DAG](../airflow/dags/iot_offline_drift.py) gọi [adapter Docker](../feature_store/drift_airflow.py) trong SDK image ID bất biến:1CPU/3GiB, readonly root/source, chỉ output mới được ghi. Các attempt giữ thư mục riêng. [Job](../feature_store/drift_monitor.py) không ghi success metric khi input/lookup/validation lỗi. SDK không materialize hoặc ghi Redis.

## Metric và vận hành

[Pushgateway1.11.4](https://github.com/prometheus/pushgateway/releases/tag/v1.11.4) dùng binary chính thức [đã pin](../infra/monitoring/pushgateway-linux.json), listener loopback19092. Publisher PUT thay nguyên nhóm cố định `job=iot_offline_drift`, không đưa run ID/machine ID/thời gian làm nhãn. Có tám PSI, tám policy flags, sample count, hai biên event-time, thời điểm thành công theo wall clock và reference ID.

Pushgateway giữ metric cũ cho đến khi bị thay/xóa; vì vậy dashboard luôn hiển thị tuổi lần so sánh thành công. Scrape `up=1` của gateway không chứng minh task Airflow vừa chạy thành công. Xem [khuyến nghị Prometheus](https://prometheus.io/docs/practices/pushing/) và [ngữ nghĩa PUT/honor_labels](https://github.com/prometheus/pushgateway/blob/master/README.md). Không tự xóa lịch sử, đổi ngưỡng theo holdout, retrain hoặc promote chỉ vì có drift.

## Kiểm tra hiện có

Code mới đã qua7unit kiểm clock mapping, train/data boundary, kích thước mẫu, từ chối timestamp thiếu timezone/public publisher, số liệu gauge biết đáp án và hai loại đồng hồ trên dashboard. Oracle PSI riêng dùng fixture hai bin tính tay và cố tình sửa score/count/decision để xác nhận bộ kiểm phát hiện sai:

```powershell
python -m unittest tests.test_drift_monitoring tests.test_offline_drift_oracle -v
```

Lượt native đầu do scheduler tạo đã success; đang thu readback/ảnh và lượt định kỳ tiếp theo, chưa công bố capsule bằng chứng. Airflow3.1.7 thực tế chọn CronTriggerTimetable nên interval_start=end ở mốc trigger; job chỉ dùng endpoint đó để ánh xạ, không dùng độ dài interval làm cửa sổ feature. Manual probe phải gửi `interval_end_utc` rõ ràng; không dùng manual run làm bằng chứng scheduler tự chạy ([Airflow](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dag-run.html)).

Kiểm UI phát hiện date unit đọc metric seconds thành milliseconds nên hiển thị1970. Dashboard đã sửa query nhân1.000 cho đúng [đơn vị ngày của Grafana](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/configure-standard-options/); metric gốc giữ seconds. Count và event clocks dùng màu trung tính; chỉ tuổi kết quả≥600giây chuyển đỏ theo cadence5phút và một khoảng dự phòng5phút. Màu không đánh giá chất lượng mô hình.
