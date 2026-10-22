# IoT Maintenance Platform

Coursework EDAI K11: giám sát máy bơm công nghiệp bằng dữ liệu cảm biến mô phỏng, dự đoán sự cố trong một giờ tới và trợ lý tra cứu tình trạng máy/tài liệu.

**Trạng thái:** generator nhỏ đã chạy với 57.600 readings; 28 tests và 35 kiểm tra dữ liệu đều qua. Hai lần sinh cùng cấu hình cho ra 7 tệp giống từng byte. Chưa có Spark/Flink pipeline, model, API hoặc dataset offline 100 GB. Xem [kết quả kiểm chứng](docs/day1-generator.md).

## Mục lục

- [Phạm vi và dữ liệu](docs/domain-proposal.md)
- [Hợp đồng dữ liệu](docs/data-contract.md)
- [Kiến trúc và trạng thái deployment](docs/architecture.md)
- [Kết quả và giải thích ngày 1](docs/day1-generator.md)
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
  rubric-tracker.md          Trích yêu cầu/điểm từ hai sheet 50%, trạng thái và bằng chứng
  evidence/day1/             Output kiểm thử, thống kê và thông tin runtime
references/coursework/       Đề tổng và workbook rubric gốc
.gitignore                   Loại dữ liệu lớn, môi trường cục bộ và secrets khỏi Git
.gitattributes               Chuẩn hóa line endings khi làm trên nhiều máy
generator/                   Mô phỏng máy, lỗi vận chuyển, lưu nguồn và validator
  configs/dev.json           Seed, số máy, thời gian, tỷ lệ lỗi và cơ chế hao mòn
scripts/                     Lệnh PowerShell chạy mốc ngày 1 và kiểm môi trường
tests/                       Bộ dữ liệu biết đáp án, biên và corruption tests
requirements.txt             Mốc 1 chỉ dùng thư viện chuẩn Python, chưa cần pip install
```

Nguồn được sinh tại `data/source/`, báo cáo các lần chạy tại `outputs/`; hai thư mục này không đưa vào Git.

## Cấu trúc triển khai dự kiến

Các thư mục sau chỉ được tạo khi bắt đầu phần tương ứng:

```text
pipelines/                   Spark, Flink, Airflow và validation
ml/                          Feature repo, training, notebook, pipeline
services/                    API, predictor và MCP server
agents/                      Agent, prompt, gateway routing và evaluation
infra/                       Docker, Kubernetes, CI/CD, IaC và monitoring
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

Nguồn thử khoảng 43,58 MB gồm offline, stream fixture và manifest; **chưa phải dataset offline 100 GB**. Stream fixture có retry và arrival order, chưa gửi tới Kafka. Đọc [data contract](docs/data-contract.md) để hiểu timestamp và mẫu số tỷ lệ; [mốc ngày 1](docs/day1-generator.md) có kết quả đo, bằng chứng và phần còn thiếu. Sơ đồ platform đích và trạng thái từng luồng ở [architecture](docs/architecture.md).

## Tài liệu đầu vào

- [Đề tổng](references/coursework/coursework_proposal.md).
- [Rubric gốc](references/coursework/rubic/EDAI%20K11.xlsx).
- [Repo tham khảo](https://github.com/cvinhnguyen/card-fraud-platform).
