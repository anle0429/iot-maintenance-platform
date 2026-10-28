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

Code mới đã qua6unit kiểm clock mapping, train/data boundary, kích thước mẫu, từ chối timestamp thiếu timezone/public publisher, số liệu gauge biết đáp án và hai loại đồng hồ trên dashboard:

```powershell
python -m unittest tests.test_drift_monitoring -v
```

Native Airflow/Feast/Pushgateway/Grafana đang được chuẩn bị; chưa có bằng chứng thành công từ mốc này. Manual probe phải gửi `interval_end_utc` rõ ràng; không dùng manual run làm bằng chứng scheduler tự chạy. Khoảng dữ liệu của một run do timetable xác định, không mặc định bằng thời điểm người dùng bấm chạy ([Airflow](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dag-run.html)).
