# Spark xử lý toàn nguồn 102,45 GB

Lượt `scale-ingest-100gb-27` đã **PASS lúc 11:02:09UTC27/10/2026** trên VM8vCPU/64GiB. Nguồn offline **102.447.847.856byte JSONL không nén**,1.000máy×220ngày/seed42, được kiểm checksum toàn bộ trước và sau xử lý. Đây là kết quả chạy thật, không ngoại suy từ tập trung gian và không cộng thêm stream/replay/bản sao.

[Verification](evidence/full-ingest/controller/verification.json), [Spark report](evidence/full-ingest/controller/ingest-report.json), [wrapper success](evidence/full-ingest/controller/_RUN_SUCCESS.json) và [lakehouse success](evidence/full-ingest/capture/lakehouse-_RUN_SUCCESS.json) đã đối chiếu SHA. [Manifest/map](evidence/full-ingest/capture/evidence-map.json) ràng buộc28tệp nhỏ native với archive gốc; data/event log lớn được giữ trên VM. [Capture](evidence/full-ingest/capture/capture-state.json) ghi máy và hash event log301.410.403byte.

## Điều đã được chứng minh

- **323.136.000Bronze rows →316.800.000Silver unique**, loại đúng **6.336.000extras**. Quy tắc giữ bản export mới nhất theo `event_id`; payload xung đột bị từ chối trong fixture.
- Đủ **13Sparkchecks**, gồm fixture biết đáp án, exact multiset readback, latest export, grain/FK, metadata readback và v1pressureNULL/v2pressurepresent. [136unit tests lúc bắt đầu run](evidence/full-ingest/controller/unit-tests.txt) cũng qua; không thay bằng số test tăng lên sau khi job đã khởi động.
- **45.852task attempts,0failed,128stages completed**, application-end thật trong [metrics](evidence/full-ingest/controller/spark-metrics.json). [History application](evidence/full-ingest/capture/history-application.json) đã `completed=true`; ứng dụng `local-1793077546565`.
- Năm bảng output có **4.099file Snappy Parquet/29.387.016.011byte**, kiểm codec và số hàng từ footer. Tổng logical output29.616.647.442byte gồm CRC/fixture/marker; không gọi chênh lệch này là dữ liệu nguồn.

| Bảng | Rows | File | Byte Parquet |
|---|---:|---:|---:|
| Bronze telemetry | 323.136.000 | 2.048 | 13.783.047.687 |
| Silver telemetry | 316.800.000 | 2.048 | 15.594.300.837 |
| Bronze machines | 1.000 | 1 | 12.957 |
| Bronze failures | 200.166 | 1 | 5.283.843 |
| Bronze maintenance | 263.217 | 1 | 4.370.687 |

Đây là Bronze/Silver plain Parquet. Gold/warehouse/feature/ML toàn100GB chưa chạy; các mốc feature/Feast/model60ngày có bằng chứng riêng. Full runner chưa gắn vào DAG Airflow; DP1–DP3 pilot đã chạy được ghi riêng, không gán thành full-scale Airflow.

## RAM, HDD và SSD

[Container limits](evidence/full-ingest/controller/container-limits.json), [preflight](evidence/full-ingest/controller/preflight.json) và [resource samples](evidence/full-ingest/controller/resource-samples.json): Spark6CPU/cap28GiB, heap20GiB/local[6],2.048shuffle/output partitions, AQEcoalescingfalse. Scratch nằm trên **HDD `/srv/iot-data/spark-scratch`**, nguồn read-only; SSD giữ Docker/image/platform, không gánh shuffle lớn.

| Số đo thực | Byte / giá trị |
|---|---:|
| Spark command gồm prehash/posthash/startup/shutdown | 22.509,780giây |
| Wrapper gồm unit/config/metrics/report | 22.520,569giây, khoảng6giờ15phút |
| Peak scratch qua9.116mẫu | 70.063.789.081byte |
| Peak output qua sampling | 29.616.647.442byte |
| HDD free thấp nhất qua sampling | 313.549.971.456byte |
| Host MemAvailable thấp nhất qua sampling | 15.026.688.000byte |
| Cgroup memory.peak trước shutdown | 30.064.775.168byte, xấp xỉ28GiB |
| Cumulative shuffle write / read | 189.572.345.836 /189.572.368.809byte |
| Disk spill / memory spill | 0 /0byte |
| Largest task peak execution memory | 1.308.622.560byte |

Sampling mỗi khoảng2giây có thể bỏ sót đỉnh ngắn; số mẫu không phải thời gian/2 chính xác. Cgroup memory gồm process/cache/kernel, không phải JVM heap hoặc host RSS; peak chạm mức cap, kể cả sai khác một trang4KiB trong accounting. Cumulative shuffle là tổng công việc của mọi check/query, không phải dung lượng scratch đồng thời.

Sau run, HDD còn **374.432.927.744byte**, SSD còn **122.422.669.312byte**, RAM khả dụng **57.505.325.056byte**; scratch của run bằng0byte. Vì vậy chưa cần thuê máy/đĩa mới để tiếp cluster và serving. Khi thêm dịch vụ vẫn đo admission theo runtime mới, giữ ít nhất24GiB RAM/40GBSSD cho bước cài cluster và giới hạn từng component. Phiên hiện tại vẫn tựSTOP12:42:37UTC; đĩa tính phí khi STOP. Budget người dùng cho phép không phải số credit trial thực đã được đọc.

[Forecast trước chạy](evidence/full-ingest/controller/preflight.json) dùng pilot cùng19file/image, HDDscratch và dự trù bảo thủ. Thực tế cần70,06GB peak scratch; không dùng số đo này để bỏ guard cho workload lớn hơn. CPU trong capture là **AMD EPYC 7B12**; pilot trước trên Broadwell, nên không coi tỷ lệ thời gian pilot/full là benchmark tối ưu cùng phần cứng. Trong run có các tác vụ feature/ML/platform dùng chung VM; số đo không phải thử nghiệm máy cô lập.

## So sánh cùng truy vấn

Mốc thời gian nguồn thuộc220ngày mô phỏng, không phải dữ liệu tương lai thu thập ngoài thực tế. Cùng dữ liệu có duplicate, cùng khoảng UTC `[2026-12-28,2027-01-08)`, phép count/sumtemperature/maxvibration; ba cặp đổi thứ tự chạy, không Spark cache. [Native SQL40–45](evidence/full-ingest/capture/history-query-44.json) và report giữ đầy đủ plan/timing:

| Lượt | JSONL, giây | Parquet, giây |
|---|---:|---:|
| 1 | 1.740,353 | 124,877 |
| 2 | 1.289,788 | 7,520 |
| 3 | 1.834,150 | 124,691 |
| Median | **1.740,353** | **124,691** |

Median khác nhau khoảng**13,96lần** cho query này. Cả hai trả **16.156.546rows**, maxvibration9,757 và tổngtemperature khớp trong tolerance đã khai báo. OS/file cache và ứng dụng nền chưa cô lập; lượt Parquet7,52giây cho thấy biến thiên lớn. Không suy ra tốc độ mọi query hoặc tổng batch đều tăng13,96lần.

![Jobs128đã hoàn tất](evidence/full-ingest/screenshots/jobs-completed.jpg)

UI5,9giờ là uptime của Spark application; wrapper6,25giờ còn gồm checksum trước khi tạo SparkSession và các bước bên ngoài application.

![Query44JSONL](evidence/full-ingest/screenshots/jsonl-query44.jpg)

![Query45Parquet](evidence/full-ingest/screenshots/parquet-query45.jpg)

Đây là ảnh thật từ History đọc event log gốc qua IAPSSH/loopback; [hash/cách chụp](evidence/full-ingest/screenshots/capture.json). JSONL plan union schema-v1/v2, Parquet có columnar scan. `PartitionFilters: []`; không nhận partition pruning hoặc z-order. Số `size of files read` trên UI là footprint tệp scan tham chiếu, không tự đồng nhất với physical I/O thực.

## Tự chạy và bàn giao

Giữ nguồn `data/source/scale-100gb-26`, output `data/lakehouse/scale-ingest-100gb-27`, receipt/log `outputs/scale-ingest-100gb-27` và event log `data/ingest-events/local-1793077546565` trên VM. Clone Git chỉ có code/config/report nhỏ, không mang dữ liệu hoặc dịch vụ.

Không chạy lại nguồn100GB chỉ để xem demo. Dùng [runner và pilot/admission](large-ingest-runner.md) trên source nhỏ trước; run lớn mới phải qua lại toàn bộ resource/time/source guards, dùng tên mới và còn đủ thời gian phiên thực được phép. Runner chưa resume run dở dang; không ghi đè output đã hoàn tất. Mốc tiếp theo [rubric final đã chốt](final-rubric-tracker.md) là notebook, distributed training, incremental data versioning, drift và vận hành. [KServe](kubernetes-serving.md), [KFP cluster](kfp-cluster.md) và [API feature](feature-api.md) đã có bằng chứng riêng; không tự thêm phạm vi LLM/agents từ tracker lịch sử.
