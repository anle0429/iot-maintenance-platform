# Thu hẹp join theo thời gian trước khi tính feature

Một snapshot của máy tại thời điểm `t` chỉ cần telemetry trong `(t−10phút,t]`. Join theo riêng `machine_id` có thể xét cả lịch sử nhiều ngày rồi mới lọc thời gian. [Bản dùng ô thời gian](../pipelines/bounded_features.py) thêm khóa bucket để thu hẹp ứng viên; điều kiện thời gian chính xác và thời điểm dữ liệu đã tới vẫn được áp dụng sau khóa đó.

| Phép tính | Độ rộng bucket | Hai bucket của snapshot | Điều kiện giữ nguyên |
|---|---:|---|---|
| Telemetry quá khứ | 600giây | hiện tại, trước đó | `t−600s < event_ts <= t`, `received_ts <= t` |
| Sự cố đã biết | 86400giây | hiện tại, trước đó | `t−24h < failure_start_ts <= t`, `recorded_ts <= t` |
| Nhãn và bảo trì tương lai | 3600giây | hiện tại, tiếp theo | `t < start_ts <= t+1h` |

Mỗi event chỉ có một bucket; mỗi snapshot được mở rộng thành hai bucket khác nhau. Vì thế một event hợp lệ chỉ được ghép một lần. Hai dòng rỗng của left join được gom về một snapshot: count bằng0, mean/max bằngNULL, độ lệch chuẩn bằng0 theo contract hiện có. Cần đầu vào đã dedup, snapshot duy nhất theo máy/thời điểm, và một phiên bản thông tin máy ban đầu cho mỗi máy. Không lấy truth tương lai vào tám feature; nhãn, can thiệp bảo trì và censoring vẫn nằm riêng.

Khóa dùng `floor(unix_micros(timestamp)/(window_seconds×1000000))`; giữ độ phân giải microsecond và floor cho thời gian trước epoch. API có trong [PySpark3.5.9](https://spark.apache.org/docs/3.5.9/api/python/reference/pyspark.sql/api/pyspark.sql.functions.unix_micros.html). Session của phép kiểm dùng UTC.

[Phép kiểm Spark](../pipelines/feature_checks.py) tạo đáp án bằng vòng lặp Python, `statistics` và oracle nhãn riêng. Nó kiểm cả bản range cũ lẫn bản bucketed với cùng đáp án: điểm đầu/cuối cửa sổ, lệch một microsecond, qua nửa đêm, trước epoch, bản ghi đến muộn, failure chưa được ghi nhận, không có readings, máy dừng và horizon chưa đủ quan sát. `created` là thời điểm tính thực tế nên không dùng làm giá trị cố định để so sánh.

## Kết quả thực trên VM

Lượt cô lập `feature-buckets-v3` hoàn tất04:01UTC27/10/2026 trên VM8CPU/64GiB hiện có; container bị giới hạn2CPU/4GiB, heap2GiB, Spark3.5.9/Python3.10.12,4shufflepartitions/AQEfalse, không mạng ngoài. Không thay code đã release, nguồn hoặc dịch vụ đang chạy. [Report](evidence/feature-buckets/report.json) và [receipt với command/image/hash thực](evidence/feature-buckets/verification.json) ghi8checksPASS cho9snapshots/168readings/192failure rows/4maintenance rows; thời gian fixture34,059giây gồm startup và cả hai strategy. Đây không phải benchmark tốc độ hoặc quy mô dữ liệu thực.

[Plan feature cũ](evidence/feature-buckets/range_features-plan.txt) join theo máy; [plan bucketed](evidence/feature-buckets/bucketed_features-plan.txt) cho thấy SortMergeJoin theo máy và bucket, vẫn có predicate event/receivedtime. [Plan nhãn cũ](evidence/feature-buckets/range_labels-plan.txt) và [plan nhãn bucketed](evidence/feature-buckets/bucketed_labels-plan.txt) cũng được lưu nguyên bản. Hash trong report phải khớp code trước khi dùng phép kiểm làm bằng chứng.

Hai lượt đầu dừng ở cấu hình container: DNS khi tắt mạng, sau đó truyền thừa `spark-submit` dù image đã có entrypoint. Chúng chưa chạy phép kiểm; giữ log lỗi riêng trênVM, sửa launcher rồi chạy thư mục mớiV3. Không đổi đáp án hoặc code tính feature giữa các lượt này. Bộ130unit host hiện có cũng vẫnPASS; không lấy unitcount thay cho coverage/mutation/API hoặc phép kiểmSpark.

Đây là thành phần chuẩn bị cho [protocol ML60ngày](ml-evaluation-protocol.md). Chưa đổi đường chạy Airflow DP3, chưa nhận đã xử lý feature60ngày hoặc100GB, chưa có metric mô hình mới. Cần kiểm trung gian và đo hiệu năng trên cùng workload trước khi chuyển pipeline chính.

Trong container Spark có repo ở `/workspace`, chạy phép kiểm vào thư mục mới:

```bash
/opt/spark/bin/spark-submit --master 'local[2]' --driver-memory 2g \
  --conf spark.sql.shuffle.partitions=4 --conf spark.default.parallelism=2 \
  --conf spark.sql.adaptive.enabled=false \
  /workspace/pipelines/feature_checks.py --output /workspace/outputs/feature-check-local-01
```

Image coursework đã đặt `spark-submit` làm entrypoint. Khi gọi `docker run IMAGE`, chỉ truyền các tham số sau entrypoint; không thêm `spark-submit` lần thứ hai. Cần ít nhất4GiB giới hạn RAM container cho cấu hình kiểm này. Nếu tắt mạng container, dùng hostname `localhost` cùng `SPARK_LOCAL_IP=127.0.0.1` và `SPARK_LOCAL_HOSTNAME=localhost`. Các lệnh trên không khởi tạo dịch vụ ngoài container Spark.
