# Từ catalog chunked tới Spark Bronze/Silver và Parquet

Mốc này nối nguồn JSONL theo chunk vào Spark mà không đẩy hàng triệu reading qua PostgreSQL/CSV staging hoặc gom telemetry vào Python list. Dùng image Spark **3.5.9** đã khóa digest ở mốc batch; không cài PyArrow vào host. Đây là bước tiếp của generator chunked, chưa phải Airflow hoặc bằng chứng xử lý 100 GB.

## Tự chạy trên tập nhỏ

Từ repo root trong PowerShell 7, sau khi có `.venv`, Docker Engine và nguồn pilot:

```powershell
./scripts/run-chunk-ingest.ps1 -RunName practice-ingest -SourcePath data/source/chunked-release/pilot
Get-Content outputs/practice-ingest/ingest-report.json
```

Nếu chưa có nguồn pilot, dùng lệnh trong [hướng dẫn chunked](chunked-generator.md). Bỏ `-SkipBuild` trên máy mới; dùng nó khi image `iot-coursework-spark:3.5.9` đã build. Script yêu cầu ít nhất 2 GiB RAM host trống và 8 GiB đĩa cho kiểm chứng hữu hạn này. Chỉ chạy container ingest theo yêu cầu; không cần PostgreSQL/Kafka/Airflow cùng lúc.

Chọn RunName mới mỗi lượt. Output Parquet ở `data/lakehouse/<run>/`; log/receipt/report ở `outputs/<run>/`. Script từ chối ghi đè. Chỉ dùng run khi có cả report `passed=true` và `_RUN_SUCCESS.json`. `-ReceiptPath` dùng lại receipt đã tạo cho đúng nguồn; adapter vẫn kiểm manifest/hash/file hiện tại. Source đã đổi sẽ bị từ chối.

## Receipt và adapter giải quyết điều gì?

[chunk_source.py](../pipelines/chunk_source.py) chạy validator tuần tự đã kiểm ở mốc trước và tạo receipt ràng buộc hash manifest cùng phiên bản validator. Khi Spark bắt đầu, adapter kiểm lại marker, catalog và byte thực của từng part. Nó chỉ mở năm table offline (`machines`, `maintenance`, `failures`, `v1`, `v2`), không nhập nhầm bản stream vào Bronze.

Adapter giữ metadata/counter nhỏ và cấp directory đã được kiểm cho Spark. Spark tự chia việc đọc file; Python driver không `collect()` telemetry hoặc dựng set toàn bộ event_id. Spark vẫn giữ file metadata và các state/shuffle của job; đây không phải tuyên bố RAM toàn pipeline luôn cố định ở mọi quy mô. Cuối run kiểm source lại theo contract bất biến. Receipt là dấu vết kiểm tra cục bộ, chưa phải chữ ký/attestation của một hệ thống phân quyền bên ngoài.

## Bronze, Silver và schema

[chunk_ingest.py](../pipelines/chunk_ingest.py) đọc schema tường minh, giữ timestamp UTC và ghi Snappy Parquet:

- Bronze có `raw_telemetry`, `raw_machines`, `raw_failures`, `raw_maintenance`. Telemetry giữ cả bản xuất thêm; v1 pressure là NULL, v2 phải có pressure. Metadata giữ interval/censoring và kiểu cột của nguồn.
- `source_path` là file part tương đối và `source_manifest_sha256` định danh chính xác nguồn. `ingested_at` ghi lúc đưa telemetry vào Bronze. Không bịa số dòng nguồn khi Spark directory reader không cung cấp line number.
- Silver `stg_telemetry` loại duplicate theo event_id, kiểm payload xung đột và giữ created_ts lớn nhất. first received_ts vẫn được giữ; không thay bằng thời điểm ingest. Grain phải đúng một reading/máy/thời điểm và machine FK phải tồn tại.

Mỗi table được đọc lại từ Parquet và đối chiếu **multiset hai chiều** bằng `exceptAll`: không mất/thêm dòng, không đổi giá trị, không che mất multiplicity hoặc NULL. Các cột payload/provenance cố định được so; `ingested_at` là metadata mới nên không đem so như một field có sẵn ở JSON nguồn. Dedup chỉ xảy ra ở Silver, sau phép so Bronze.

Đây vẫn là các thư mục Parquet thông thường, chưa có transaction log/catalog lakehouse, warehouse, SCD2 metadata-change mới, feature mới hoặc huấn luyện lại model. Luồng PostgreSQL ngày 2 và Kafka/Flink ngày 3 giữ phạm vi kiểm chứng riêng.

## Đo dung lượng và truy vấn

Report ghi byte/file và codec đọc từ footer **thật**, không suy codec chỉ từ option ghi. Tỷ lệ JSONL/Parquet dựa trên dữ liệu cùng payload, Bronze có thêm provenance và metadata ingest. Silver đã dedup nên không dùng nó để so tốc độ với JSONL chứa duplicate.

Phép đo query dùng cùng projection/filter/aggregate trên JSONL typed và Bronze Parquet, cùng khoảng event time, giữ nguyên duplicate. Ba lượt mỗi format, thứ tự xen kẽ, không cache DataFrame Spark. Kiểm đáp án mỗi lượt rồi báo toàn bộ thời gian và median. Cache OS/file metadata/JIT và ứng dụng nền không được cô lập; đây là kết quả cho một query/workload, không bảo đảm tốc độ cho mọi query hoặc quy mô lớn.

[ingest_metrics.py](../pipelines/ingest_metrics.py) đọc Spark event log tuần tự để cộng IO/shuffle/spill của các task attempt. Số này gồm cả kiểm chứng và query, không phải byte dataset hoặc đỉnh dung lượng scratch đồng thời. `memory.peak` trong cgroup là mức cao nhất tới trước khi shutdown được đọc; gồm process/cache/kernel accounting của container, không phải RSS host hay chỉ JVM heap. Container giới hạn 2.5 GiB/2 CPU, driver heap 1 GiB, local[2], shuffle 8 partitions trong các run được ghi.

Scratch nằm ở Docker volume Linux riêng `iot-coursework-ingest_ingest-scratch`; init chỉ cấp quyền ghi cho volume này rồi job chạy bằng user spark. Không dùng bind mount Windows làm shuffle scratch. Event log ở `data/ingest-events/`; dữ liệu/log/volumes không đưa vào Git.

## Kết quả đã chạy ngày 26/10/2026

Máy **THIENAN / Ryzen 5 5500 / Windows 11**, host Python 3.13.14; container Spark 3.5.9/Python 3.10.12/Java 17.0.19. Base commit `fc41b6360eab5ee3cffd3eb79857379dd1338d3f`, thay đổi chưa commit được định danh bằng SHA từng file trong run-context. Image ID `sha256:f794cf2433d348c8cb70c84d8048d3056a14602cdfa9b368dfc02d81815edb0e`. Các con số dưới đây là đo thật, đơn vị byte thập phân nếu không ghi khác.

| Số đo | Pilot `ingest-small-26` | Trung gian `ingest-medium-26` | Biên `ingest-boundary-26` |
|---|---:|---:|---:|
| Nguồn JSONL offline, kể cả metadata/duplicate | 37.263.211 | 1.117.608.156 | 21.152 |
| Bronze telemetry rows | 117.504 | 3.525.120 | 66 |
| Silver unique rows | 115.200 | 3.456.000 | 60 |
| Bronze telemetry Snappy Parquet | 4.676.588 | 99.682.563 | 44.886 |
| Silver telemetry Snappy Parquet | 4.764.665 | 102.937.160 | 44.534 |
| Cgroup memory.peak trước shutdown | 1.123.160.064 | 2.048.651.264 | 792.915.968 |
| Shuffle bytes written, cộng mọi task | 57.343.605 | 1.701.776.571 | 88.044 |
| Disk bytes spilled, cộng mọi task | 0 | 967.257.420 | 0 |
| Spark event log | 6.809.006 | 7.197.373 | 6.485.159 |
| Job wall time, gồm UI pause | 82,888 s | 289,074 s | 82,383 s |
| UI pause trong wall time | 30 s | 45 s | 45 s |
| Known-answer + kiểm readback | 13/13 | 13/13 | 13/13 |

Nguồn trung gian là **200 máy × 288 giờ liên tục**, không ghép các mô phỏng độc lập. Đã loại đúng **69.120** bản xuất thêm, v1 có 1.762.470 dòng pressure NULL và v2 có 1.762.650 dòng pressure hiện hữu trước dedup. Metadata đọc lại đúng 200 máy, 2.259 sự cố và 2.731 interval bảo trì. Tổng 19 file Parquet của năm table là **202.784.341 byte**; tổng output directory gồm fixture kiểm tra/CRC/marker là **204.373.064 byte**. Silver có ít dòng hơn nhưng file lớn hơn Bronze telemetry trong lượt này; thứ tự/phân bố giá trị ảnh hưởng nén, số dòng không tự xác định số byte.

Tập biên có một máy × một giờ, không sự cố và không bảo trì: hai table rỗng vẫn có schema Parquet đọc được, footer_rows=0 và không có row group/codec để quan sát. Không nhận codec rỗng là đã quan sát Snappy; các table có row group đều có footer SNAPPY. Trên tập vài chục dòng, overhead Parquet lớn hơn JSONL và query Parquet chậm hơn: không suy từ tập trung gian rằng format này luôn thắng.

Host log pilot ghi **82 tests** lúc chạy; trung gian và biên ghi **84 tests** sau khi thêm hai test parser metrics. [Report pilot](evidence/chunk-ingest-small/ingest-report.json), [trung gian](evidence/chunk-ingest-medium/ingest-report.json), [biên](evidence/chunk-ingest-boundary/ingest-report.json), [metrics trung gian](evidence/chunk-ingest-medium/spark-metrics.json), [máy/config/hash](evidence/chunk-ingest-medium/run-context.json) và [đối chiếu bằng chứng](evidence/chunk-ingest-verification.json).

### Cùng query, hai định dạng

Lọc event time **[2026-10-05 19:12, 2026-10-06 09:36) UTC**, tính count, tổng nhiệt độ và max rung trên nguồn trung gian. Cả hai format trả **176.311 dòng**, tổng nhiệt độ xấp xỉ **10.709.763,063**, max rung **9,169**; so tổng số thực có tolerance vì thứ tự cộng phân tán khác nhau.

| Format | Lần 1 (s) | Lần 2 (s) | Lần 3 (s) | Median (s) |
|---|---:|---:|---:|---:|
| JSONL typed | 9,464 | 8,959 | 8,424 | 8,959 |
| Bronze Snappy Parquet | 0,549 | 0,595 | 0,494 | 0,549 |

Thứ tự chạy JSON→Parquet, Parquet→JSON, JSON→Parquet. Đây là so sánh format cho query trên cùng dữ liệu; chưa phải benchmark baseline/optimized toàn batch, chưa có tuning partition/skew hoặc Airflow. Không so thời gian job ngày 2 với job này vì workload/phép kiểm khác nhau.

### Spark UI thật và cách tự xem

Ảnh được chụp bằng trình duyệt từ **Spark History Server đọc event log gốc** của application `local-1793030779262`; không phải ảnh dựng hoặc job đang chạy lại. [REST applications](evidence/chunk-ingest-medium/history-applications.json) ràng buộc ID/thời gian, [query 44 JSON](evidence/chunk-ingest-medium/history-query-44.json) và [query 45 Parquet](evidence/chunk-ingest-medium/history-query-45.json) lưu thông tin tương ứng. UI có thể hiển thị timezone browser; timestamp event/report giữ UTC.

![127 Spark jobs hoàn tất của lượt trung gian](evidence/chunk-ingest-medium/ui-jobs.jpg)

![Query 44: hai Scan JSON theo schema version](evidence/chunk-ingest-medium/ui-query-json.jpg)

![Query 45: Scan Parquet và ColumnarToRow](evidence/chunk-ingest-medium/ui-query-parquet.jpg)

JSON có hai nhánh scan v1/v2, mỗi nhánh 18 file. Parquet có tám file, vectorized scan và chỉ đọc schema cột cần query. Đây là cơ sở giải thích kết quả đo; con số “size of files read” trong UI là kích thước file liên quan, không tự bằng byte IO vật lý. DAG Parquet vẫn xuất 3.525.120 dòng trước Filter: chưa có bằng chứng partition/row-group pruning giảm số reading. Nén và tránh parse JSON có lợi trên workload này; chưa chứng minh mỗi cơ chế đóng góp bao nhiêu riêng lẻ.

Để tự mở lại lịch sử sau khi container ingest tự kết thúc (cần event log cục bộ):

```powershell
docker compose -f infra/ingest.compose.yaml --profile history up -d history
# Mở http://127.0.0.1:14041 rồi chọn application ID trong report.
docker compose -f infra/ingest.compose.yaml stop history
```

History profile chỉ bật khi gọi rõ, giới hạn 768 MiB/1 CPU và mount event log read-only. Live UI ở loopback14040 chỉ tồn tại khi ingest chạy; cấu hình bind UI đã đổi SPARK_LOCAL_IP từ 127.0.0.1 sang 0.0.0.0 trong container sau lượt medium, vẫn publish chỉ 127.0.0.1 trên host. Snapshot Compose của các run trước được giữ nguyên, không sửa hash lịch sử. Profile History được thêm sau các run để lấy ảnh từ log đã hoàn tất.

## Tự chạy nguồn trung gian và phần còn lại

Nguồn trung gian cấu hình [ingest-medium.json](../generator/configs/ingest-medium.json) là 200 máy × 288 giờ, một phút một reading; chạy offline-only để không tạo stream fixture không dùng cho batch:

```powershell
./.venv/Scripts/python.exe -m generator.chunked generate --config generator/configs/ingest-medium.json --output data/source/medium-practice --offline-only
./scripts/run-chunk-ingest.ps1 -RunName medium-practice -SourcePath data/source/medium-practice -SkipBuild
```

Đây là workload trung gian, không phải 100 GB. Byte nguồn, Parquet, event log và spill phải được ghi thật trước khi điều chỉnh ngân sách lớn. Gold/feature/warehouse và đỉnh scratch ở 100 GB vẫn chưa được đo. Giữ các phần chưa đo trong kế hoạch dung lượng; không lấy hệ số nén của tập trung gian để tuyên bố đã đạt quy mô hoặc chắc chắn đủ disk.

[Kế hoạch dung lượng cập nhật](evidence/chunk-ingest-medium/100gb-current-plan.json) dùng nguồn trung gian đã đo, giữ giả định bảo thủ cũ: cần **395.000.000.000 byte**, thực trống **346.017.071.104 byte** lúc chốt, `disk_budget_fits_now=false`. Đây vẫn là ước tính cho lần chạy tương lai; chưa cấp phép sinh lớn hoặc khẳng định hệ số output/shuffle thực ở 100 GB.

Tiếp theo là tách các bước ingest/validate/transform để đưa vào Airflow DP1–DP3 và lineage/assertions DataHub, cùng giao thức dữ liệu ML có tập test đủ ca hỏng. Mốc cuối tạm 31/10/2026 được giữ nguyên.

## Tài liệu chính thức của runtime

- [Spark 3.5.9 JSON Lines, schema và file source](https://spark.apache.org/docs/3.5.9/sql-data-sources-json.html).
- [Spark 3.5.9 Parquet, schema và compression](https://spark.apache.org/docs/3.5.9/sql-data-sources-parquet.html).
- [Spark 3.5.9 WebUI: SPARK_LOCAL_IP quyết định địa chỉ bind UI](https://github.com/apache/spark/blob/v3.5.9/core/src/main/scala/org/apache/spark/ui/WebUI.scala).
- [Spark 3.5.9 event log, History Server và task metrics](https://spark.apache.org/docs/3.5.9/monitoring.html).
- [Linux cgroup v2: memory.peak và memory accounting](https://docs.kernel.org/admin-guide/cgroup-v2.html).
