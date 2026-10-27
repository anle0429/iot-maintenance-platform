# Generator theo chunk: một lịch sử liên tục, RAM có giới hạn

Mục đích là bỏ nhu cầu giữ cả dataset trong RAM trước khi ghi nguồn. Mốc này chuẩn bị đường sinh/đọc kiểm cho 100 GB, dùng **JSONL không nén**, chưa phải Parquet hoặc bằng chứng đã sinh đủ 100 GB. Không thêm package vào host Python.

## Tự chạy

Từ repo root trong PowerShell 7, dùng `.venv` đã có theo README:

```powershell
./scripts/run-chunked.ps1 -RunName practice-chunks
Get-Content outputs/practice-chunks/chunked-report.json
```

Script chạy unit tests, sinh/đọc kiểm pilot, lặp lại cùng cấu hình, đổi kích thước chunk, đo thêm lịch sử dài gấp bốn, lập hai ngân sách đĩa và kiểm tương thích nguồn legacy. Không bật Docker hoặc dừng ứng dụng/container khác. Mỗi lượt chọn tên mới; source ở `data/source/<run>/`, báo cáo ở `outputs/<run>/`, ngoài Git. Máy cần ít nhất 512 MiB RAM trống và 4 GiB đĩa cho protocol pilot mặc định; đây là guard tối thiểu cho tập nhỏ, không phải sizing cho 100 GB.

Chạy từng bước để học:

```powershell
./.venv/Scripts/python.exe -m generator.chunked generate --config generator/configs/chunked-pilot.json --output data/source/manual-chunks --chunk-rows 4096
./.venv/Scripts/python.exe -m generator.chunked inspect --source data/source/manual-chunks --report outputs/manual-chunks-quality.json
./.venv/Scripts/python.exe -m generator.chunked plan --source data/source/manual-chunks --target-offline-gb 100 --offline-only --report outputs/manual-chunks-plan.json
```

`plan` chỉ tính dự trù. Xem riêng `disk_budget_fits_now` và `generation_authorized_by_plan`; `passed=true` của plan nghĩa là tạo được báo cáo ước tính hợp lệ. Nó không có nghĩa dataset đích đã tồn tại hoặc được phép chạy lớn.

## Vì sao chunk không làm sai mô phỏng?

[Iterator vật lý](../generator/simulation.py) giữ wear, tải, RNG, repair và maintenance riêng từng máy. Writer luân phiên lấy reading của các máy ở cùng bước thời gian. Đóng file không tạo lại máy, không đổi seed và không bắt đầu lại lịch bảo trì. Hai fixture có hash chụp trước refactor bảo vệ thứ tự RNG và giá trị cũ; kiểm byte nguồn ngày 1 được ghi riêng trong bằng chứng.

Ba [bộ chọn lỗi](../generator/chunked.py) giữ `remaining` và `needed` cho **toàn run**, với RNG riêng cho late/offline duplicate/stream retry. Ở mỗi reading, xác suất chọn là `needed / remaining`; chọn xong giảm needed, luôn giảm remaining. Nhờ vậy chọn đúng `floor(N × rate)` vị trí, không lưu một set ID cỡ N và không làm tròn lại theo từng chunk. Ví dụ N=120, rate=1,9% phải có **2** bản offline thêm, kể cả chunk chỉ chứa một dòng. Số lượng dùng phân số chính xác từ biểu diễn thập phân của rate: 180 × 0,7 phải ra 126, tránh float nhị phân cho 125,999999 rồi floor nhầm thành 125.

Phương thức chọn transport mới được ghi là `sequential-exact-1`, khác cách `random.sample` của lệnh cũ. Cùng cấu hình vật lý giữ event_id/giá trị đo/sự cố, nhưng các ID được chọn late/retry có thể khác giữa hai chế độ. **Không trộn hai nguồn legacy/chunked như một run**; phải giữ provenance của source/manifest. Thay chunk_rows trong cùng chế độ mới không đổi lựa chọn lỗi.

## Bố cục nguồn

```text
manifest.json                         Config, totals và hash catalog
files.jsonl                           Một dòng metadata/checksum cho mỗi part
_GENERATION_SUCCESS.json              Ghi xong, ràng buộc hash manifest
machines/part-000000.jsonl
maintenance_events/part-000000.jsonl
failure_events/part-000000.jsonl
telemetry/schema_v1/part-000000.jsonl
telemetry/schema_v2/part-000000.jsonl
stream/part-000000.jsonl
```

Mỗi table đánh số part từ 0. `chunk_rows` giới hạn **số dòng đã lưu**, gồm cả duplicate. Một cặp original/duplicate có thể nằm hai file khác nhau. Part rỗng không được tạo; v1/v2 vẫn tách thật theo event time. Trong mỗi partition telemetry, thứ tự là event time rồi số máy; nối v1 trước v2 sẽ được toàn bộ grid. `files.jsonl` là catalog khi file đóng, không phải thứ tự replay giữa các table.

Writer ghi từng dòng, chỉ giữ buffer IO cố định. Catalog và SHA-256 được ghi/tính dần; không nạp file lớn bằng `read_bytes()`, không giữ toàn bộ danh sách part trong RAM. `logical_sha256` của một table băm các byte dữ liệu nối theo thứ tự part, nên đổi cách chia file vẫn đối chiếu được dữ liệu logic.

`_GENERATION_SUCCESS.json` chỉ xác nhận hoàn tất ghi nguồn. Phải chạy `inspect` và kiểm exit code/report `passed=true` trước khi dùng nguồn làm evidence. Report đặt ngoài source. Source đã tồn tại bị từ chối ghi đè; khi hết giới hạn dung lượng, phần chưa hoàn tất được giữ để chẩn đoán, không có success marker. Chưa có resume/checkpoint qua process crash.

## Stream và validator không giữ toàn bộ ID

Stream dùng min-heap theo `(published_ts, delivery_id)`. Sau khi lấy xong các máy ở một event time, có thể ghi mọi delivery đã tới trước/đúng mốc đó. Những event tương lai không thể có published_ts nhỏ hơn event_ts của chúng. Phần còn giữ trong heap chỉ phụ thuộc số máy và horizon late/retry; mọi part stream nối lại có arrival order toàn cục.

[Validator](../generator/chunk_quality.py) thực sự đọc lại source:

- Đọc catalog/part tuần tự, kiểm checksum, bytes, số dòng, part bound, file thừa và success marker; không theo symlink ra ngoài run.
- Dựa trên contract grid có thứ tự để kiểm đủ từng `(machine_id,event_ts)`, ID và duplicate kề nhau, gồm cả cặp nằm hai part; không xây set toàn bộ event_id.
- Merge sự cố/bảo trì theo thời gian, giữ interval gần nhất mỗi máy để kiểm motor, censoring và recorded time. Đo lại skew/schema/late/duplicate trên toàn run.
- Join stream với original offline trong horizon arrival/retry hữu hạn. Mỗi reading phải có đúng primary; retry phải đúng payload, ID, attempt và timestamp, không lặp attempt. Không chấp nhận chỉ khớp tổng số đếm.

RAM vì vậy phụ thuộc **số máy + horizon đến muộn + buffer IO**, không phải danh sách toàn lịch sử. Config giới hạn 10.000 machine states; guard buffer mặc định 250.000 deliveries. Với cấu hình làm buffer ước lượng vượt guard, generator từ chối trước ghi; `--offline-only` bỏ stream và chi phí heap. Số machine/horizon vẫn cần sizing khi đổi workload.

Lệnh cũ `python -m generator` vẫn giữ guard 100.000 unique rows. Lệnh mới `python -m generator.chunked` có hard cap mặc định **2 GiB tổng output**, kiểm reserve đĩa mặc định 1 GiB khi mở part mới. Reserve không khóa dung lượng trước; phải đo lại filesystem trước lượt lớn. Không tự tăng cap hoặc sinh 100 GB trong mốc pilot này.

## Kế hoạch dung lượng và giới hạn tích hợp

[Planner](../generator/capacity.py) lấy byte offline thật/unique từ pilot, dùng 100 GB = **100.000.000.000 byte**. Dự trù tách nguồn (có/không stream, thêm 20% margin), processed outputs 1,5×offline, shuffle 1×offline, log 5 GB và reserve 20 GB. Đây là các giả định bảo thủ để review, chưa phải footprint Spark đo trên workload lớn. Kích thước dòng/tần suất sự cố thay đổi theo horizon; cần tập trung gian và đo ingest/shuffle trước lượt lớn.

Output mới chưa được nối vào `source_loader`/batch job/Flink driver cũ: các job đó vẫn dùng layout nguồn ngày 1 và một phần còn materialize trong RAM. **Mốc tiếp nối 26/10:** [adapter catalog và Spark ingest riêng](chunk-ingest.md) đã đọc nguồn liên tục 1.117.608.156 byte offline vào Bronze/Silver Snappy Parquet, có exact readback, query/footprint/spill và UI thật. Chưa Gold/feature/Feast/Airflow cho nguồn lớn; vẫn cần ngân sách nguồn + output + đỉnh shuffle + log trước 100 GB. Không thể suy từ generator mới rằng toàn pipeline đã xử lý được 100 GB.

## Kết quả kiểm chứng và cách đọc số đo

Mốc bàn giao **23/10/2026**, run `chunked-release` trên THIENAN/Ryzen 5 5500/CPython 3.13.14: **74 unittest qua**; pilot và scale đều **19/19 kiểm tra readback qua**. Timestamp UTC thực của lượt chạy giữ trong [report](evidence/chunked/chunked-report.json), cùng base commit `f739e66` và hash từng file code khi thay đổi chưa commit. Không chạy lại benchmark Spark/Flink trong mốc này.

| Đo thực tế, cùng 40 máy/seed42/sample60s | Pilot 48 giờ | Scale 192 giờ |
|---|---:|---:|
| Unique readings | 115.200 | 460.800 |
| Offline JSONL, gồm metadata và bản xuất thêm | 37.263.211 byte | 149.023.279 byte |
| Toàn source, gồm stream/catalog/manifest/marker | 87.177.655 byte | 348.651.626 byte |
| Thời gian generate | 7.737s | 30.714s |
| Thời gian inspect | 8.079s | 24.418s |
| Peak working set cây process lúc generate | 50.94 MiB | 51.29 MiB |
| Peak working set cây process lúc inspect | 49.62 MiB | 51.00 MiB |
| Pending deliveries cao nhất lúc generate | 103 | 110 |
| Pending events cao nhất lúc join readback | 69 | 72 |

Mỗi source là **một lịch sử liên tục**, không ghép các seed/shard độc lập. Scale đổi horizon thành gấp bốn; không cộng dung lượng nhiều run để gọi là một nguồn 100 GB. Mỗi phép đo chỉ một lượt: lấy mẫu tổng working set launcher và descendants, chờ 50 ms và khám phá PID con mỗi ít nhất 250 ms. Đây là đỉnh quan sát, có thể bỏ lỡ spike/đếm trùng shared pages; không cô lập cache hoặc ứng dụng nền. Kết quả cho thấy RAM quan sát gần nhau ở hai cỡ đã chạy, không chứng minh mọi cấu hình hoặc 100 GB sẽ dùng đúng chừng đó RAM.

Pilot có 2.304 offline extras/115.200 unique =2%, 5.760 late=5%, 1.728 retries=1,5%; v1/v2 mỗi loại57.600 unique, F-01 chiếm80.640/115.200=70%. Có 65 failures/77 maintenance events; validator kiểm trạng thái motor với các interval này. Cardinality event_id đã dedup=115.200, machine_id=40, event_ts=2.880; trên117.504 telemetry rows lưu, event_id unique/rows=98,0392%.

- [Pilot quality](evidence/chunked/pilot-quality.json) và [scale quality](evidence/chunked/scale-quality.json) là kết quả đọc lại byte/record thật.
- [Repeat](evidence/chunked/repeat-comparison.json): cùng config/chunk4096 giống toàn bộ source bytes, gồm manifest/marker.
- [Khác chunk](evidence/chunked/different-chunks-comparison.json): đổi4096→257 dòng, logical SHA-256 của cả sáu table không đổi; file/catalog/manifest khác là đúng vì cách chia file khác.
- [Legacy regression](evidence/chunked/legacy-regression.json): cả7file nguồn ngày1 vẫn giống từng byte sau khi dùng chung iterator vật lý.
- [Unit tests](evidence/chunked/unit-tests.txt) có cả corruption được cập nhật checksum để chứng minh validator còn kiểm nghiệp vụ, không chỉ checksum; [kiểm cuối](evidence/chunked/final-verification.json) đối chiếu hash code/bytes/rubric/repo.

Kế hoạch100GB [offline-only](evidence/chunked/100gb-offline-only-plan.json) dự trù **395.000.000.000 byte**, đĩa lúc đo trống **390.963.044.352 byte**, `disk_budget_fits_now=false`. [Có stream](evidence/chunked/100gb-with-stream-plan.json) cần **555.741.200.752 byte** theo cùng giả định. Chưa sinh100GB; cần đo/giảm footprint hoặc có đủ dung lượng trước chạy lớn, không bỏ reserve/cap chỉ để qua guard.

Kết quả chính nằm dưới `data/source/chunked-release`; lượt thử trước và lượt terminal bị ngắt giữ riêng trong data/outputs, không dùng làm bằng chứng chính. Script lưu số đo từng bước trong outputs để tránh mất toàn bộ phép đo khi terminal ngắt; report tổng chỉ xuất khi toàn protocol thành công.


**Tiếp nối mốc nguồn100 GB (26–27/10/2026):** [Nguồn liên tục trên VM](scale-100gb.md) đã sinh và đọc kiểm toàn bộ **102.447.847.856 byte offline**, 316.800.000 unique readings,14 quality rules; Spark trung gian5,59 GB/13 checks và99 unit qua. Kết quả100 GB thuộc nguồn JSONL không nén, chưa là Spark/Gold/feature/ML hoặc Airflow toàn100 GB. Ngân sách mới tách HDD400 GB/SSD35 GB; trước Spark lớn cần kiểm lại và đặt scratch trên HDD. Các kết quả nhỏ phía trên giữ nguyên scope/thời điểm lịch sử.
