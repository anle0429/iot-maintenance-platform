# Nguồn offline 100 GB trên VM

Mốc này tiếp tục Airflow/DataHub trên cùng VM 8 vCPU/64 GiB. Mục tiêu là **một nguồn liên tục có ít nhất 100.000.000.000 byte offline thật**, rồi đọc kiểm đầy đủ; không cộng nhiều nguồn/seed để đủ dung lượng. Đã hoàn tất **102.447.847.856 byte offline**, 316.800.000 readings không trùng, đầy đủ checksum và 14 quy tắc semantic readback. [Kiểm chứng tổng](evidence/scale-source/verification.json), [report nguồn lớn](evidence/scale-source/scale-100gb-26/verification.json).

## Tại sao phải tính lại?

Airflow/DataHub và image đã chiếm thêm SSD. Snapshot đầu lượt: HDD trống khoảng 520,30 GB, SSD 133,84 GB, RAM khả dụng 56,19 GiB. Ngân sách cũ đặt 100 GB shuffle+15 GB tăng trưởng platform+20 GB dự phòng trên SSD cần 135 GB, không còn vừa.

Ngân sách mới tách theo đĩa:

Trong tài liệu này, GB =1.000.000.000 byte, GiB =1.073.741.824 byte. Đĩa GCE được cấp theo GiB; dung lượng trống và payload được kiểm bằng byte thực. RSS là bộ nhớ process, còn `MemAvailable` là RAM host có thể cấp thêm, có tính phần cache có thể thu hồi.

| Đĩa | Khoản dự trù | Byte |
|---|---|---:|
| HDD | Hard cap nguồn, gồm manifest/catalog |115.000.000.000|
| HDD | Output xử lý trong tương lai |150.000.000.000|
| HDD | Shuffle cho lượt Spark lớn trong tương lai |100.000.000.000|
| HDD | Log |5.000.000.000|
| HDD | Khoảng trống giữ lại |30.000.000.000|
| **HDD tổng** | |**400.000.000.000**|
| SSD | Tăng trưởng platform 15 GB + khoảng trống 20 GB |**35.000.000.000**|

Đây là **dự trù**, không phải footprint đã đo của Spark 100 GB. Source runner giữ 285 GB HDD cho các khoản sau nguồn và kiểm lại free space liên tục. Lượt Spark 100 GB sau này cần chuyển scratch sang HDD theo ngân sách này; launcher `optimized` hiện vẫn dùng SSD và không được tự nhận rằng ngân sách SSD đã đủ. Lượt Spark trung gian dùng SSD, có đo scratch riêng.

RAM: trong protocol đã đo, nguồn được đặt giới hạn systemd 4 GiB/1 CPU; Spark trung gian dùng 6 CPU/heap 20 GiB/container 28 GiB. Hai lượt chạy tuần tự. Gate giữ ít nhất 36 GiB RAM khả dụng trước lượt lớn để còn dư địa Spark 28 GiB +8 GiB hệ thống; mức này không phải dự báo chính xác RAM cho feature join 100 GB. Chạy pilot trực tiếp bằng CLI bên dưới có guard byte/RAM/thời gian của script; giới hạn cgroup 4 GiB chỉ áp dụng khi được bọc bởi systemd như protocol này.

Generator hiện là một process Python: tăng số vCPU không tự chia mô phỏng ra nhiều core. VM 8 vCPU/64 GiB cung cấp dư địa cho Spark và platform; lượt nguồn ưu tiên giữ đúng một history, giảm chi phí timestamp/JSON đã đo và kiểm chứng toàn bộ dữ liệu. RSS worker thấp không có nghĩa toàn VM chỉ dùng lượng RAM đó; cgroup còn tính controller/cache và các dịch vụ có bộ nhớ riêng.

[Snapshot systemd khi nguồn đang chạy](evidence/scale-source/systemd-limits.txt) ghi `MemoryPeak=4294967296`, bằng 4 GiB cap, và CPU quota 1 core. Đây là cả nhóm tiến trình/page cache; không mâu thuẫn với RSS worker khoảng 31 MiB được quan sát trong generation. Dự trù RAM vẫn dùng cap 4 GiB và RAM khả dụng của host, không thay cap bằng RSS nhỏ.

## Thứ tự kiểm chứng

1. Chạy cùng cấu hình 200 máy ×24 giờ: bản gốc, cache timestamp và cache+codec JSON. Đối chiếu manifest/part hashes và semantic readback, không chỉ so số dòng.
2. Sinh một lịch sử 200 máy ×1.440 giờ, khoảng 5,6 GB; đọc kiểm toàn bộ catalog/SHA, grid máy/thời gian, schema, duplicate, late và stop truth.
3. Spark đọc chính source/receipt đó, ghi Bronze/Silver, chạy 13 checks, kiểm kết quả query hai định dạng. Lấy cgroup memory, spill, output bytes và scratch quan sát mỗi giây.
4. Dùng số đo trung gian để ước tính thời gian cho cấu hình 1.000 máy ×5.280 giờ (**220 ngày mô phỏng**, không phải thời gian thực phải chờ). Một nguồn có 316.800.000 unique readings; metadata/failure/maintenance cùng quá trình vật lý. Mốc đổi firmware nằm giữa horizon, duplicate 2%, late 5%.
5. Chỉ khi HDD/SSD/RAM/thời gian đều qua gate mới sinh lớn. Giữ hệ số thời gian 1,2 và 20 phút dự phòng so với thời điểm VM tự STOP đã đọc từ GCE. Không đổi giới hạn 8 giờ.
6. Chỉ ghi success khi byte offline thực tế ≥100 GB và full readback qua. Byte nguồn không tính catalog/manifest, không tính stream, không lấy kích thước Parquet hoặc ước tính thay cho số đo.

## Tối ưu giữ nguyên dữ liệu

`pipelines/source_runtime.py` dùng cache 4096 entries cho phép chuyển timestamp thuần, cache timestamp cấu hình bất biến, và tùy chọn `orjson==3.13.0` để đọc/ghi JSONL. Cache có giới hạn và được tháo khi kết thúc scope; chỉ dùng trong process nguồn/validator riêng. Không sửa generator vật lý hoặc hash/version của validator cũ, nên receipt nguồn trước vẫn có hiệu lực.

Codec nhanh giữ sorted keys/newline cho các scalar ASCII của nguồn. Unicode, nested payload, số ngoài miền thông thường và nonfinite đi qua encoder stdlib gốc; nonfinite vẫn bị từ chối. Reader vẫn giới hạn 16 KiB/dòng và chỉ nhận JSON object. Tests so **toàn bộ byte file** và quality report của source nhỏ, cả stream-envelope fallback; thêm test phục hồi alias sau exception và các guard dữ liệu lỗi.

Tham khảo chính thức: [orjson](https://github.com/ijl/orjson). Chỉ dùng dependency này vì đo được chi phí JSON, không cài vào môi trường Airflow/Spark. Mỗi phép đo calibration chỉ một lượt/mode; cache hệ điều hành và platform nền không cô lập, không tuyên bố speedup ổn định.

## Số đo calibration và tập trung gian

Ba lượt calibration dùng cùng 200 máy ×24 giờ, seed 42, sample 60 s, 288.000 readings không trùng và **93.139.057 byte offline**. Manifest/SHA của mọi part giống nhau ở cả ba chế độ; mỗi lượt đọc kiểm đầy đủ đều qua 14 quy tắc.

| Chế độ | Sinh nguồn (s) | Đọc kiểm (s) | Peak RSS khi sinh (byte) |
|---|---:|---:|---:|
| Gốc |9,827281|5,170372|24.403.968|
| Cache timestamp/config |7,378223|4,517091|26.238.976|
| Cache + orjson |6,193026|3,152919|26.488.832|

Đây là thời gian process thực hiện từng phase; chưa gồm launcher/poll/setup. [So sánh gốc](evidence/scale-source/calibration-comparison.json) giữ thời gian CPU/RSS và từng report. Hai lượt đầu chạy runtime trước khi bổ sung codec; [snapshot module gốc](evidence/scale-source/calibration-runtime-v1/pipelines__source_runtime.py.txt) và [launcher gốc](evidence/scale-source/calibration-runtime-v1/scripts__run-scale-source.py.txt) được trích từ archive thực, đối chiếu preflight SHA. Bản nhanh và các nguồn sau dùng [runtime mới](evidence/scale-source/runtime.json). Không đổi mô phỏng vật lý hoặc dữ liệu để đạt tốc độ.

Nguồn `scale-intermediate-26`, 200 máy ×1.440 giờ: **5.587.984.353 byte offline**, 17.280.000 unique readings, 17.625.600 dòng telemetry lưu gồm 345.600 bản xuất thêm. Sinh 353,302 s, đọc kiểm 288,706 s; peak RSS của hai process lần lượt 28.098.560/24.535.040 byte. [Receipt](evidence/scale-source/scale-intermediate-26/receipt.json) kiểm đủ checksum/grid, v1/v2, duplicate/late và trạng thái sự cố/bảo trì; [verification](evidence/scale-source/scale-intermediate-26/verification.json) ràng buộc manifest SHA.

Spark đọc **chính nguồn và receipt đó**, local[6], heap 20 GiB/container 28 GiB, 32 partitions, Spark 3.5.9. **13 kiểm tra Spark và 99 unittest qua**, Bronze 17.625.600 → Silver 17.280.000, loại 345.600 extras. Job 385,070 s; tổng wrapper/monitor 435,581 s. Năm table có 131 file Snappy Parquet, tổng 1.302.032.631 byte. Đây là một lượt optimized, chưa có baseline cùng workload 5,59 GB.

| Tài nguyên đo trên lượt Spark trung gian | Kết quả |
|---|---:|
| Cgroup `memory.peak` |21.150.318.592 byte|
| Memory/disk bytes spilled, cộng task |0 /0|
| Shuffle read/write, cộng task |9.239.453.022 /9.239.452.411 byte|
| Scratch peak quan sát mỗi giây |4.630.239.746 byte|
| Host RAM khả dụng thấp nhất khi lấy mẫu |44.782.653.440 byte|
| SSD trống thấp nhất khi lấy mẫu |129.204.269.056 byte|

**Không spill vẫn cần scratch**: các file shuffle vẫn được ghi xuống đĩa. Cgroup memory gồm accounting process/cache/kernel, khác JVM heap hoặc RSS. Mẫu 1 giây có thể bỏ qua spike; shuffle cộng task khác đỉnh dung lượng đồng thời. [Report](evidence/scale-source/intermediate-spark/ingest-report.json), [metrics](evidence/scale-source/intermediate-spark/spark-metrics.json), [mẫu tài nguyên](evidence/scale-source/intermediate-spark/sampled-resources.json), [99 tests trên VM](evidence/scale-source/intermediate-spark/unit-tests.txt).

Lưu ý đọc evidence: câu `scope` chung trong `cloud-context.json` còn mô tả mặc định 16 partitions, còn `ingest-report.json.scope` ghi mặc định Local[2]. Giá trị thực là `shuffle_partitions=32`, `threads=6`, khớp lệnh `--partitions 32` và `--master local[6]`. Giữ nguyên log gốc, dùng các trường cấu hình/lệnh thực thi để đối chiếu. Hash `source_bundle_manifest_sha256` trỏ tới [manifest runtime v3 đã lưu](evidence/gcp-runtime/runtime-source-v3-manifest.json); image ID và launcher SHA được ghi riêng trong context. Mốc này không đổi mã Spark/generator cốt lõi của snapshot đó.

32 partitions vẫn tạo 64 file cho mỗi bảng telemetry vì writer đặt `maxRecordsPerFile=500000`: mỗi partition có hơn 500.000 dòng và tách thành hai file; thêm 3 file metadata thành 131 file. Silver 693.321.312 byte lớn hơn Bronze telemetry 607.934.240 byte trong lượt này dù loại 2% extras; số byte còn phụ thuộc thứ tự/encoding/nén, chưa có phép đo tách riêng nguyên nhân. Không dùng số dòng giảm để tự kết luận tối ưu storage đã hoàn tất.

Manifest bundle v3 gốc trên VM dùng CRLF; Git lưu bản LF theo `.gitattributes`. Do đó hash metadata trong context là `ba3d2fc5…`, còn blob Git là `92a8c66c…`. [Ánh xạ đầy đủ](evidence/scale-source/git-source-mapping.json) kiểm phép đổi line endings này, tách khỏi21 file thực thi khớp nguyên byte. Các manifest/catalog/receipt nguồn mới đều giữ byte LF và các marker vẫn khớp; không thay hash nguồn để bỏ qua kiểm tra.

## Giới hạn phiên và lần khởi động lại thật

Sau tập trung gian, plan cũ qua HDD/SSD/RAM nhưng **không đủ thời gian phiên**. [Plan bị chặn](evidence/scale-source/vm-restart/rejected-old-session-plan.json) không được dùng để bắt đầu sinh lớn. Đã kiểm 0 DAG run đang hoạt động và 0 Spark container, sau đó STOP/START **cùng VM/đĩa/cấu hình**; giới hạn 28800 giây và hành động STOP giữ nguyên. Đây là kiểm khởi động lại thủ công, chưa phải bằng chứng đã chờ tự STOP sau 8 giờ.

[Trước](evidence/scale-source/vm-restart/before-restart.json) / [sau](evidence/scale-source/vm-restart/after-restart.json): hai mount phục hồi,9 dịch vụ cùng image ID,3 DAG không lỗi import và GMS HTTP 200. Nguồn 5,59 GB đã [rehash mọi part sau boot](evidence/scale-source/fresh-session/source-after-restart.json). [GCE đã lược định danh](evidence/scale-source/vm-restart/sessions.json) ghi CPU platform đổi AMD Rome →Intel Broadwell dù cùng `e2-highmem-8`; không bảo đảm throughput calibration trước boot sẽ giữ nguyên.

Phiên mới bắt đầu **21:49:09.924 UTC 26/10/2026**, tự STOP dự kiến **05:49:09.924 UTC 27/10**. [Plan mới](evidence/scale-source/fresh-session/100gb-plan.json) đo HDD 513.096.605.696 byte, SSD 133.833.076.736 byte trống và RAM 61.881.991.168 byte khả dụng. Bốn gate qua; dự báo nguồn 102.446.379.805 byte và sinh+đọc kiểm 14.124,180 s đã nhân 1,2, thêm 20 phút kết thúc. Source lớn bắt đầu 21:52:43 UTC; kết quả đo thật nằm trong phần tiếp theo, không thay thế bằng forecast này.

## Kết quả nguồn lớn đã đo

Run `scale-100gb-26` trên cùng VM, sau lần STOP/START được ghi ở trên, dùng Python 3.12.3/orjson 3.13.0 và [21 file runtime đã fingerprint](evidence/scale-source/runtime.json). Cấu hình [1000 máy ×5280 giờ](../generator/configs/scale-100gb.json), sample 60 s/seed 42; đây là 220 ngày mô phỏng của **một history liên tục**. Các timestamp ngoài ngày chạy là thời gian mô phỏng, không phải dữ liệu nhà máy thật đã quan sát.

| Số đo thực tế | Kết quả |
|---|---:|
| Offline payload JSONL, UTF-8/LF, không nén |**102.447.847.856 byte**|
| Toàn source, gồm catalog/manifest/marker |102.448.083.672 byte|
| Allocation của các payload file trên ext 4 |102.450.417.664 byte|
| Payload parts / tổng files |1.298 /1.301|
| Unique readings / telemetry rows lưu |316.800.000 /323.136.000|
| Sinh nguồn: wall /CPU |7.802,887 /7.788,021 s|
| Đọc kiểm: wall /CPU |5.375,480 /4.577,375 s|
| Toàn wrapper, gồm setup/poll |13.179,855 s|
| Peak RSS process sinh /đọc kiểm |32.169.984 /54.820.864 byte|
| RAM khả dụng thấp nhất, mẫu 2 s |60.872.949.760 byte|
| HDD trống thấp nhất, mẫu 2 s |410.645.180.416 byte|

Đây là byte thực từ file, vượt 100.000.000.000 byte; không cộng các run nhỏ, không tính stream, không lấy dung lượng Parquet hoặc tổng byte đã đọc hai lượt để đạt mốc. Toàn bộ payload đã được đọc checksum/logical hash, rồi đọc semantic để kiểm grid/ID/nội dung và metadata. Collector chỉ đối chiếu thêm stat/mtime/catalog sau readback, không thực hiện lượt quét payload thứ ba. [Receipt14 checks](evidence/scale-source/scale-100gb-26/receipt.json), [inventory](evidence/scale-source/scale-100gb-26/source-inventory.json), [catalog mỗi part](evidence/scale-source/scale-100gb-26/source-files.jsonl), [protocol success](evidence/scale-source/fresh-session/_PROTOCOL_SUCCESS.json).

Các đặc tính được đọc kiểm trên toàn nguồn:

- Skew factory: F-01=221.760.000, F-02=47.520.000, F-03=47.520.000; F-01 chiếm 70% unique readings. Đây là skew nguồn, chưa là benchmark tối ưu skew Spark.
- Schema v1/v2: 158.400.000 /158.400.000 unique readings. V1 không có trường pressure trong JSON, v2 có; schema Spark gộp dùng pressure nullable.
- Offline extras: **6.336.000 /316.800.000=2%**. Nếu lấy tổng dòng lưu làm mẫu số thì extras/rows≈1,960784%; event_id distinct/rows≈98,039216%.
- Late unique: **15.840.000=5%**, delay min/max 300,000 /600,000 s. Stream không được sinh trong run này, nên không nhận bằng chứng Kafka/Flink 100 GB từ các timestamp offline.
- Trạng thái unique: failed=9.006.627, maintenance=5.264.200, operating=302.529.173. Có 200.166 failure và 263.217 maintenance events; motor status/censoring/recorded time khớp intervals. Sự cố xuất phát từ quá trình hao mòn, không gán nhãn ngẫu nhiên độc lập.
- Cardinality suy từ grid/ID đầy đủ đã kiểm: event_id=316.800.000, machine_id=1.000, event_ts=316.800. Không xây set hàng trăm triệu ID trong RAM để đo. Trên 323.136.000 dòng telemetry lưu, tỷ lệ distinct/rows của machine_id≈0.000309467% và event_ts≈0.098039%; không gồm các bảng metadata.

Dữ liệu và báo cáo gốc nằm trên VM tại `data/source/scale-100gb-26` và `outputs/scale-100gb-26`. Git chỉ giữ code/config/báo cáo/catalog nhỏ; laptop clone repo sẽ không tự có 100 GB này. [Đối chiếu runtime với Git](evidence/scale-source/git-source-mapping.json) ràng buộc working-tree execution với nội dung source đã commit, gồm snapshot hai module calibration đời trước.

Mốc kết thúc lúc 01:32:23 UTC 27/10/2026, toàn wrapper khoảng 3 giờ 39 phút 40 giây. [Kiểm sức khỏe cuối](evidence/scale-source/final-health.json) và [API/container readback](evidence/scale-source/final-services.json) xác nhận 9 dịch vụ cùng image,3 DAG/0 import errors, scheduler/dag-processor/database healthy và GMS HTTP thành công. Hai mount sẵn sàng; sau job HDD 410.644.516.864 B/SSD 133.822.328.832 B trống, RAM 60.981.194.752 B khả dụng.

Unit tạm đã được systemd thu hồi sau thành công: [trạng thái sau job](evidence/scale-source/unit-after.txt) có `LoadState=not-found`, `ActiveState=inactive`. Các giá trị infinity/default được `systemctl show` trả sau đó trong final-health không phải giới hạn lúc chạy; cờ `source_unit_status_available` chỉ phản ánh lệnh đọc trả exit 0. Giới hạn thực thi được lưu ở [snapshot active](evidence/scale-source/systemd-limits.txt):4 GiB/1 core. Giữ nguyên các báo cáo gốc để đối chiếu.

## Cách tự chạy

Trên VM đã chuẩn bị hai volume và Spark image, trong repo root:

```bash
python3 -m venv data/runtime/scale-venv
data/runtime/scale-venv/bin/python -m pip install -r requirements-scale.txt
data/runtime/scale-venv/bin/python -m unittest discover -s tests -q
```

Source runner ghi vào `data/source/<run-name>` và `outputs/<run-name>`, từ chối ghi đè. Mặc định vẫn cap 2 GiB. Ví dụ pilot mới:

```bash
data/runtime/scale-venv/bin/python scripts/run-scale-source.py \
  --run-name my-scale-pilot --config generator/configs/scale-calibration.json --fast-json
```

Lượt vượt 2 GiB bắt buộc có `--maximum-bytes`, `--estimated-seconds` và `--deadline-utc` phù hợp phiên VM hiện tại. Không dùng lại thời điểm STOP của protocol ngày 26/10 cho phiên khác. Script `continue-scale-protocol.py` lưu chính chuỗi kiểm chứng của mốc này với tên run/thời điểm đã kiểm; nó không phải scheduler định kỳ hoặc lệnh để ghi đè dữ liệu cũ.

Progress có phase, byte đã ghi, elapsed và tài nguyên. `_GENERATION_SUCCESS.json` chỉ xác nhận ghi nguồn xong; còn phải chờ receipt, `verification.json` và `_RUN_SUCCESS.json` của wrapper mới coi toàn mốc đã qua. Nếu dừng giữa generation thì giữ source dở dang để điều tra, không ghép phần tiếp nối. Nếu source đã hoàn tất nhưng validation chưa xong, có thể đọc kiểm lại nguồn bất biến với receipt mới; không sửa marker hoặc giả kết quả PASS.

Preflight dành 20 phút kết thúc ngoài forecast đã nhân 1,2. Trong lúc chạy, guard kiểm mỗi 2 giây và chủ động dừng khi chỉ còn 10 phút trước deadline hoặc chạm reserve RAM/HDD; source chưa hoàn tất được giữ nguyên để điều tra. Guard này bảo vệ dữ liệu và thời gian thu báo cáo, không làm nguồn dở dang trở thành PASS.

Sau khi run lớn hoàn tất, kiểm nhanh các báo cáo/marker trên VM:

```bash
data/runtime/scale-venv/bin/python - <<'PY'
import hashlib, json
from pathlib import Path
p = Path('outputs/scale-100gb-26')
raw = (p / 'verification.json').read_bytes()
v = json.loads(raw)
receipt = json.loads((p / 'receipt.json').read_text())
q = receipt['quality']
marker = json.loads((p / '_RUN_SUCCESS.json').read_text())
assert marker['verification_sha256'] == hashlib.sha256(raw).hexdigest()
assert v['passed'] and q['passed'] and all(q['checks'].values())
assert v['source_manifest_sha256'] == receipt['source_manifest_sha256']
assert v['actual_offline_payload_bytes'] == q['offline_payload_bytes']
assert v['actual_offline_payload_bytes'] >= 100_000_000_000
print(v['actual_offline_payload_bytes'], v['unique_rows'], len(q['checks']))
PY
```

Lệnh này kiểm report đã lưu và ràng buộc marker, không quét lại payload. Muốn đọc kiểm lại dữ liệu hiện tại, dùng `python -m pipelines.chunk_source --source data/source/scale-100gb-26 --report outputs/new-source-receipt.json` với tên report mới và đủ thời gian/tài nguyên; lệnh stdlib này có thể chạy lâu hơn codec của protocol. Không chạy lại chỉ để xem số liệu đã có trong report.

## Tự trình bày mốc này

- **Chunk và RAM:** Mỗi file giữ tối đa số dòng cấu hình; iterator giữ trạng thái máy qua file, thay vì nạp cả history vào RAM.
- **Bằng chứng dữ liệu đúng:** Ngoài byte/SHA, validator đọc lại đầy đủ grid máy/thời gian, schema, duplicate/late và trạng thái motor theo sự cố/bảo trì. Test nhỏ có corruption để kiểm validator thực sự phát hiện lỗi.
- **Vai trò tập trung gian:** Nó cho số đo RAM/scratch/throughput thật để quyết định có đủ đĩa và thời gian chạy lớn; forecast luôn tách khỏi kết quả 100 GB đo được.
- **Phạm vi 100 GB:** Nguồn offline đạt dung lượng và quality. Muốn chứng minh Spark/feature/ML ở quy mô đó phải chạy và thu bằng chứng riêng cho các bước ấy.

## Phạm vi còn lại

Nguồn 100 GB không tự chứng minh Gold/feature/model hoặc Airflow xử lý 100 GB. Cần benchmark Spark cùng workload baseline/optimized, kiểm đỉnh shuffle, warehouse/indexing, feature join lớn, split/label có positive rồi Feast/KFP. Model cũ có tập test 0 positive vẫn chưa được sửa trong mốc sinh nguồn.

Đặc biệt, [DP3](../pipelines/features.py) hiện ghép snapshots với telemetry/failure/maintenance bằng `machine_id` kèm điều kiện thời gian; [job điều phối](../pipelines/orchestrated_job.py) còn cache readings/snapshots. Chưa đo các phép ghép/cache này trên horizon dài. Mốc tiếp theo phải xem physical plan, kiểm tập trung gian biết đáp án, giảm candidate join/kiểm arrival-time và đo RAM/shuffle trước khi đưa toàn bộ nguồn mới vào DP3; không dùng số đo Bronze/Silver làm bằng chứng cho feature pipeline.

Ngân sách thời gian Spark lớn phải tính cả launcher/adapter rehash nguồn, full readback và query/metrics, ngoài `job_wall_seconds`. Các bước này có thể đọc lại cùng file nhiều lần; tổng byte I/O không phải dung lượng dataset. Cần đo lại và lập gate theo phiên VM hiện tại trước khi chạy, giữ giới hạn STOP 8 giờ đã duyệt.

## Gate cho Spark toàn nguồn lớn

**Cập nhật sau pilot mới:** [Runner HDD V4](large-ingest-runner.md) đã kiểm128partitions/13Sparkchecks/130unitVM, nguồnread-only và pre/postfullchecksums. Budget cho job này chốt **355GBHDD=output80+scratch240+log5+free30**,SSD35GB,RAM36GiB; nguồn lớn gợi ý2048partitions, có screeningper-taskmemory. [Admission mới](evidence/large-ingest-runner/admission/plan.json) dự tính7giờ35phút, chặn phiên hiện tại; một phiên8giờ mới chỉ là đề xuất chưa được cấp quyền. Plan02:15 bên dưới giữ làm lịch sử, không phải cấu hình launcher mới.

Lúc **02:15:26 UTC 27/10/2026**, đã đo trực tiếp trên VM và lưu [plan đầy đủ](evidence/large-ingest-plan/plan.json). Nguồn lớn vẫn giữ nguyên; script chỉ đọc 64 MiB đầu của tám part v1 có sẵn bằng GNU `dd iflag=direct,fullblock`, tổng **536.870.912 B trong 4,114749 giây**, khoảng **130,47 MB/s**. O_DIRECT tránh đo nhầm tốc độ page cache; không xóa cache toàn máy, không ghi source. Đây là mẫu ngắn có thể hưởng burst của đĩa, không cam kết băng thông shuffle hỗn hợp hoặc tốc độ chạy nhiều giờ. Kiểm inode/size/mtime và manifest trước/sau, không gọi mẫu đọc này là full validation mới.

| Tài nguyên | Khả dụng thực đo | Ngân sách tăng thêm và dự phòng | Gate |
|---|---:|---:|---|
| HDD | 410.628.182.016 B | 305 GB: output150 + scratch120 + log5 + trống30 | Qua |
| SSD | 133.819.969.536 B | 35 GB: platform15 + trống20 | Qua |
| RAM | 60.643.393.536 B | 36 GiB: container28 + headroom8 | Qua |
| CPU | 8 vCPU | Ít nhất7, trong đó Spark6 | Qua |
| Thời gian trước STOP | 12.822,949 giây | Ước tính13.570,344 + dự phòng kết thúc1.200 giây | **Chưa qua** |

Source đã chiếm đĩa nên không cộng lại 115 GB source cap vào ngân sách tăng thêm. Scratch chuyển sang HDD; tăng dự trù từ100 lên120GB để có chỗ cho ngoại suy scratch trung gian khoảng85GB cùng margin. Đây vẫn là ngân sách, chưa phải quan sát scratch100GB hoặc đảm bảo RAM khi scale.

[Planner](../pipelines/ingest_budget.py) dùng source/receipt lớn đã PASS, intermediate/report cùng manifest, task metrics trung gian và probe mới. Workload được dự tính là Bronze/Silver, full checks và ba cặp query JSONL/Parquet hiện có. Task I/O ngoại suy cộng **ba lượt checksum** của launcher hiện tại cho khoảng **1.362 GB I/O**. Đây là byte đọc/ghi lặp, không phải dung lượng dataset. Thời gian lấy giá trị lớn hơn giữa ngoại suy wall-time Spark và I/O/băng thông mẫu, nhân1,3 rồi dành thêm20phút kết thúc. Toàn khoảng4giờ6phút vượt3giờ33phút còn lại lúc đo. Không bỏ gate hoặc thay auto-STOP để chạy.

Plan luôn ghi `execution_ready=false`: launcher optimized cũ còn chọn SSD, chưa có live guards đầy đủ cho lượt lớn và cần kiểm partition lớn. Vì vậy ngay cả khi các resource/time gates cùng qua, không dùng file plan như một lệnh tự cho phép chạy Spark. Chưa mở lại/kéo dài phiên VM hoặc tạo/nâng tài nguyên cloud trong mốc này.

Trên VM, chọn report mới và deadline STOP thực của phiên hiện tại:

```bash
data/runtime/scale-venv/bin/python scripts/plan-large-ingest.py \
  --deadline-utc "$VM_STOP_UTC" --output outputs/my-large-ingest-plan.json
```

Script không sinh lại source hoặc khởi chạy Spark. Bốn test planner kiểm biên từng volume/RAM/CPU, receipt khác nguồn, số đo lỗi/không hữu hạn, và thiếu thời gian dù riêng Spark wall-time có vẻ vừa. Bộ unittest sau thay đổi: **111 tests PASS** trên PC và VM; [bằng chứng kiểm code](evidence/large-ingest-plan/checks.json).

[Benchmark skew](skew-benchmark.md) đã kiểm ba cặp cùng query trên17,28triệu readings, có physical plans và Spark UI thật. Mốc kế cần hoàn thiện runner HDD/live guards, giảm đọc kiểm dư thừa mà vẫn giữ integrity, kiểm partition/feature join bằng tập biết đáp án rồi mới admission lại theo phiên VM thực. Không sinh lại nguồn102,45GB đã đạt.
