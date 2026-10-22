# Đo nguồn trung gian trước khi tăng quy mô

Ngày 22/10/2026 trên THIENAN, script [measure-generator.ps1](../scripts/measure-generator.ps1) đã sinh và đọc kiểm lại **119.233.630 byte offline** không nén, JSONL. Đây là bước kiểm tra tài nguyên trung gian, chưa đạt 100 GB.

```powershell
./scripts/measure-generator.ps1 -RunName throughput-practice
Get-Content outputs/throughput-practice/generator-throughput.json
```

Script giữ guard 100.000 unique của generator hiện tại: chạy tuần tự bốn mô phỏng độc lập, mỗi phần 32 máy × 48 giờ × một phút = 92.160 unique. Seed lần lượt 42–45; không ghép thành một lịch sử liên tục vì machine_id và khoảng thời gian trùng giữa các mô phỏng. Không dùng bốn phần này thay tập train/test theo thời gian của batch. Mỗi lần dùng tên mới, nguồn/báo cáo cũ được giữ.

| Phép đo `throughput2-tree` | Kết quả |
|---|---:|
| Tổng unique readings của bốn mô phỏng | 368.640 |
| Offline metadata + telemetry, không gồm stream/manifest | 119.233.630 byte (113,71 MiB) |
| Tất cả source, gồm stream fixture và manifest | 278.919.030 byte |
| Tổng thời gian generate bốn process | 19,203 giây |
| Offline bytes / tổng thời gian generate | 5,922 MiB/s |
| Tổng thời gian inspect/readback | 23,805 giây |
| Kiểm tra chất lượng | 35/35 cho từng phần |
| Working set cao nhất quan sát lúc generate | 264.777.728 byte (~252,51 MiB) |
| Working set cao nhất quan sát lúc inspect | 474.206.208 byte (~452,24 MiB) |

[Report đo thật](evidence/day2-throughput/generator-throughput.json) có cấu hình, lệnh/exit status, thời gian từng phần, máy/base commit, hashes generator và script; [quality 1](evidence/day2-throughput/shard-01-quality.json), [2](evidence/day2-throughput/shard-02-quality.json), [3](evidence/day2-throughput/shard-03-quality.json), [4](evidence/day2-throughput/shard-04-quality.json) có số dòng, checksum và kết quả đọc lại. Nguồn ở `data/source/throughput2-tree`, ngoài Git. Host dùng CPython 3.13.14, Ryzen 5 5500, RAM 15,87 GiB; trước phép đo trống 5.315.424.256 byte RAM và 401.891.651.584 byte đĩa.

Thời gian generate gồm cả tạo stream fixture và ghi manifest, trong khi tử số throughput chỉ tính byte offline. Không kiểm soát filesystem cache/ứng dụng nền; một phần lượt đo chồng với kiểm tra nạp lại PostgreSQL. Đây là một lượt cho mỗi seed, không phải benchmark tốc độ ổn định hoặc so sánh tối ưu. Không ngoại suy thành tuyên bố đã sinh đủ 100 GB.

Phép đo RAM cộng working set của tiến trình `.venv` và các tiến trình con được phát hiện; chờ 50 ms giữa mẫu, tìm PID con mỗi ít nhất 250 ms. Số liệu là đỉnh **quan sát được**, có thể bỏ lỡ spike và có thể đếm trùng trang nhớ dùng chung. Bản đo thử trước chỉ nhìn launcher (~11 MiB) đã bị loại khỏi bằng chứng chính vì không bao gồm Python thực thi. Kết quả ở bảng trên dùng phép đo cây tiến trình đã sửa.

Trước khi sinh lớn, cần generator và validator đọc/ghi theo chunk/Parquet, có kiểm tính tái lập và quy tắc anomaly toàn cục; đo cả footprint nguồn, CSV staging/PostgreSQL, các lớp output, shuffle/event logs và dư địa đĩa. Loader hiện kiểm cả tập nhỏ trong RAM, không phải đường ingest cho 100 GB. Thiết kế đích giữ metadata ở PostgreSQL và telemetry lớn ở prefix/object storage riêng, rồi Spark đọc theo partition. Không nhân số process hiện tại để vượt guard rồi gọi là generator cho 100 GB. Giữ các nguồn thử cục bộ cho đến khi quyết định rõ việc dọn dữ liệu.
