# Feature và nhãn trên lịch sử 60 ngày

Mốc này chuẩn bị dữ liệu cho ML từ Silver đã kiểm chứng, theo [protocol được chốt trước huấn luyện](ml-evaluation-protocol.md). Nguồn vẫn là200máy ×60ngày,5.587.984.353byte offline; không sinh lại, không chuyển mốc nguồn102,45GB thành bằng chứng feature100GB.

[Job Spark](../pipelines/feature_dataset.py) tạo snapshot mỗi5phút, tính tám feature bằng [join theo bucket](bounded-features.md), ghi nhãn riêng và loại các dòng không đủ điều kiện. [Bộ nạp protocol](../ml/protocol.py) kiểm SHA của bản đã commit `b0ce019`; thay cut time hoặc threshold cũng bị từ chối dù JSON vẫn hợp lệ. `created` ghi thời gian thực thi thật, còn event/arrival time là đồng hồ mô phỏng; không so sánh hai đồng hồ như cùng một thời gian vận hành.

## Kết quả chạy thật

Run `feature-medium-27-v2` hoàn tất **04:23:51UTC27/10/2026** trên VM hiện có8CPU/64GiB, không mở phiên mới. Image Spark3.5.9/Python3.10.12; container thực giới hạn6CPU/28GiB, heap20GiB,128shufflepartitions, AQEcoalescingfalse, UTC và scratchHDD. Code được chụp riêng và gắn chỉ đọc trước khi chạy; inputParquet cũng chỉ đọc. [Preflight](evidence/feature-medium-60d/preflight.json) và [Docker inspect](evidence/feature-medium-60d/container-limits.json) lưu cấu hình thực.

**3.456.000snapshots** tạo đủ feature, nhãn và audit. Có **3.132.184dòng đủ điều kiện** sau censoring, feature-quality và purge; bảng entities giữ nhãn/split/episode, chưa tự ghép chúng thành tập huấn luyện bỏ qua Feast.

| Tập cố định | Dòng đủ điều kiện | Positive | Negative | Sự cố độc lập có positive snapshot |
|---|---:|---:|---:|---:|
| Train:01/10–12/11 | 2.195.668 | 93.603 | 2.102.065 | 7.870 |
| Validation:12/11–21/11 | 468.132 | 19.828 | 448.304 | 1.677 |
| Test:21/11–30/11 | 468.384 | 19.957 | 448.427 | 1.687 |

Validation/test đều qua gate20positive,20negative và5episode. Nhiều snapshot trước cùng một sự cố chỉ được tính là một episode qua `failure_id`. Test cũ của lịch sử48giờ vẫn giữ nguyên kết quả0positive. Lịch sử60ngày và các cut time đã được chốt trước lần chuẩn bị này; chưa có mô hình hoặc metric chất lượng mới để chọn lại split.

Các lý do loại dòng được đếm theo thứ tự ưu tiên, không chồng đếm:

| Lý do | Số dòng |
|---|---:|
| Máy đang dừng | 158.496 |
| Horizon chưa quan sát đủ | 2.333 |
| Có bảo trì bắt đầu trong horizon | 158.257 |
| Không có reading đã tới trong cửa sổ | 148 |
| Nhãn chưa có trước ranh giới tập kế tiếp | 4.582 |

Không phát sinh dòng bị loại riêng vì feature không hữu hạn. Các dòng đã bị loại do dừng máy vẫn có thể đồng thời thiếu horizon/readings; bảng trên là phân loại loại trừ nhau. Tổng loại323.816 cộng3.132.184included bằng toàn bộ3.456.000snapshots. Feature chỉ lấy event trong `(t−10phút,t]` đã nhận trước/tại`t`, lịch sử sự cố24giờ đã ghi nhận trước/tại`t`; truth trạng thái máy và sự cố tương lai chỉ nằm trong bảng nhãn.

## Kiểm chứng và tài nguyên

- **136unit test** qua trênPC10,774giây vàVM2,922giây; sáu test mới bảo vệ protocol/class/episode gates. [LogVM](evidence/feature-medium-60d/feature-dataset-vm-unit-tests.txt).
- Ba known-answer checks cho exclusion/purge chạy trước đọc dữ liệu lớn; tám kiểm tra bounded/range đã có [bằng chứng riêng](bounded-features.md). Không lấy unitcount thay cho APIcoverage/mutation.
- Feature và label được so hai chiều bằng multiset với Parquet đọc lại; kiểm grid từng máy, grain, availability, horizon, episode và label purge. **260inputParquet files** được hash trước/sau và không đổi; [binding](evidence/feature-medium-60d/input-binding.json).
- Bốn dataset,320Parquetfiles/Snappy thật quafooter, tổng **277.455.368byte**: feature186.720.189B, label53.178.056B, audit14.824.771B và eligibleentities22.732.352B.
- Build phase195,724giây; từ trước Docker-start đến hết post-check214,975giây. Các số này không gồm unit test, admission, snapshot và prehash đầu vào trước đó; không phải benchmark baseline/optimized.
-100mẫu/2giây: peak scratch2.505.181.242B, peak logical output279.626.431B, RAMhost khả dụng thấp nhất30.736.920.576B, HDD trống thấp nhất403.390.631.936B. Giá trị lớn nhất đọc từ cgroup`memory.peak` trước shutdown là8.721.842.176B; sampling có thể bỏ lỡ đỉnh cuối. Không đồng nhất RAMcontainer với RAMhost khả dụng.
- [Event metrics](evidence/feature-medium-60d/spark-metrics.json):4.120taskattempts,0failed,0disk/memoryspill, có application-end thật. Shuffle tích lũy3.972.639.247B mỗi chiều; không phải peak scratch.

[Report](evidence/feature-medium-60d/feature-report.json), [receipt](evidence/feature-medium-60d/verification.json), [wrapper marker](evidence/feature-medium-60d/_RUN_SUCCESS.json) và [data marker](evidence/feature-medium-60d/features-_FEATURES_SUCCESS.json) được nối bằng SHA. Runtime base là commit `5a2d06e` cộng bốn file mới có SHA trong [code packet](evidence/feature-medium-60d/feature-dataset-code-v3-deployed.json); mọi Python trong snapshot cũng có SHA trong preflight/report. Lượt đầu `feature-medium-27` dừng ở Docker trướcSpark do thiếu nestedmountpoints trong codeRO; đã sửa launcher, giữ log/partial cũ và chạy tên mới. Không sửa đáp án hoặc protocol để có kết quả đẹp.

## Tự chạy và giới hạn

[Launcher](../scripts/run-feature-dataset.py) cần LinuxVM có đúng source/Silver/receipts từ mốc trước và image hiện có. Gitclone không tự mang dữ liệu/runtime này. Launcher khóa cùng mutex với largeingest, dùng output mới, chụp code, kiểm nguồn/fixture/protocol và Dockerlimits; không có resume cho output dở.

Ngân sách riêng cho bước medium này: HDD56GB=output5+scratch20+log1+free30,SSD35GB,RAMavailable36GiB/CPU≥7. Giới hạn job45phút cộng10phút hoàn tất phải vừa thời gian phiên còn lại. Trong lúc chạy, giữ ít nhất30GBHDD/35GBSSD/4GiBRAMhost khả dụng; chạm cap thì chỉ dừng container của lượt này. Đây không phải ngân sách355GB của largeingest.

Sau khi đọc deadlineSTOP thực tế của phiênVM được duyệt, đặt biến môi trường `ACTUAL_STOP_DEADLINE_UTC` bằng timestamp đó và chọn tên mới:

```bash
data/runtime/scale-venv/bin/python scripts/run-feature-dataset.py \
  --run-name feature-medium-check-01 --deadline-utc "$ACTUAL_STOP_DEADLINE_UTC"
```

Dữ liệu ở `data/feature-datasets/<run>/artifacts`; reports/log/snapshot ở `outputs/<run>`, scratch trên `/srv/iot-data/spark-scratch/<run>`. Không đưa chúng lênGit. Metadata nhỏ dưới `docs/evidence` đủ để đối chiếu, không thay thế dữ liệu gốc.

Tiếp theo cần đăng ký feature trong Feast, kiểm PIT/online/materialize/push rồi để KFP lấy feature qua store, chia tập theo protocol và huấn luyện/đánh giá. Mốc này chưa chứng minh Feast, KFP, MLflow, KServe, AirflowDP3 trên60ngày hoặc full100GB. Không có PR-AUC/F1 mới từ việc chỉ đếm class support.

**Feast27/10:** [Mốc feature store](feast-feature-store.md) đã compact lossless3.456.000snapshots và dùng native SDK lấy toàn3.132.184eligible rows theo protocol cố định. Exact8feature/nhãn/split hai chiều, sealed retry và corruption-copy rejection đã qua. Không huấn luyện mới hoặc KFP từ mốc này.
