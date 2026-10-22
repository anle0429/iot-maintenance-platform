# IoT Maintenance Platform

Coursework EDAI K11: giám sát máy bơm công nghiệp bằng dữ liệu cảm biến mô phỏng, dự đoán sự cố trong một giờ tới và trợ lý tra cứu tình trạng máy/tài liệu.

**Trạng thái:** đã chạy luồng PostgreSQL/Spark → feature → baseline dự đoán, và Kafka/Flink xử lý late arrival/replay. **55 unit test, 19 kiểm tra Spark/data và 10 kiểm tra tích hợp streaming qua**; phát 116.928 messages giữ đúng 57.600 readings/5.760 cửa sổ. Tập test ML chưa có ca hỏng; chưa có API hoặc dataset offline 100 GB. Xem [batch](docs/day2-batch.md) và [kết quả streaming](docs/day3-streaming.md).

## Mục lục

- [Phạm vi và dữ liệu](docs/domain-proposal.md)
- [Hợp đồng dữ liệu](docs/data-contract.md)
- [Kiến trúc và trạng thái deployment](docs/architecture.md)
- [Kết quả và giải thích ngày 1](docs/day1-generator.md)
- [PostgreSQL nguồn, Spark và baseline](docs/day2-batch.md)
- [Kafka/Flink: event time, late arrival và replay](docs/day3-streaming.md)
- [Đo nguồn trung gian và RAM](docs/generator-throughput.md)
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
  rubric-tracker.md          Trích yêu cầu/điểm từ hai sheet 50%, trạng thái và bằng chứng
  evidence/day1/             Output kiểm thử, thống kê và thông tin runtime
  evidence/day2-final/       Bằng chứng batch end-to-end và code/runtime hashes
  evidence/day3/             Bằng chứng stream, retention probe và giao diện Flink thật
references/coursework/       Đề tổng và workbook rubric gốc
.gitignore                   Loại dữ liệu lớn, môi trường cục bộ và secrets khỏi Git
.gitattributes               Chuẩn hóa line endings khi làm trên nhiều máy
generator/                   Mô phỏng máy, lỗi vận chuyển, lưu nguồn và validator
  configs/dev.json           Seed, số máy, thời gian, tỷ lệ lỗi và cơ chế hao mòn
scripts/                     Lệnh PowerShell chạy generator, batch và stream verification
pipelines/                   PostgreSQL/Spark, feature/label, Kafka/Flink và kiểm tra thời gian
ml/                          Baseline logistic regression và metrics/threshold
infra/                       Compose, image Spark/Flink và schema PostgreSQL nguồn
tests/                       Bộ dữ liệu biết đáp án, biên và corruption tests
requirements.txt             Host dùng standard library; dependency Spark/Flink khóa trong Dockerfile
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

## Tài liệu đầu vào

- [Đề tổng](references/coursework/coursework_proposal.md).
- [Rubric gốc](references/coursework/rubic/EDAI%20K11.xlsx).
- [Repo tham khảo](https://github.com/cvinhnguyen/card-fraud-platform).
