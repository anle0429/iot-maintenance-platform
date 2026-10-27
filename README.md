# IoT Maintenance Platform

Coursework EDAI K11: giám sát máy bơm công nghiệp bằng dữ liệu cảm biến mô phỏng, dự đoán sự cố trong một giờ tới và trợ lý tra cứu tình trạng máy/tài liệu.

**Trạng thái:** đã sinh và đọc kiểm đầy đủ **102.447.847.856 byte offline**, 316.800.000 unique readings trên VM. [Mốc100 GB](docs/scale-100gb.md) có14 kiểm tra nguồn; [runner HDD mới](docs/large-ingest-runner.md) kiểm lại5,59GB/128partitions/13Sparkchecks/130unittestVM. [Airflow DP1–DP3/DataHub](docs/orchestration.md) đã kiểm pilot riêng:64 rules/14 bảng, chặn batch lỗi và phục hồi. Chưa Spark/Gold/feature toàn100 GB hoặc API; [protocol ML mới](docs/ml-evaluation-protocol.md) đã chốt trước huấn luyện, kết quả test cũ0positive giữ nguyên.

Đã [triển khai VM Google Cloud](docs/gcp-runtime.md) 8vCPU/64GiB với Ansible, IAP và đĩa HDD/SSD tách riêng. Cùng workload1,12GB trên một VM: baseline223,942s→optimized100,892s, disk spill967MB→0, cả hai13checks/84tests qua. Một lượt mỗi cấu hình, chưa benchmark100GB hoặc bằng chứng hoàn thành toàn coursework.

[Benchmark skew mới](docs/skew-benchmark.md) trên 17,28 triệu dòng Silver: ba cặp query cho kết quả giống hệt, trung vị15,339→8,577giây khi broadcast bảng chính sách nhà máy; có ảnh Spark UI thật. 111 unittest qua trên PC và VM sau khi thêm planner tài nguyên. [Gate Spark lớn](docs/scale-100gb.md#gate-cho-spark-toàn-nguồn-lớn): đủ đĩa/RAM nhưng chưa đủ thời gian còn lại của phiên; chưa khởi chạy toàn nguồn lớn.

[Feature60ngày](docs/medium-feature-dataset.md) đã chạy trênVM:3.456.000snapshots,3.132.184dòng đủ điều kiện theo protocol cố định; validation/test đều có đủ classes và sự cố độc lập.136unit test và ba kiểm tra cohortSpark qua, input/output/marker được đối chiếu. Chưa huấn luyện mô hình mới hoặc triển khai Feast/KFP.

## Mục lục

- [Phạm vi và dữ liệu](docs/domain-proposal.md)
- [Hợp đồng dữ liệu](docs/data-contract.md)
- [Kiến trúc và trạng thái deployment](docs/architecture.md)
- [Kết quả và giải thích ngày 1](docs/day1-generator.md)
- [PostgreSQL nguồn, Spark và baseline](docs/day2-batch.md)
- [Kafka/Flink: event time, late arrival và replay](docs/day3-streaming.md)
- [Đo nguồn trung gian và RAM](docs/generator-throughput.md)
- [Generator theo chunk và ngân sách 100 GB](docs/chunked-generator.md)
- [Catalog → Spark → Parquet: kiểm đúng, dung lượng và query](docs/chunk-ingest.md)
- [Google Cloud VM: Terraform plan, quota và dự toán](docs/gcp-vm.md)
- [VM đã chạy: Ansible, cùng workload nhanh 2,22 lần và cách tự chạy](docs/gcp-runtime.md)
- [Airflow DP1–DP3, validation chặn lỗi và DataHub lineage/assertions](docs/orchestration.md)
- [Mốc nguồn 100 GB: ngân sách HDD/SSD/RAM, tập trung gian và cách tự chạy](docs/scale-100gb.md)
- [Spark skew: cùng query, ba cặp đo, kết quả đúng và ảnh UI](docs/skew-benchmark.md)
- [Runner nguồn lớn: HDD scratch, hai lượt checksum và live guards](docs/large-ingest-runner.md)
- [Protocol ML60ngày: chia theo thời gian, purge và holdout trước metric](docs/ml-evaluation-protocol.md)
- [Feature theo bucket thời gian: kiểm đúng ranh giới và dữ liệu đến muộn](docs/bounded-features.md)
- [Feature60ngày: nhãn, purge, loại dòng và class/episode support](docs/medium-feature-dataset.md)
- [Cách chạy](#cách-chạy)
- [Checklist rubric](docs/rubric-tracker.md)
- [Cấu trúc hiện tại](#cấu-trúc-hiện-tại)

## Cấu trúc hiện tại

```text
README.md                    Giới thiệu và đường dẫn tài liệu
docs/
  domain-proposal.md         Nghiệp vụ, dữ liệu, nhãn, feature, agent
  data-contract.md           Grain, schema, units, thời gian và chính sách dữ liệu
  architecture.md            Kiến trúc dự kiến và phạm vi đã triển khai
  day1-generator.md          Kết quả đo và giới hạn kiểm chứng
  day2-batch.md              PostgreSQL/Spark, feature, nhãn và baseline đã chạy
  day3-streaming.md          Kafka/Flink, watermark, dedup và kiểm replay
  chunked-generator.md       Sinh/đọc kiểm theo chunk, đo RAM và dự trù dung lượng
  chunk-ingest.md            Spark đọc catalog, Parquet Bronze/Silver và số đo thực
  orchestration.md           Ba DAG trên VM, validation/lineage, lỗi và phục hồi đã chạy
  scale-100gb.md             Nguồn lớn liên tục, gate tài nguyên, số đo và giới hạn xử lý
  rubric-tracker.md          Trích yêu cầu/điểm từ hai sheet 50%, trạng thái và bằng chứng
  evidence/day1/             Output kiểm thử, thống kê và thông tin runtime
  evidence/day2-final/       Bằng chứng batch end-to-end và code/runtime hashes
  evidence/day3/             Bằng chứng stream, retention probe và giao diện Flink thật
references/coursework/       Đề tổng và workbook rubric gốc
.gitignore                   Loại dữ liệu lớn, môi trường cục bộ và secrets khỏi Git
.gitattributes               Chuẩn hóa line endings khi làm trên nhiều máy
generator/                   Mô phỏng máy, lỗi vận chuyển, lưu nguồn và validator
  configs/dev.json           Seed, số máy, thời gian, tỷ lệ lỗi và cơ chế hao mòn
  configs/chunked-pilot.json Pilot liên tục vượt guard 100k của lệnh cũ
  chunked.py                 Writer có giới hạn, exact sampling và stream arrival heap
  chunk_quality.py           Validator tuần tự, grid/interval và bounded stream join
  capacity.py                Ước tính riêng nguồn/output/shuffle/log/reserve
scripts/                     Lệnh PowerShell chạy generator, batch và stream verification
pipelines/                   PostgreSQL/Spark, feature/label, Kafka/Flink và kiểm tra thời gian
airflow/dags/                DP1 Bronze → DP2 Silver/Gold → DP3 features, validation và metadata
ml/                          Baseline logistic regression và metrics/threshold
infra/                       Compose/image, Terraform VM, Ansible, Airflow/DataHub và source SQL
tests/                       Bộ dữ liệu biết đáp án, biên và corruption tests
requirements.txt             Host dùng standard library; dependency Spark/Flink khóa trong Dockerfile
requirements-scale.txt       Codec JSON tùy chọn cho source runner riêng trên VM
```

Nguồn được sinh tại `data/source/`, báo cáo các lần chạy tại `outputs/`; hai thư mục này không đưa vào Git.

## Cấu trúc triển khai dự kiến

Các thư mục sau chỉ được tạo khi bắt đầu phần tương ứng:

```text
services/                    API, predictor và MCP server
agents/                      Agent, prompt, gateway routing và evaluation
```

## Cách chạy

Từ repo root trong PowerShell 7, với Python 3.13 (đã kiểm trên 3.13.14):

```powershell
./scripts/run-day1.ps1 -RunName practice1
Get-Content outputs/practice1/quality.json
```

Script tự tạo `.venv` nếu chưa có; không cần kích hoạt venv hoặc cài package. Chọn RunName mới mỗi lần; source run đã có sẽ được giữ nguyên. Nếu PowerShell chặn script, bạn có thể gọi `pwsh -NoProfile -ExecutionPolicy Bypass -File ./scripts/run-day1.ps1 -RunName practice1` cho tiến trình đó; không cần thay policy toàn máy. Đã kiểm trên PowerShell 7.6.5; PowerShell 5.1 chưa được kiểm chứng.

Để học từng bước, chọn thư mục nguồn chưa tồn tại:

```powershell
python -m venv .venv # chỉ cần khi chưa có .venv
./.venv/Scripts/python.exe -m unittest discover -s tests -v
./.venv/Scripts/python.exe -m generator generate --config generator/configs/dev.json --output data/source/manual1
./.venv/Scripts/python.exe -m generator inspect --source data/source/manual1 --report outputs/manual1-quality.json
```

Nguồn thử khoảng 43,58 MB gồm offline, stream fixture và manifest; **chưa phải dataset offline 100 GB**. Bước generator ghi stream fixture có retry và arrival order; script ngày 3 bên dưới gửi fixture tới Kafka. Đọc [data contract](docs/data-contract.md) để hiểu timestamp và mẫu số tỷ lệ; [mốc ngày 1](docs/day1-generator.md) có kết quả đo, bằng chứng và phần còn thiếu. Sơ đồ platform đích và trạng thái từng luồng ở [architecture](docs/architecture.md).

Mốc PostgreSQL/Spark (cần Docker và ít nhất 2 GiB RAM trống):

```powershell
./scripts/run-day2.ps1 -RunName practice2
Get-Content outputs/practice2/batch-report.json
```

Run `day2-final` đã qua; chỉ nhận output mới khi có `_RUN_SUCCESS.json` và `batch-report.json` với `passed=true`. Chọn RunName mới để giữ kết quả cũ. [Tài liệu batch](docs/day2-batch.md) có số liệu, ví dụ truy nguồn tới dự đoán, câu tự kiểm tra và giới hạn đánh giá.

Kafka/Flink, với source nhỏ mặc định đã sinh từ ngày 1:

```powershell
./scripts/run-day3.ps1 -RunName practice3
Get-Content outputs/practice3/stream-report.json
```

Script chỉ chạy profile streaming, tự dừng Kafka coursework sau kiểm chứng; dữ liệu/volume và sổ event_id giữ cục bộ. Chỉ nhận kết quả khi có `data/stream/<run>/_RUN_SUCCESS.json` cùng report `passed=true`. [Giải thích stream](docs/day3-streaming.md) nêu quy tắc watermark, ledger và các giới hạn về recovery.

Chuẩn bị nguồn lớn bằng generator theo chunk (không cần Docker):

```powershell
./scripts/run-chunked.ps1 -RunName practice-chunks
Get-Content outputs/practice-chunks/chunked-report.json
```

[Hướng dẫn chunked](docs/chunked-generator.md) giải thích state liên tục qua file, kiểm byte/logical repeat và ngân sách đĩa. Lệnh này chỉ chạy pilot và lịch sử dài gấp bốn; kế hoạch 100 GB là ước tính. Loader/batch/Flink cũ vẫn dùng nguồn ngày 1, chưa tự chuyển sang layout chunked.

[Source runner trên VM](docs/scale-100gb.md) bổ sung cache/codec đã kiểm byte tương đương, đo tài nguyên và giới hạn thời gian phiên. Dependency tùy chọn cài bằng `python -m pip install -r requirements-scale.txt` trong venv trước khi chạy các test codec; bộ test cơ bản vẫn chạy bằng standard library và báo skip nếu thiếu codec. Dữ liệu lớn ở đĩa VM, không tải qua Git khi clone trên laptop.

## Tài liệu đầu vào

- [Đề tổng](references/coursework/coursework_proposal.md).
- [Rubric gốc](references/coursework/rubic/EDAI%20K11.xlsx).
- [Repo tham khảo](https://github.com/cvinhnguyen/card-fraud-platform).
