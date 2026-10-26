# Airflow DP1–DP3 và DataHub trên VM

Mốc 26/10/2026 nối tiếp generator theo chunk và Spark đã benchmark. **Đã chạy thật DP1–DP3, một lỗi validation và batch phục hồi trên VM `iot-cw-vm`**, không thay lịch coursework. [Đối chiếu tổng](evidence/orchestration/verification.json), [runtime/hash code](evidence/orchestration/runtime.json), [ảnh ba DAG](evidence/orchestration/screenshots/airflow-three-dags-recovered.jpg).

## Kết quả đã kiểm chứng

Nguồn **18.632.173 byte JSONL offline không nén**, 20 máy × 48 giờ liên tục, seed 42; receipt qua 14 checks. Manifest SHA `139ca56f976c0799fae0e37bc227a6dc173e395987271fcbeea844450f8836a9`. Hai batch sạch dùng cùng nguồn; không tái sinh seed để sửa kết quả.

| Stage | Đầu ra đọc lại | Rules qua | Thời gian DAG batch phục hồi |
|---|---|---:|---:|
| DP1 | Bronze 58.752 telemetry,20 máy,31 sự cố,39 bảo trì |17/17|49,356s|
| DP2 | Silver/Gold57.600 unique,20 machine versions,3 factory; loại1.152 export trùng |32/32|69,385s|
| DP3 |11.520 feature rows ×13 cột và11.520 label rows riêng |15/15|53,480s|

**91 unittest qua trên PC Windows/Python3.13 và VM Linux/Python3.12**; DP3 còn chạy11 fixture Spark biết đáp án về schema, duplicate, SCD, label/arrival/split. Không cộng chúng thành64 rule dữ liệu: 64 là17+32+15 validation checks trên output pilot. Đây là phép kiểm đúng, không phải benchmark tối ưu.

- `orchestration-good-26`: cả ba DAG thành công từ19:47:14 đến19:50:33UTC.
- `fixture-null-machine-26`: ingest DP1 thành công, validate thất bại đúng ba rule `exact_multiset_readback`, `required_fields_and_times`, `machine_foreign_key`; publish metadata thành công, release `upstream_failed`, **không có DP2/DP3** cho batch lỗi.
- `orchestration-recovery-26`: cả ba DAG thành công từ19:53:53 đến19:56:45UTC. Retry ingest DP1 đã hoàn tất trả exit0 và giữ nguyên hash cả7 Parquet parts; không ghi thêm.

DataHub OSS đã đọc lại **14 output datasets,3 DataFlows,6 DataJobs,64 assertion definitions** và kết quả theo từng run qua GMS/GraphQL. Bốn entity nguồn JSONL bổ sung thành18 datasets; tìm kiếm IoT hiển thị27 dataset/pipeline/task entities. Rule khóa máy có lịch sử **PASS → FAIL → PASS**, không xóa lần lỗi sau phục hồi. [API states](evidence/orchestration/orchestration-recovery-26-airflow.json), [indexed lineage/assertion history](evidence/orchestration/orchestration-recovery-26-datahub.json), [bad-run states](evidence/orchestration/fixture-null-machine-26-airflow.json).

Ảnh là giao diện thật qua IAP, không vẽ lại: [DP1](evidence/orchestration/screenshots/airflow-dp1-success.jpg), [DP2](evidence/orchestration/screenshots/airflow-dp2-success.jpg), [DP3](evidence/orchestration/screenshots/airflow-dp3-success.jpg), [validation chặn downstream](evidence/orchestration/screenshots/airflow-validation-blocked.jpg), [schema feature](evidence/orchestration/screenshots/datahub-feature-schema.jpg), [job/lineage](evidence/orchestration/screenshots/datahub-feature-lineage.jpg), [feature quality](evidence/orchestration/screenshots/datahub-feature-quality.jpg), [lịch sử PASS/FAIL/PASS](evidence/orchestration/screenshots/datahub-validation-pass-fail-pass.jpg), [catalog](evidence/orchestration/screenshots/datahub-catalog.jpg). UI dùng giờ America/Chicago; JSON API ghi UTC.

Runtime nhận working-tree snapshot qua IAP, chưa clone một commit mới khi chạy. Đã đối chiếu **59 file source/config runtime khớp PC**; hashes trong runtime report cho phép đối chiếu với commit được push sau mốc. VM 8 vCPU/64 GiB; tại collection còn520,31GB trên HDD và133,86GB trên SSD. Ngân sách scratch135GB cũ **cần tính lại** trước big run vì image/platform chiếm thêm SSD. Chưa sinh100 GB hoặc benchmark feature join lớn; test ML cũ vẫn0positive.

[Đối chiếu nội dung Git](evidence/orchestration/git-source-mapping.json): 56 file giống từng byte; 3 file cấu hình/Dockerfile chỉ khác CRLF→LF do Git chuẩn hóa xuống dòng. Toàn bộ file Python giống từng byte. Hash snapshot gốc vẫn giữ nguyên trong bằng chứng; mapping này không được tính là chạy lại pipeline từ một clone.

## Mỗi thành phần làm gì?

| Pipeline | Ingest bằng Spark | Validate đầu ra đã lưu | Sau validation |
|---|---|---|---|
| `iot_dp1_bronze` | Đọc năm bảng JSONL theo catalog; giữ duplicate/provenance, ghi bốn bảng Bronze | Multiset nguồn/Parquet, số dòng, trường bắt buộc, thời gian, firmware v1/v2, khóa máy | Gửi schema, lineage và assertions; chỉ giải phóng DP2 khi validation và gửi metadata đều thành công |
| `iot_dp2_warehouse` | Latest `created_ts` theo `event_id`, Silver, SCD2 machine, dim factory và ba fact Gold | Unique key, replay, export mới nhất, bảo toàn số dòng và khóa dim/fact | Gửi metadata rồi giải phóng DP3 |
| `iot_dp3_features` | Snapshot mỗi 5 phút, feature 10 phút và bảng nhãn riêng | Grain máy/thời điểm, event/arrival time, cửa sổ nhãn, censoring, fixture biết đáp án và truy ngược một vector | Gửi metadata; chưa train/deploy mô hình trong DAG này |

Mỗi DAG có `ingest → validate → publish_to_datahub`. Task publish dùng `all_done` để kết quả validation **FAIL** cũng vào DataHub. Task `release_next_pipeline` cần cả validate và publish thành công, nên dữ liệu lỗi không đi tiếp. Lỗi hạ tầng trước khi có báo cáo validation không được giả thành một lỗi chất lượng dữ liệu.

`pipelines/orchestrated_job.py` dùng cùng Spark 3.5.9, adapter chunked và logic feature đã có. LocalExecutor của Airflow điều phối; mỗi task Spark chạy container riêng 4 CPU/heap 10 GiB/giới hạn 14 GiB. Pool `spark_jobs` chỉ có một slot. DataHub SDK chạy image riêng 2 GiB để tránh xung đột thư viện với Airflow.

## Dữ liệu và quy tắc đúng

Nguồn pilot liên tục 20 máy × 48 giờ, seed 42; giữ schema evolution, duplicate export, late arrival và sự cố gắn với mô phỏng. Receipt kiểm tra ngữ nghĩa và hash catalog/file trước khi Spark đọc. Nguồn JSONL là bất biến; output nằm ở `data/orchestrated/<batch>/dp1|dp2|dp3`.

Stage ingest chỉ ghi mới, không append/overwrite. Retry chỉ tái sử dụng khi manifest, receipt, input stage và hash từng part còn khớp. Thư mục dở dang bị từ chối; chọn batch mới để giữ log điều tra. Bước sau phải đọc được validation `passed=true` gắn đúng SHA của manifest đầu vào. Chạy lại cùng nguồn dưới batch mới vẫn tạo cùng grain logic; `created` là thời gian materialize thực nên có thể khác.

Bronze giữ cả bản export trùng; Silver giữ bản `created_ts` mới nhất, từ chối payload xung đột. Feature chỉ dùng telemetry có `event_ts <= event_timestamp` và `received_ts <= event_timestamp`, trong `(t−10 phút,t]`; sự cố quá khứ phải có `recorded_ts <= t`. `created_ts` của telemetry là thời gian export offline, còn `received_ts` mô phỏng thời điểm reading có sẵn ở nguồn. Đây là replay theo đồng hồ mô phỏng, chưa chứng minh độ trễ vận hành online/Feast.

Nhãn không nằm trong feature. Chỉ máy đang hoạt động và đủ quan sát mới có nhãn 0/1; sự cố bắt đầu trong `(t,t+1h]` cho nhãn 1. Khoảng có can thiệp bảo trì bị loại, phần đuôi thiếu quan sát giữ NULL. Gold SCD2 trong nguồn chunked hiện có một version/máy; fixture cũ kiểm cả đổi version và biên interval. Join Gold theo event time không tự chứng minh feature PIT.

## Metadata và validation

DataHub lưu dataset URN ổn định theo tên bảng, schema từ Parquet readback, profile số dòng, đường dẫn batch và SHA nguồn. Mỗi DAG là một DataFlow; task `ingest` và `validate` là DataJob. Producing job có input/output dataset thật; từng bảng có upstream riêng, gồm cả quan hệ Silver/Gold trong DP2.

Mỗi rule là assertion `CUSTOM`, nguồn `EXTERNAL`; Spark là nơi tính kết quả. Publisher tạo `assertionInfo` trước `assertionRunEvent`, dùng đúng dataset URN cho `asserteeUrn`. Run ID và thời điểm validation phân biệt lịch sử PASS/FAIL. SDK đọc lại schema, table lineage, producing-job inputs/outputs và assertion definitions từ GMS; phần kiểm chứng còn đọc lịch sử run qua GraphQL và chụp UI thật. DataHub không tự thực thi các rule Spark này.

Gold ở đây là Parquet trên một VM, chưa phải một warehouse SQL với indexing/HA. Chưa tuyên bố đạt quy mô 100 GB, tối ưu feature join lớn, Feast, KFP hoặc chất lượng mô hình.

## Tự chạy trên VM đã chuẩn bị

Trong repo trên VM Linux (Docker và hai volume đã mount):

```bash
docker build -t iot-coursework-spark:3.5.9 -f infra/spark/Dockerfile .
sudo python3 scripts/start-orchestration.py
sudo python3 scripts/verify-orchestration.py prepare
sudo python3 scripts/verify-orchestration.py health
sudo python3 scripts/verify-orchestration.py trigger --batch my-good-run
sudo python3 scripts/verify-orchestration.py snapshot --batch my-good-run
sudo python3 scripts/verify-orchestration.py metadata --batch my-good-run
```

VM hiện tại đã có image Spark này; build dùng cache nếu không đổi dependency. Chờ health ổn và cả ba DAG được import trước khi trigger. `trigger` tạo pool một slot, bỏ pause các DAG, rồi chỉ gọi DP1; chính DAG giải phóng DP2/DP3. Lặp `snapshot` để đọc trạng thái thật. Chọn batch mới nếu một ingest dở dang; không xóa dữ liệu đang dùng để chạy lại.

`metadata` xác nhận cả indexed lineage/producing job và lịch sử assertion run; index có thể cần vài giây sau publish. Script `collect-orchestration-evidence.py` kiểm đúng ba batch của mốc này, chạy thử retry và thu báo cáo nhỏ vào `outputs/orchestration/evidence`, từ chối ghi đè evidence đã có. Nó không sao chép Parquet/secrets lên Git.

Kiểm tra lỗi có chủ đích trên **batch riêng**:

```bash
sudo python3 scripts/verify-orchestration.py trigger --batch fixture-null-machine --fault null_machine
sudo python3 scripts/verify-orchestration.py snapshot --batch fixture-null-machine
```

Chỉ batch bắt đầu bằng `fixture-` được phép chèn lỗi. Nguồn gốc giữ nguyên; một event trong output Bronze mất `machine_id`. Mong đợi ingest thành công, validate thất bại, publish thành công, release bị chặn và không có DP2/DP3 cho batch này. Chạy batch sạch mới để chứng minh phục hồi.

## Truy cập và giới hạn triển khai

VM chỉ publish `127.0.0.1:18480` (Airflow), `127.0.0.1:19002` (DataHub UI), `127.0.0.1:18080` (GMS). Dùng SSH qua IAP và port forwarding để mở UI từ máy cá nhân; không thêm firewall public. Tài khoản UI là `learner`; mật khẩu sinh riêng ở `data/runtime/secrets/orchestration-users.json`, chỉ đọc tại máy mình, không đưa vào chat/Git. Airflow SimpleAuthManager chỉ dành cho môi trường học/thử nghiệm.

Trên Windows PowerShell có gcloud, đặt Project ID của mình trong biến `$projectId` và mở một terminal dành cho tunnel:

```powershell
gcloud compute ssh iot-cw-vm --project=$projectId --zone=us-central1-a --tunnel-through-iap --quiet `
  --ssh-flag=-N --ssh-flag=-L --ssh-flag=127.0.0.1:18480:127.0.0.1:18480 `
  --ssh-flag=-L --ssh-flag=127.0.0.1:19002:127.0.0.1:19002
```

Giữ terminal đó mở; vào `http://127.0.0.1:18480` và `http://127.0.0.1:19002`. PuTTY của Windows cần `-L` và giá trị là **hai flag riêng**. Không chạy song song các lệnh gcloud trong cùng working directory vì SDK hiện có tranh chấp `tmpfile`.

Airflow scheduler có Docker socket và DAG là mã tin cậy, tương đương quyền điều khiển container trên host; không cho người không tin cậy sửa DAG hoặc truy cập UI. GMS tắt xác thực ở mạng Docker riêng và loopback; không dùng cấu hình này cho môi trường công khai/nhiều tenant. MySQL/Postgres và broker/search không publish port. Docker volumes nằm trên SSD đã cấu hình; VM vẫn giữ giới hạn tự STOP sau 8 giờ mỗi start. STOP không xóa đĩa hoặc dữ liệu.

## Phiên bản và nguồn chính thức

- [Airflow 3.1.7: API v2, SimpleAuthManager, LocalExecutor](https://airflow.apache.org/docs/apache-airflow/3.1.7/installation/upgrading_to_airflow3.html). Image Python 3.12, constraints đúng release; Docker SDK 7.1.0.
- [DataHub quickstart-profile v1.6.0.3](https://github.com/datahub-project/datahub/blob/v1.6.0.3/docker/quickstart/docker-compose.quickstart-profile.yml), [hotfix LTS v1.6.0.3](https://github.com/datahub-project/datahub/releases/tag/v1.6.0.3). Compose được giản lược/adapt theo [Apache-2.0](../infra/datahub/UPSTREAM_LICENSE), không tái sử dụng mã nghiệp vụ. Server/GMS/frontend/upgrade 1.6.0.3, SDK 1.6.0 ở container riêng; Kafka 8.0.0 cho metadata, OpenSearch 2.19.3, MySQL 8.0.44, PostgreSQL 16.11 cho Airflow. Kafka metadata độc lập với Kafka 3.9.1 của bài stream trước.
- [External custom assertions](https://github.com/datahub-project/datahub/blob/v1.6.0/docs/api/tutorials/assertions.md). Metadata được phát từ kết quả Spark, không phải assertion giả hoặc DataHub Cloud native scheduler.

Phiên bản phụ trợ, image ID/digest thực và kết quả chạy được lưu trong bằng chứng runtime; tag không thay thế việc ghi image thực.

Các lỗi triển khai đã sửa trước khi lấy bằng chứng: bộ Airflow constraints xung đột dependency SDK nên tách image; Compose DataHub cũ có image kafka-setup không được phát hành nên dùng profile KRaft/internal registry mới; hotfix cần system-client secret dùng chung; LTS không cấp session cho user chưa có entity nên bootstrap `learner`/role Admin trong catalog. Mật khẩu và token signing/system secrets sinh riêng, không dùng giá trị mẫu của quickstart. Các lượt khởi động lỗi chỉ giữ log local, không coi là kết quả thành công.
