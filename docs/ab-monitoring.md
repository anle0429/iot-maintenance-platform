# Theo dõi hai phiên bản bằng Prometheus và Grafana

**Đã chạy thật trên PC Windows ngày 27/10/2026; chưa chạy trên VM.** Prometheus **3.13.5 LTS** lấy metrics từ A/B API; Grafana OSS **13.3.0** đọc Prometheus và hiển thị sáu biểu đồ. Code chạy ở commit `9fa1326d68615c4120aa99dee65387766a9a9ea9`. Registry và feature Feast là fixture; hai version pin dùng cùng weights legacy. Kết quả chứng minh đường monitoring, chưa chứng minh model mới tốt hơn hoặc A/B đã triển khai qua Jenkins/Kubernetes.

Sau mốc PC, native serving CI sửa quyền đọc bundle và cấu hình thư mục mutation. Verifier giữ77source bindings **lịch sử**; hai file CI mới được đối chiếu riêng: ba HTTP helper đã dùng không đổi AST, cấu hình mutation chỉ thay copy script đơn thành thư mục.75file runtime/API/monitoring còn lại vẫn khớp. Không nhận các số đo PC này thành proof cho native CI hoặc cấu hình mutation mới. [Host monitoring trên Linux VM](host-monitoring.md#linux-vm-kết-quả-chạy-thật-ngày2810) đã có proof riêng; không đồng nghĩa đã có dashboard A/B native.

## Thành phần và số đo

[Bộ tạo cấu hình](../ml/ab_monitoring.py) viết một thư mục runtime mới, gồm Prometheus file discovery, datasource và dashboard provision của Grafana. Mỗi lần chạy dùng state riêng; trùng thư mục sẽ bị từ chối. Hai dịch vụ chỉ nghe `127.0.0.1`. Target A/B phải là IPv4 loopback hoặc RFC1918 cụ thể; không nhận public URL, DNS, metadata endpoint hoặc URL chứa credentials. Các file sinh ra, database, password và binary không vào Git.

Datasource được gắn UID cố định trước khi provision. Theo [tài liệu Grafana](https://grafana.com/docs/grafana/latest/administration/provisioning/), biến môi trường trong file định nghĩa dashboard không được nội suy như file provisioning. Vì vậy chỉ đặt `${DS_PROMETHEUS}` rồi chép JSON sẽ chưa đủ. Mã hiện tại thay UID trong cả sáu panel và biến chọn experiment, sau đó kiểm bằng API Grafana thật.

Prometheus dùng [file service discovery](https://prometheus.io/docs/prometheus/latest/configuration/configuration/#file_sd_config), lấy số liệu mỗi hai giây. Test đổi target qua file nguyên tử, quan sát `down`, khôi phục target cũ và quan sát `up`; không cần mở endpoint reload. Thời gian giữ TSDB24giờ và giới hạn retention256MB là cấu hình giữ dữ liệu, **không phải trần tổng dung lượng thư mục hoặc RAM**. `GOMEMLIMIT=384MiB` trong test là mục tiêu mềm của Go, không phải giới hạn cứng cho process.

| Biểu đồ | Baseline/v1 | Candidate/v2 | Cách đối chiếu |
| --- | ---: | ---: | --- |
| Unique completed trials | 372 | 428 | Đếm request ID duy nhất trong ledger |
| Non200 unique trial outcomes | 0 | 0 | Đếm status khác200 của trial đã ghi |
| Unique feature snapshots | 93 | 107 | Set `(machine_id, feature_event_ts)` |
| P95 server model path | 4,9083ms | 4,9828ms | Nội suy histogram theo đúng bucket đã xuất |
| Mean risk score | 0,72340016 | 0,72340016 | Trung bình risk score từ các row thành công |
| PSI | 8feature/nhóm | 8feature/nhóm | Reference/bins cố định, không dùng nhãn |

800trial là bốn lượt qua200máy; **40concurrent retries không làm tăng trial hoặc snapshot**. Các lượt lặp lại không phải800mẫu drift độc lập. Counter này cũng không tính mọi HTTP request: các input bị chặn trước trial, như label422 hoặc ID conflict409, nằm ngoài counter ledger.

P95 ở đây đo toàn thí nghiệm hữu hạn, từ histogram server sau khi có khóa; không gồm chờ khóa hoặc mạng client và không phải P95 chính xác của mẫu latency. Chênh lệch nhỏ giữa hai nhóm trên PC không phải bằng chứng hiệu năng model. Mean score không phải accuracy. Fixture giữ feature bằng nhau; đường8feature chồng nhau trong mỗi nhóm. PSI hơi khác0 do pseudocount và kích thước reference64 so với93/107snapshot, không tự chứng minh drift thực tế.

![Dashboard Grafana thật trên PC: trial, failure, snapshot, latency và mean score](evidence/ab-monitoring-pc/grafana-overview.jpg)

![PSI của hai nhóm trên Grafana thật; các feature trùng nhau trong fixture](evidence/ab-monitoring-pc/grafana-psi.jpg)

Khoảng đứt trên biểu đồ xuất hiện khi test cố ý ngắt target metrics rồi phục hồi. Hai ảnh là JPEG gốc từ trình duyệt, không chỉnh số liệu hoặc dựng lại giao diện.

## Kiểm chứng đã thực hiện

- **24unit tests PASS**: chặn URL không phù hợp/port0/label sai, không tạo thư mục khi input sai, không ghi đè runtime cũ, không sửa template và gắn datasource đúng.
- **Một integration test PASS**: ba HTTP API thật, Prometheus/promtool thật và Grafana thật;800trial/40retry; sáu truy vấn qua cả Prometheus query engine và Grafana datasource API. **26series mỗi engine** được so với giá trị từ ledger. API readback cũng xác nhận dashboard đã provision và dùng đúng datasource.
- Experiment không tồn tại trả vector rỗng; PromQL sai trả400. Target metrics mất kết nối thật rồi được khôi phục; ledger giữ nguyên. Cả hai process monitoring chỉ có listener127.0.0.1 khi kiểm; ba thread API và hai process đã dừng sau test.
- [Verifier độc lập](../scripts/verify-ab-monitoring.py) không import implementation: kiểm20payload hashes/77source bindings,800assignment và phép tính model, bốn cycle, snapshot sets, P95 theo bucket, PSI theo counts và26giá trị qua mỗi query engine. Đây là audit bằng chứng đã lưu, không phải chạy lại hệ thống.

[Manifest](evidence/ab-monitoring-pc/manifest.json), [kết quả](evidence/ab-monitoring-pc/verification.json), [query readbacks](evidence/ab-monitoring-pc/panel-queries.json) và [projection800row](evidence/ab-monitoring-pc/ledger-observations.json) đủ để tự kiểm các con số trên. Projection chỉ giữ các cột phục vụ phép kiểm, không phải bản sao đầy đủ trace hoặc HTTP wire bytes. Raw ledger/database vẫn ở máy chạy. Ba file khi thực thi dùng CRLF; proof ghi cả hash working tree và Git LF, không nhận chúng là cùng raw bytes. Console/JUnit/promtool chỉ thay đường dẫn workspace; lưu hash trước/sau trong verification.

Binary lấy từ [Prometheus official download](https://prometheus.io/download/) và [Grafana OSS Windows](https://grafana.com/grafana/download?edition=oss&platform=windows), đã đối chiếu SHA256 của nhà phát hành trước giải nén. [Receipt tải](evidence/ab-monitoring-pc/official-downloads.json) ghi URL/version/hash/byte thực tế. Bản standalone không được cài làm Windows service. Điểm đo working set của hai process khoảng388MiB tổng cộng tại lúc chụp; đây không phải peak hoặc ngân sách cho cả workload. Thời lượng pytest83,64giây gồm khoảng chờ kiểm giao diện, không dùng làm benchmark tốc độ.

## Tự chạy

Trong Python environment có các dependency test của [lock serving](../infra/ci/requirements-serving-tests.lock), từ repo root:

```powershell
python -m pytest tests/test_ab_monitoring.py -q
python -S scripts/verify-ab-monitoring.py
```

Để tự chạy integration, giải nén hai bản standalone nêu trên vào thư mục cục bộ sau khi kiểm SHA256, rồi trỏ tới binary thực tế. Ví dụ layout đã dùng trên PC này:

```powershell
$env:IOT_PROMETHEUS_EXE=(Resolve-Path outputs/monitoring-27/prometheus-3.13.5.windows-amd64/prometheus.exe).Path
$env:IOT_GRAFANA_EXE=(Resolve-Path outputs/monitoring-27/grafana-13.3.0/bin/grafana.exe).Path
$env:IOT_GRAFANA_HOME=(Resolve-Path outputs/monitoring-27/grafana-13.3.0).Path
python -m pytest tests/integration/test_ab_monitoring_http.py -q
```

Test tự chọn port loopback còn trống, tạo admin password ngẫu nhiên qua process environment, provision một viewer chỉ trên máy hiện tại rồi dừng dịch vụ khi xong. Thiếu ba biến binary thì test báo **SKIP**, không được nhận là đã kiểm integration. Không dùng lại `--basetemp` có dữ liệu cần giữ: pytest quản lý/xóa thư mục đó.

Nếu muốn xem giao diện trong khi test, trước lệnh integration đặt `IOT_MONITORING_HOLD_FILE` tới **một đường dẫn chưa tồn tại**. Sau khi kiểm xong các API, test viết `monitoring/ui-ready.json` trong thư mục tmp của pytest; mở `dashboard_url` trong file đó. Tạo file hold đã chọn để kết thúc hoặc test tự dừng sau tối đa10phút. Không giữ dịch vụ chạy vô thời hạn.

Để chuẩn bị cấu hình cho một A/B API riêng đã chạy, dùng [CLI](../scripts/prepare-ab-monitoring.py), thay endpoint bằng địa chỉ thực đã kiểm:

```powershell
python scripts/prepare-ab-monitoring.py --output outputs/my-ab-monitor --endpoint http://127.0.0.1:18001 --experiment my-trial --label "Private runtime deployment pending"
```

CLI chỉ tạo cấu hình và `launch.json`, không khởi động dịch vụ. Trước khi chạy Grafana từ cấu hình này phải đặt `GF_SECURITY_ADMIN_USER`/`GF_SECURITY_ADMIN_PASSWORD` riêng qua environment. Cổng mặc định Prometheus19090/Grafana13000 phải còn trống; có thể đổi bằng hai tùy chọn port. Không chuyển listener thành0.0.0.0 hoặc mở firewall chỉ để xem dashboard.

## Phần còn lại

F53 vẫn **Đang làm** vì cần dashboard gắn với hai model và A/B release thật trên VM qua CI. F46 vẫn thiếu phạm vi metrics của các API khác; F47 chưa có dashboard CPU/RAM/disk/network. Điểm đo RAM bằng công cụ hệ điều hành không thay cho F47. Loopback viewer phục vụ kiểm cục bộ, chưa đáp ứng Vault hoặc service authentication của final. Native Jenkins training/serving, Linux API gates, model promotion và healthy rollback vẫn theo [bàn giao CI](ci-ab-deployment.md).
