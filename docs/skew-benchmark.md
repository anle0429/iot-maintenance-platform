# Spark: xử lý lệch tải khi join theo nhà máy

Benchmark `skew-join-27-v2` đã chạy thật trên VM hiện có lúc 02:00–02:02 UTC ngày 27/10/2026. Cả sáu lượt cho **4.320 dòng kết quả giống hệt nhau**; thời gian trung vị giảm từ **15,339 xuống 8,577 giây**, tương ứng **1,788 lần** trên workload này. Đây là truy vấn trên Silver của nguồn trung gian 5,59 GB, chưa phải kết quả xử lý toàn nguồn 100 GB.

## Truy vấn có ích gì?

Mỗi giờ, mỗi nhà máy cần biết số readings, số readings khi máy hoạt động, số readings vượt ngưỡng cảnh báo, tổng nhiệt độ và độ rung lớn nhất. Silver lưu `machine_id`; cả hai phương án cùng broadcast bảng 200 máy để lấy `factory_id`, rồi join với [ba chính sách cảnh báo](../pipelines/configs/factory-risk-policies.json).

Chỉ readings có `operating_status=operating` mới được tính cảnh báo. Nhiệt độ hoặc độ rung phải **lớn hơn** ngưỡng; bằng ngưỡng chưa cảnh báo. Ngưỡng là cấu hình minh họa có phiên bản, không phải tiêu chuẩn an toàn máy công nghiệp hoặc nhãn `failure_in_next_1h`. Đây là báo cáo offline đã chốt dữ liệu, không phải feature có sẵn ở thời điểm dự đoán.

F-01 có 12.096.000 readings; F-02 và F-03 mỗi nơi 2.592.000. Khi chia theo khóa nhà máy, **70% của 17.280.000 readings về một partition**. Tăng số partition đơn thuần không tách được các dòng cùng một khóa.

## Thay đổi và bằng chứng Spark UI

Baseline ép `SortMergeJoin` cho join chính sách. Spark phải shuffle và sort dữ liệu cảm biến theo `factory_id`. Bản tối ưu broadcast ba dòng chính sách, để từng task tra chính sách tại chỗ; Spark có thể tổng hợp cục bộ trước khi shuffle kết quả theo nhà máy/giờ. Join máy → nhà máy giống nhau trong cả hai phương án.

[Ảnh stage 74](evidence/skew-benchmark/screenshots/baseline-stage-74.png) cho thấy shuffle read 288,6 MiB và task chậm nhất khoảng 10 giây. [Bảng task đã sắp xếp](evidence/skew-benchmark/screenshots/baseline-hot-tasks.png) chỉ rõ task index 17 đọc 12.096.001 records/202,1 MiB, so với hai task còn lại khoảng 2.592.001 records/43 MiB mỗi task. Mỗi nhóm gồm readings và một dòng chính sách. Nhiều task rỗng khiến median chung rất nhỏ; không lấy median đó làm thời gian đại diện cho task có dữ liệu.

![Stage baseline thật: một task đọc phần lớn dữ liệu](evidence/skew-benchmark/screenshots/baseline-stage-74.png)

[SQL 18 baseline](evidence/skew-benchmark/screenshots/baseline-sql-18.png) thực sự dùng `SortMergeJoin` trên `factory_id`. [SQL 19 tối ưu](evidence/skew-benchmark/screenshots/broadcast-sql-19.png) thực sự dùng `BroadcastHashJoin` trên cùng khóa, giữ đủ 17.280.000 output rows sau join. Không suy ra kế hoạch chỉ từ tên hint trong code.

![Join chính sách được broadcast trong lần chạy thật](evidence/skew-benchmark/screenshots/broadcast-sql-19.png)

[Stage 85 sau tối ưu](evidence/skew-benchmark/screenshots/broadcast-stage-85.png) đọc 666,9 KiB/21.960 partial aggregates trong nhóm 20 task tương ứng, thay vì các readings gốc. Max UI duration 24 ms; query tổng thể vẫn mất khoảng 9 giây vì còn đọc, chuyển đổi, join và tổng hợp ở stage trước. Không gọi phép so sánh riêng hai stage này là tốc độ của toàn pipeline.

UI dùng các trường thời gian của Spark; [event parser](../pipelines/skew_metrics.py) còn ghi wall duration `Finish Time - Launch Time`, nên có chênh nhỏ giữa duration trong JSON và UI. Ví dụ task nóng wall duration 9.880 ms. Giữ nguyên cả ảnh và số liệu gốc.

## Đo trên cùng workload

VM `iot-cw-vm`, `us-central1-a`, 8 vCPU/64 GiB, CPU Intel Broadwell. Spark **3.5.9**, Python **3.10.12** trong image `iot-coursework-spark:3.5.9`; controller dùng Python 3.12.3. Hai phương án cùng container 6 CPU/28 GiB, driver heap 20 GiB, 32 shuffle partitions, HDD scratch. AQE tắt và `autoBroadcastJoinThreshold=-1` cho cả hai để khảo sát riêng thay đổi chiến lược join chính sách.

| Cặp | Thứ tự đo | Baseline (giây) | Broadcast (giây) |
|---|---|---:|---:|
| 0 | baseline → broadcast | 18,242914 | 9,115160 |
| 1 | broadcast → baseline | 15,337732 | 8,443892 |
| 2 | baseline → broadcast | 15,338882 | 8,576587 |
| Trung vị | Ba lượt mỗi phương án | **15,338882** | **8,576587** |

Trong từng lượt, task metrics ghi input read 161.764.503 B cho cả hai. Đây là các cột được đọc, không phải toàn bộ 693.321.312 B của 64 file Silver. Baseline shuffle read/write mỗi chiều **302.603.711 B**; broadcast mỗi chiều **1.076.691 B**, giảm khoảng **99,64%**. Cả sáu lượt không failed task hoặc spill. [Danh sách sáu query trong UI](evidence/skew-benchmark/screenshots/paired-sql-runs.png) và [báo cáo đầy đủ](evidence/skew-benchmark/benchmark.json) giữ từng lần đo, không chỉ giá trị tốt nhất.

Không dùng Spark cache/persist cho benchmark. OS/file cache, JIT và dịch vụ Airflow/DataHub cùng VM không được cô lập; ba cặp trên một phiên không chứng minh speedup ổn định ở mọi kích cỡ. AQE có thể tự xử lý một số trường hợp skew trong cấu hình khác; chưa đo so sánh đó. Broadcast chỉ phù hợp khi dimension đủ nhỏ và có khóa duy nhất; salting/đổi partition là lựa chọn cần xem lại nếu dimension lớn.

## Kiểm đúng và tài nguyên

- **107 unittest** đã qua trên PC và VM trước benchmark. Bảy known-answer checks trong Spark kiểm ngưỡng bằng/vượt, máy dừng, biên giờ, chính sách khác nhà máy, hai chiến lược khớp đáp án tay, giữ dòng chưa match máy và chặn dimension có khóa trùng. Bốn kiểm toàn tập bổ sung giữ số dòng/phân phối/provenance, exact aggregates và physical plans.
- Nhiệt độ/độ rung ba chữ số thập phân chuyển sang số nguyên milli-unit để so sánh chính xác tổng và max. Sáu tập kết quả có SHA-256 chung `7709e19ad539bcff7eeab3c17925c9a18d406dd9f9b69af0c01822d9ca41f877`; so sánh từng dòng, không chỉ tổng số dòng.
- [Input binding](evidence/skew-benchmark/input-binding.json) ghi receipt nguồn, chính sách, SHA từng file Silver/machines. Rehash sau benchmark giữ nguyên tất cả input và policy. [Verification](evidence/skew-benchmark/verification.json) cùng [success marker](evidence/skew-benchmark/_RUN_SUCCESS.json) ràng buộc SHA báo cáo/task metrics.
- Cgroup memory peak quan sát trước shutdown **8.294.035.456 B**. [112 mẫu tài nguyên mỗi giây](evidence/skew-benchmark/resource-samples.json): scratch lớn nhất **611.038.721 B**, RAM khả dụng thấp nhất **53.313.253.376 B**, HDD trống thấp nhất **410.017.701.888 B**. Sampling có thể bỏ sót đỉnh ngắn, không phải cam kết RAM cho toàn 100 GB.
- [Preflight](evidence/skew-benchmark/preflight.json) chứa exact source hashes, image ID, limits, base commit `270e880` cộng hash các file mới thực chạy. Đây là working-tree snapshot đã kiểm; không gán ngược final commit cho lần chạy trước commit.

Lượt `skew-join-27` trước đó lỗi vì code ban đầu giả định Silver có `factory_id`. Đã sửa bằng bước enrichment qua dimension máy, thêm known-answer check chống mất/nhân dòng, chạy lại vào output mới `skew-join-27-v2`. Output/log lỗi vẫn giữ riêng trên VM, không dùng làm evidence thành công.

## Tự chạy trên VM

Cần nguồn/Silver trung gian đã có và các marker hợp lệ, hai volume, image Spark đã build; clone Git không chứa dữ liệu hoặc volumes. Từ repo root, chọn tên mới và truyền **deadline STOP thực của phiên VM**, cùng commit release thực đang dùng:

```bash
data/runtime/scale-venv/bin/python scripts/run-skew-benchmark.py \
  --run-name my-skew-check \
  --deadline-utc "$VM_STOP_UTC" --base-commit "$RELEASE_COMMIT"
```

Đặt hai biến từ phiên/release hiện tại trước khi chạy. Không sao chép deadline lịch sử sang phiên mới. Launcher từ chối ghi đè, kiểm preflight, chạy test, kiểm Docker limits, theo dõi RAM/đĩa/thời gian và chỉ dừng container riêng của lượt đó nếu chạm guard. Scratch ở `/srv/iot-data/spark-scratch/<run-name>`; output trong `outputs/<run-name>`, kết quả trong `data/benchmarks/<run-name>`. Lượt dở dang không có resume.

Để xem History, bật profile `history` của `infra/ingest.compose.yaml`, rồi forward loopback cổng VM14041 qua IAP về máy cá nhân. Ứng dụng benchmark có ID `local-1793066417077`; SQL18/19 và stage74/85 là cặp đầu. Ảnh trong Git được chụp nguyên từ UI này; [manifest ảnh](evidence/skew-benchmark/screenshots.json) có URL/SHA.

## Liên hệ mốc tiếp theo

Benchmark này giải thích và chứng minh xử lý skew trên truy vấn báo cáo của tập trung gian. Chưa tích hợp truy vấn cảnh báo mới vào DAG DP2; Airflow DP1–DP3/DataHub pilot đã có [bằng chứng riêng](orchestration.md). Còn phải hoàn thiện runner có guard cho toàn nguồn lớn, kiểm partition/feature join, warehouse/indexing và các phần ML/platform trong tracker. Không dùng kết quả này để đánh dấu mọi tối ưu Spark hoặc toàn coursework đã xong.

Tham khảo chính thức: [Spark 3.5.9 join hints/AQE](https://spark.apache.org/docs/3.5.9/sql-performance-tuning.html), [event logs, History Server và task metrics](https://spark.apache.org/docs/3.5.9/monitoring.html).
