# Runner Spark cho nguồn lớn

Runner này dùng nguồn đã sinh và có semantic receipt; không sinh lại dữ liệu. Nó đưa JSONL vào Bronze/Silver Snappy Parquet, kiểm exact multiset, schema evolution, duplicate/latest export, khóa máy và ba cặp query JSONL/Parquet. Gold, feature và model là các bước khác cần kiểm chứng riêng.

## Vì sao cần runner riêng?

Launcher cloud trước đây quét checksum nguồn ở host rồi adapter quét trước và sau Spark: tổng ba lượt đọc toàn bộ source. Bản mới chỉ kiểm manifest, catalog, receipt, inventory và kích thước file ở host. **Hai lần checksum đầy đủ vẫn chạy trong adapter Spark**, nên dữ liệu bị sửa cùng kích thước không được lọt qua. Test nhỏ cố tình làm đúng lỗi này để chứng minh sự khác biệt giữa admission nhẹ và kiểm integrity.

Source được gắn **read-only** riêng trong container. Output và scratch có tên run mới; không overwrite, không append, không tự resume một lượt dở dang. Container thật được inspect để đối chiếu image ID, 6 CPU, 28 GiB RAM, nguồn read-only và scratch đúng HDD. Chỉ có file Compose chưa đủ chứng minh các giới hạn này đang có hiệu lực.

## Đĩa, bộ nhớ và partition

- Scratch ở `/srv/iot-data/spark-scratch/<run-name>`, trên HDD500GiB; không dùng SSD150GiB cho shuffle lớn. Source hiện có không bị cộng lại như dung lượng cần cấp mới. Cap output80GB dành cho job Bronze/Silver này; Gold/feature/warehouse cần admission riêng khi triển khai.
- Admission yêu cầu HDD trống355GB cho output80/scratch240/log5/free30; SSD trống35GB cho platform và dự phòng; RAM khả dụng36GiB cho container28GiB cộng8GiB headroom; ít nhất7CPU để Spark dùng6.
- Partition có biên1–4096. Gợi ý theo raw bytes, nhắm không quá64MiB/partition và làm tròn lũy thừa hai: nguồn5,59GB gợi ý128; nguồn102,45GB gợi ý2048. Đây là cách phân chia công việc, không phải dự báo chính xác heap/RAM. Lượt trung gian mới cố ý dùng128 để kiểm đường chạy vượt cap64 cũ.
- Tham số bỏ trống dùng gợi ý; giá trị0 là lỗi. Nguồn100GB phải chọn ít nhất số partition đã gợi ý. Không tự giảm partition để qua guard. Runner tắt riêng AQE coalescing để giữ việc chia nhỏ đã kiểm; những tối ưu AQE khác giữ theo Spark. Fixture biết đáp án dùng8partitions rồi trả lại cấu hình workload thật.

[Cấu hình Spark 3.5.9](https://spark.apache.org/docs/3.5.9/configuration.html) phân biệt giới hạn heap, thread và thư mục local. [Tuning SQL](https://spark.apache.org/docs/3.5.9/sql-performance-tuning.html) giải thích file partitions/shuffle partitions/AQE. Container memory còn chứa non-heap, Python và cache; không coi driver heap20GiB là toàn bộ memory footprint.

## Guard khi đang chạy

Controller quan sát mỗi2giây: RAM khả dụng, free bytes của cả hai volume, logical bytes của output/scratch/log và thời gian còn lại trước STOP. Một đỉnh ngắn hơn chu kỳ có thể bị bỏ sót; free-space gate dùng filesystem thực để bổ sung cho số đo logical file bytes.

Pilot có cap90phút, output/scratch mỗi loại20GB và luôn giữ HDD trống355GB cho bước lớn sau đó. Lượt lớn có cap từ forecast đã qua admission, output80GB/scratch240GB và giữ HDD trống30GB. Cả hai giữ SSD35GB, RAM trống4GiB, log không quá5GB; guard dừng khi còn10phút trước STOP. Admission luôn cần thêm20phút ngoài forecast để kết thúc/thu báo cáo.

Khi chạm guard hoặc nhận signal, controller chỉ stop container `iot-large-<run-name>` của mình và tiến trình CLI liên quan. Các service Airflow/DataHub không bị runner dừng. Advisory lock chỉ tuần tự hóa những lượt dùng launcher này; trước khi chạy vẫn phải kiểm không có job nặng từ launcher/DAG khác.

`ingest-progress.json` ghi phase thật: kiểm source trước xử lý, fixture biết đáp án, ghi/kiểm Bronze, metadata, ghi/kiểm Silver, query, kiểm source sau xử lý, completed. `wrapper-progress.json` ghi command hiện tại và elapsed. Progress không thay success marker; một phase cũ còn trong file không chứng minh process vẫn sống. Đối chiếu unit/PID/container và marker khi tiếp tục sau khi mất kết nối.

## Cách chạy trên VM hiện có

Từ repo root, dùng image/runtime đã triển khai, chọn run name mới. Đặt `VM_STOP_UTC` từ scheduling thực của phiên VM và `RELEASE_COMMIT` từ release đang dùng; không sao chép deadline lịch sử sang phiên mới.

```bash
data/runtime/scale-venv/bin/python scripts/run-large-ingest.py \
  --run-name my-hdd-pilot --source data/source/scale-intermediate-26 \
  --receipt outputs/scale-intermediate-26/receipt.json --partitions 128 \
  --deadline-utc "$VM_STOP_UTC" --base-commit "$RELEASE_COMMIT"
```

Chạy trong cơ chế nền có log/unit đã chuẩn bị khi làm qua SSH. Đây là lệnh dài, không phải lệnh chỉ xem số đo. Nếu bị chặn admission, `preflight.json` và `rejected.json` giữ nguyên nguyên nhân, không tạo lakehouse/scratch hoặc container Spark.

Sau pilot PASS, mode lớn bắt buộc kiểm marker, report hashes, source identity, image, exact runtime code hashes và đủ ba cặp query của pilot trước khi ngoại suy. Forecast giữ hai lượt checksum, task I/O gồm spill, thời gian command và margin. Nó còn kiểm dự báo scratch/output với cap đang enforce. Scratch lấy mức lớn nhất giữa peak đã đo, tổng shuffle đã ghi và floor1,5×raw source, rồi cộng30%; không chọn một lượt có peak thấp thuận lợi. RAM screening dùng peak execution memory của task, tỷ lệ raw bytes/partition,6task đồng thời và margin1,3 so với ngân sách10GiB bên trong heap20GiB. Đây là screening dựa trên pilot, không bảo đảm RAM tuyến tính hoặc không OOM; cgroup28GiB và live guard vẫn có hiệu lực. Băng thông mẫu O_DIRECT ngắn bị giảm một nửa và cap64MB/s cho mô hình mixed I/O; đây là giả định thận trọng, không phải băng thông đảm bảo. Forecast không tự cho phép mở lại/kéo dài phiên VM.

Lệnh lớn có thêm `--pilot-run my-hdd-pilot`, nguồn `data/source/scale-100gb-26`, receipt tương ứng và `--disk-probe` trỏ report probe đã kiểm. Chỉ chạy sau khi toàn bộ admission gates qua trong phiên VM thực được phép sử dụng. Repo clone không chứa source, image store, volumes hoặc credentials.

## Bằng chứng cần kiểm sau mỗi lượt

Success phải có cả marker lakehouse ràng buộc SHA `ingest-report.json` và marker wrapper ràng buộc SHA `verification.json`. Report phải khớp source bytes/manifest/unique rows; event log phải có `SparkListenerApplicationEnd`, task work thực và0failed attempts. Source metadata và mọi execution file hash không được đổi trong lúc chạy. `verification.json` lưu thời gian command/wrapper, image ID và runtime hashes để truy lại đúng code thực thi. Resource samples được đóng trước verification và ràng buộc SHA; sửa peak nhỏ đi sau đó sẽ làm forecast bị từ chối.

## Kết quả đã chạy thật: large-ingest-medium-27-v4

Lượt cuối hoàn tất **03:26:00 UTC27/10/2026** trên VM8vCPU/64GiB, CPU Intel Broadwell. Spark3.5.9/Python3.10.12, controllerPython3.12.3. Nguồn giữ nguyên **5.587.984.353B**,200máy×1440h:17.625.600Bronze rows →17.280.000Silver unique, loại345.600extras. **13Sparkchecks** gồm5fixture biết đáp án và8kiểm toàn dữ liệu; **130unittest** trênVM qua2,929giây. Tất cả source bytes được checksum lại trước/sau xử lý.

[Report](evidence/large-ingest-runner/ingest-report.json), [verification](evidence/large-ingest-runner/verification.json), [marker wrapper](evidence/large-ingest-runner/_RUN_SUCCESS.json) và [marker lakehouse](evidence/large-ingest-runner/lakehouse-_RUN_SUCCESS.json) đã đối chiếu SHA. [Container inspect](evidence/large-ingest-runner/container-limits.json) xác nhận6CPU/28GiB/HDDscratch/nguồnread-only; [preflight](evidence/large-ingest-runner/preflight.json) ràng buộc code/image/source. Runtime là snapshot mở rộng commitf01513c, exact hashes đi kèm; không gán finalcommit có sau cho thời điểm thực thi.

| Số đo | Kết quả thực |
|---|---:|
| Command Spark, gồm prehash/startup/posthash/shutdown | 569,484489giây |
| Toàn wrapper, gồm unit/config/metrics/report | 577,556895giây |
| Output partitions / shuffle partitions | 128 /128; AQEcoalescingfalse |
| Peak cgroupmemory trước shutdown | 17.978.679.296B |
| Peak scratch qua283mẫu/2giây | 6.299.949.224B |
| Peak output logical file bytes | 1.489.031.464B |
| RAM khả dụng thấp nhất qua sampling | 50.323.574.784B |
| HDD trống thấp nhất qua sampling | 399.800.516.608B |
| Task attempts / failed / diskspill | 2965 /0 /0B |
| Cumulative shuffle write | 9.516.589.265B |
| Maximum task peak executionmemory | 671.088.496B |

Event log có application-end thật. Tổng5datasets Parquet là1.477.481.665B/259files, Snappy kiểm từfooter; `_checks` và metadata làm logical output lớn hơn. Query JSONL/Parquet chạy3lần mỗi loại cho cùng đáp án, median14,460815/0,732496giây. Không gọi so sánhV3/V4 là benchmark tối ưu độc lập: fixture partitioning/AQE/filecache khác, và chỉ một fullrun/version.

LượtV3 trước đó cũng PASS nhưng peak scratch8.290.946.837B/nguồn5,59GB, lớn hơn V4. [CalibrationV3](evidence/large-ingest-runner/calibration-v3/resource-samples.json) và các snapshot code cũ được giữ để giải thích floor1,5×source và việc tăng ngân sách. V3 không đủ điều kiện làm same-code pilot choV4. V4 ký SHA resource samples trước marker; forecast còn dùng **tổng shufflewrite**, lớn hơn peak sampling, để dự phòng lifecycle/đỉnh ngắn.

## Admission toàn nguồn102,45GB

[PlanV4](evidence/large-ingest-runner/admission/plan.json) dựa đúng code/image/pilotV4 và disk probe đã kiểm. Projected output35.489.091.775B≤cap80GB; projected scratch226.815.294.354B≤cap240GB. Với2048partitions, projected executionmemory của6task cộngmargin≈5.998GB≤screeningbudget10GiB. Các phép dự tính này không phải số đo full100GB.

Ước tính26127,706giây cộng1200giây kết thúc, khoảng **7giờ35phút**, sau khi đã tính hai lượt checksum, cumulative task I/O, giả định mixed-IO64MB/s và margin1,3. Phiên hiện tại không đủ thời gian; **launcher thật đã reject**, không tạo dataset/scratch/container lớn. [Rejected](evidence/large-ingest-runner/admission/rejected.json) có `spark_launched=false`.

Tính thử một phiên8giờ mới, trừ10phút boot/recovery, thì cả5gates qua với tài nguyên hiện có. Đây là **đề xuất cần quyền**, chưa mở lại VM. Dự toán8giờVM và24giờgiữcácđĩahiệncó là [5,522220USD gồm30%dựphòng](evidence/large-ingest-runner/admission/next-session-estimate.json), chưa gồm mạng/thuế; không phải actualbill hoặc spendcap. Đĩa500GiBstandard +200GiBbalanced khoảng1,315USD/ngày dù VMSTOP. Giá tham chiếu: [E2highmem8 tạiIowa](https://cloud.google.com/products/compute/pricing/general-purpose?hl=en), [PersistentDisk](https://cloud.google.com/compute/disks-image-pricing), [IPv4](https://cloud.google.com/vpc/pricing).

## Kiểm bản đã push

[Release receipt](evidence/large-ingest-runner/release.json): commit `e944b29f847359ebbea1891bd034cc4ee2a21664` đã pushprivate `main`; clone mới từGitHub trên cùngPC qua130unit/0skip/11,758giây và kiểmSHAexecution/reports/resources/markers/protocol,93tiêu chí gốc và relative links. VM nhận463file qua verifiedGitarchive lúc03:52:53UTC27/10; nguồn lớn giữ nguyên,0DAGrunning/queued/0importerrors,9platformimages vẫn chạy/GMS200. Không restart service hoặc kéo dài phiênVM. Dữ liệu/runtime/secrets/setup/handoff riêng không có trongarchive; đây chưa phải chạy lại Docker/full100GB từclone hoặc kiểm trên laptop.

Giữ nguyên [mốc nguồn102,45GB](scale-100gb.md) và [benchmark skew](skew-benchmark.md) đã có. Chưa Spark/Gold/feature/ML toàn100GB, chưa tích hợp launcher mới vàoAirflow, và chưa có resume. Bước tiếp theo cần phiên VM thực được phép, kiểm lại scheduling/resources/code hashes rồi mới admission và launch mới; không dùng hypothetical clock để chạy.
