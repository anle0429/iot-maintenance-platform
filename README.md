# IoT Maintenance Platform

Coursework EDAI K11: giám sát máy bơm công nghiệp bằng dữ liệu cảm biến mô phỏng và dự đoán sự cố trong một giờ tới. Người dùng đã chọn [rubric final-coursework](docs/final-rubric-tracker.md) làm chuẩn bàn giao; tracker93dòng cũ giữ làm lịch sử.

**Mốc100GB đã kiểm chứng:** [Spark toàn nguồn102.447.847.856byte](docs/full-scale-ingest.md) →323,136triệu Bronze/316,8triệu Silver unique;13checks,45.852tasks/0failed, checksum trước/sau và Spark UI thật. Nguồn đã qua14quy tắc semantic readback. Query JSONL/Parquet cùng workload/ba cặp có median1.740,35/124,69giây; giới hạn cache và máy dùng chung được ghi rõ. Chưa Gold/feature/ML toàn100GB hoặc fullrunner trong Airflow.

[VM Google Cloud](docs/gcp-runtime.md)8vCPU/64GiB, Terraform/Ansible/IAP và HDD/SSD tách riêng đã chạy thật. [Airflow DP1–DP3/DataHub](docs/orchestration.md) đã kiểm pilot riêng:64rules/14bảng, chặn batch lỗi và phục hồi. [Skew benchmark](docs/skew-benchmark.md)17,28triệu Silver rows, ba cặp cùng kết quả, median15,339→8,577giây khi broadcast bảng chính sách nhà máy. Đây là các scope riêng, không thay bằng số đo fullsource.

[Feature60ngày](docs/medium-feature-dataset.md)/[Feast](docs/feast-feature-store.md) có3.456.000snapshots/3.132.184training rows theo protocol cố định; native full retrieval/nhãn và replay/corruption đã kiểm. [Airflow materialization](docs/feast-materialization.md) đã chạy initial/incremental/replay200máy, chặn lùi checkpoint và publish DataHub. [Flink→Feast](docs/stream-feast-push.md) có live push ba cửa sổ, fault/retry và readiness trước source; chưa toàn mini-coursework stream.

[KFP trên cluster bốn bước](docs/kfp-cluster.md) đã PASS với nativeFeast3.132.184dòng; testPR-AUC0,6151/F10,5510 theo protocol giữ nguyên. Modelversion2 đã đăng ký MLflow và đọc lại dữ liệu/model; KServe vẫn phục vụ version1. [Feature API→Feast→KServe](docs/feature-api.md) đã kiểm200máy/195prediction/5stopped409, cùng127PCtests/1POSIXskip,57nativefeaturetests;211statements/56branches100%. Mutation featureAPI413/48984,46%, predictor209/24684,96%; Locust10users đạt96,655req/s,p95 32ms/0fail trong60s. Chưa distributedtraining, notebook, incrementaldata versioning hoặc toàn bộ vận hành final.

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
- [Feast offline/online: PIT, compaction, full-cohort load và retry](docs/feast-feature-store.md)
- [Airflow Feast: incremental/checkpoint, chặn lùi thời gian và DataHub](docs/feast-materialization.md)
- [Flink → Feast: live push, retry, readiness và giới hạn checkpoint](docs/stream-feast-push.md)
- [ML bốn bước: KFP, protocol cố định, artifact và MLflow](docs/ml-training.md)
- [Predictor model-inside-image: API, lineage và kiểm thử](docs/model-serving.md)
- [KServe trên Kubernetes: pod và HTTP thật](docs/kubernetes-serving.md)
- [Kubeflow cluster: bốn task, artifact bền và modelversion2](docs/kfp-cluster.md)
- [Feature API: freshness, operating state, mutation và Locust](docs/feature-api.md)
- [Cách chạy](#cách-chạy)
- [Checklist final-coursework chính](docs/final-rubric-tracker.md)
- [Checklist hai sheet50% trước đây](docs/rubric-tracker.md)
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
  final-rubric-tracker.md    Chuẩn final chính, trích ô/điểm/proof gốc và bằng chứng
  rubric-tracker.md          Lịch sử hai sheet50%, không cộng với final
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

[Hướng dẫn chunked](docs/chunked-generator.md) giải thích state liên tục qua file, kiểm byte/logical repeat và ngân sách đĩa. Lệnh này chạy pilot và lịch sử dài gấp bốn. Mốc nguồn offline102,45GB và Spark Bronze/Silver đã có [kiểm chứng riêng](docs/full-scale-ingest.md); Gold/features/ML chưa chạy đủ100GB. Các profile loader/batch/Flink nhỏ vẫn dùng nguồn đã chọn, không tự chuyển sang layout chunked.

[Source runner trên VM](docs/scale-100gb.md) bổ sung cache/codec đã kiểm byte tương đương, đo tài nguyên và giới hạn thời gian phiên. Dependency tùy chọn cài bằng `python -m pip install -r requirements-scale.txt` trong venv trước khi chạy các test codec; bộ test cơ bản vẫn chạy bằng standard library và báo skip nếu thiếu codec. Dữ liệu lớn ở đĩa VM, không tải qua Git khi clone trên laptop.

## Tài liệu đầu vào

- [Đề tổng](references/coursework/coursework_proposal.md).
- [Rubric gốc](references/coursework/rubic/EDAI%20K11.xlsx).
- [Repo tham khảo](https://github.com/cvinhnguyen/card-fraud-platform).

- [Notebook ML tiếng Việt đã chạy thật](docs/training-notebook.md): Feast → nhãn snapshot → temporal split → train/validation → lưu và nạp lại model.

- [Huấn luyện phân tán trên Kubeflow](docs/distributed-training.md): hai executor thực thi task LR, nguồn trung gian60ngày.
- [Phiên bản dữ liệu tăng dần](docs/incremental-data-versioning.md): nativeMLflow, phần đổi mới được upload và fullrestore đã kiểm.
- [Generator và API drift](docs/drift-detection.md): cấu hình lệch cảm biến, bảng nhãn/feature thật, HTTP và kiểm thử.
- [Deploy API bằng Helm](docs/api-deployment.md): rolling update và tự rollback đã thử trên hai API.
- [Đối chiếu nội dung lớp cho novel ideas](docs/novel-ideas.md): hai lịch K9 người dùng cung cấp, không thay rubric K11 final.
- [Năm lớp chính của vòng đời ML](docs/ml-class-design.md): trách nhiệm, giao diện, adapter/factory và dependency injection trong mã đã kiểm.
