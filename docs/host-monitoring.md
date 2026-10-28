# Theo dõi CPU, RAM, đĩa và mạng

**Đã chạy thật trên PC Windows và VM Linux chạy platform.** Mốc PC dùng source `658183995ce28c6ca71aa75104a478a4948305d3`/Windows exporter **0.32.0**; mốc VM bên dưới dùng node_exporter **1.12.1**. Cả hai dùng Prometheus **3.13.5 LTS** và Grafana OSS **13.3.0**,9panel/11query mỗi engine. Dữ liệu host là thật, bao gồm các ứng dụng/dịch vụ khác; không gán toàn bộ tải máy cho coursework. Bảng đầu là snapshot PC, không phải tài nguyên VM.

## Các số đo có nghĩa gì?

| Panel | Cách tính | Giá trị ở mốc kiểm cố định |
| --- | --- | ---: |
| CPU non-idle | 100 × (1 − trung bình tốc độ idle của các lõi logic) | 18,6616% |
| RAM unavailable / total | 100 × (1 − available / total) | 89,7861% |
| RAM available | RAM sẵn sàng cấp phát, có tính phần cache có thể thu hồi | 1.740.963.840byte |
| Disk unavailable / total | 100 × (1 − available / size) | 66,7097% trên C: |
| Disk available | Số byte sẵn dùng theo exporter | 332.574.416.896byte |
| Disk I/O | Tốc độ đọc/ghi, không phải tổng byte từ lúc boot | 55.460,57 / 673.371,43byte/giây |
| Network I/O | Tổng RX/TX của các NIC không phải loopback | 197.172,36 / 12.156,96byte/giây |
| Exporter reachable | Prometheus scrape thành công | 1 |
| Minimum collector success | Giá trị nhỏ nhất của4collector đang bật | 1 |

RAM available khác với chỉ phần RAM hoàn toàn trống. Theo [collector memory của bản0.32.0](https://github.com/prometheus-community/windows_exporter/blob/v0.32.0/docs/collector.memory.md), số available có tính standby/cache có thể cấp lại. Không dùng `1 − free/total` rồi gọi toàn bộ cache là RAM không thể cấp.

Tốc độ dùng [`rate()`](https://prometheus.io/docs/prometheus/latest/querying/functions/#rate) trên cửa sổ30giây; Prometheus xử lý counter reset và nội suy tới biên cửa sổ. Lấy counter tích lũy rồi gắn nhãn byte/giây sẽ sai. Tổng mạng có thể đếm trùng lưu lượng đi qua NIC vật lý và NIC ảo, nên đây không phải phép đo tốc độ một đường truyền hoặc riêng coursework.

**Không hiển thị rate khi cửa sổ chưa đủ15mẫu với scrape mỗi2giây.** Lượt xem ban đầu phát hiện đường CPU cao giả trong khoảng khởi động do chưa phủ đủ cửa sổ. Mã hiện tại để trống CPU/disk/network rates cho tới khi đủ mẫu; mất mẫu cũng có thể làm đường trống. Không thay dữ liệu thiếu bằng0. Gauge RAM/đĩa có thể xuất hiện sớm hơn.

![CPU, RAM và đĩa thật của PC; đường rate chỉ bắt đầu sau khi đủ cửa sổ](evidence/host-monitoring-pc/host-overview.jpg)

![Mạng, exporter up và collector success trên Grafana thật](evidence/host-monitoring-pc/host-network.jpg)

Hai ảnh JPEG là capture gốc từ trình duyệt, chụp sau mốc query cố định nên giá trị đang thay đổi có thể khác bảng. Không sửa pixel để ép khớp số. Màu của chuỗi dữ liệu dùng phân biệt biểu đồ; hai panel trạng thái cuối dùng0/1 đỏ/xanh.

## Triển khai và kiểm chứng

[Bộ cấu hình](../ml/host_monitoring.py) tạo runtime riêng, không ghi đè state cũ và không cài OS service. Exporter, Prometheus và Grafana đều nghe `127.0.0.1`. Alias `pc-windows` được chọn riêng cho dashboard, không dùng hostname cá nhân.

Chỉ bật4Windows collectors: CPU, memory, logical_disk và net. Prometheus có allowlist metric name; thông tin MAC, địa chỉ NIC, hostname, process/user không được nhập vào TSDB. Bằng chứng raw sample đã chọn chỉ thay4nhãn NIC bằng2alias, giữ nguyên giá trị/timestamp và quan hệ giữa các series; lưu hash trước/sau. Không đưa full exporter scrape, binary, TSDB, password hoặc runtime log lên Git.

- **22tests PASS** trên PC:21unit/contract tests và một integration chạy exporter/Prometheus/Grafana thật. Không thêm hoặc sửa API body/dependency; không nhận kết quả này thay coverage/mutation/load gates của API.
- `promtool` thật kiểm **5nhóm/35biểu thức có đáp án biết trước**:11query Windows,11query Linux,3ca counter reset/zero-boundary/missing-boundary và10ca khởi động chưa đủ mẫu phải trả rỗng. Các biểu thức fixture được so với profile hiện hành, không kiểm một bản query cũ.
- Mỗi **11query** được gọi qua cả Prometheus và Grafana ở cùng timestamp tới millisecond. [Oracle Python riêng](../tests/monitoring_oracle.py) tính lại từ gauge vectors và raw counter matrices: reset, khoảng cách sample, extrapolation, mean/sum và phép chia. Không lấy chính output đã tổng hợp làm đáp án. Oracle đối chiếu semantics của Prometheus, không phải một định nghĩa rate khác.
- Kiểm thêm bằng Win32/standard library:12logical processors,17.044.983.808byte physical memory và dung lượng C: khớp. Free disk lấy vào thời điểm gần nhau, cho sai lệch tối đa64MiB vì ứng dụng khác vẫn ghi; phép so raw-query cùng timestamp vẫn dùng dung sai số học chặt.
- [Kiểm cold-start thật](evidence/host-monitoring-pc/cold-start-check.json): ở scrape thứ3 có12bare CPU-rate series nhưng cả5query rate đã guard đều rỗng. Đây là kiểm truy vấn ngược vào thời điểm khởi động của cùng lượt PC, tách khỏi fixtures.
- Đã xem9panel trong trình duyệt, lưu2ảnh và kiểm3listener thực đều là loopback. Sau kiểm đã terminate/wait cả3process. Thời lượng107,52giây có khoảng chờ kiểm giao diện, không dùng làm benchmark. Điểm đo working set ba process khoảng434MiB tổng cộng, không phải peak hoặc ngân sách RAM cho VM.

[Verification](evidence/host-monitoring-pc/verification.json), [manifest](evidence/host-monitoring-pc/manifest.json), [mẫu đã chọn](evidence/host-monitoring-pc/raw-samples.json), [query readbacks](evidence/host-monitoring-pc/panel-queries.json) và [kiểm từ Windows](evidence/host-monitoring-pc/native-os-sanity.json) ghi rõ phạm vi.20payload nhỏ,10source bindings có execution bytes đúng Git. [Verifier](../scripts/verify-host-monitoring.py) kiểm hash/source/mọi giá trị đã vẽ/Win32 sanity/cold guard/privacy/listeners/cleanup; audit này không chạy lại dịch vụ.

Windows exporter lấy từ [release chính thức0.32.0](https://github.com/prometheus-community/windows_exporter/releases/tag/v0.32.0), binary AMD64 SHA256 `ca811672573c930c6ba0dd0fd9ac3e010b74b4e951595485fc04d06e7c608687`,33.358.776byte. Đã đối chiếu checksum nhà phát hành và GitHub asset digest trước khi chạy. Receipt của exporter và hai binary monitoring ở thư mục evidence; binary giải nén vẫn giữ cục bộ.

## Tự chạy và xem dashboard

Từ repo root, trong Python environment có dependency test của [lock serving](../infra/ci/requirements-serving-tests.lock):

```powershell
python -S scripts/verify-host-monitoring.py
python -m pytest tests/test_host_monitoring.py -q
```

Muốn chạy lại các phần dùng binary thật, giải nén Prometheus/Grafana như [mốc A/B monitoring](ab-monitoring.md), tải Windows exporter theo release/hash ở trên, rồi đặt đường dẫn thực tế. Ví dụ layout trên PC này:

```powershell
$env:IOT_HOST_EXPORTER_EXE=(Resolve-Path outputs/host-monitoring-27/windows_exporter-0.32.0-amd64.exe).Path
$env:IOT_PROMETHEUS_EXE=(Resolve-Path outputs/monitoring-27/prometheus-3.13.5.windows-amd64/prometheus.exe).Path
$env:IOT_GRAFANA_EXE=(Resolve-Path outputs/monitoring-27/grafana-13.3.0/bin/grafana.exe).Path
$env:IOT_GRAFANA_HOME=(Resolve-Path outputs/monitoring-27/grafana-13.3.0).Path
python -m pytest tests/test_host_monitoring.py tests/integration/test_host_monitoring_http.py -q
```

Thiếu binary environment thì phần tương ứng báo **SKIP**, không nhận là đã có host/dashboard thật. Test chạy trên Windows, tự chọn port còn trống và admin password ngẫu nhiên qua environment; dừng các process do nó tạo khi xong. Nó không đóng ứng dụng khác. Máy cần đủ RAM để chạy thêm ba process; không dùng số working set ở một thời điểm như trần RAM cứng.

Để xem UI trong lúc test, đặt `IOT_HOST_MONITORING_HOLD_FILE` tới **file chưa tồn tại** trước lệnh integration. Sau khi tất cả phép kiểm qua, mở `dashboard_url` trong `host-monitor/ui-ready.json` tại thư mục tmp của pytest. Tạo file hold đã chọn để kết thúc; tối đa10phút thì test tự kết thúc. Tránh dùng lại `--basetemp` chứa dữ liệu cần giữ, vì pytest quản lý/xóa thư mục đó.

[CLI chuẩn bị cấu hình](../scripts/prepare-host-monitoring.py) có thể tạo runtime riêng cho một endpoint đã xác minh:

```powershell
python -S scripts/prepare-host-monitoring.py --output outputs/my-host-monitor --endpoint http://127.0.0.1:19182 --family windows --host pc-windows --label "PC Windows actual host"
```

CLI chỉ tạo file và `launch.json`; không tự khởi động exporter hoặc các dịch vụ. Đặt `GF_SECURITY_ADMIN_USER`/`GF_SECURITY_ADMIN_PASSWORD` riêng trước khi chạy Grafana. Hai port monitor mặc định19091/13001 có thể đổi, listener vẫn loopback. Retention24giờ/256MB là chính sách TSDB, không phải giới hạn toàn bộ đĩa; `GOMEMLIMIT=384MiB` trong test là mục tiêu mềm của Go, không phải hard memory quota.

## Linux VM: kết quả chạy thật ngày28/10

Runtime/source5file lấy đúng từ Git commit `9f7e7e6aa3ad0187da1947b011bb5dd5b3f10182`, oracle4file từ `82c70f206d1c46fc030f8fb4f770c296a0f130d8`. Source được truyền bằng packet đã kiểm SHA; không lấy working files. VM hiện có8vCPU/64GiB, không tăng cấu hình hoặc đĩa để chạy monitoring.

[Verification native](evidence/host-monitoring-linux/host/verification.json) tại **01:23:48.085UTC,28/10/2026**:

| Số đo | Giá trị tại timestamp cố định |
| --- | ---: |
| CPU non-idle, trung bình8lõi logic | 36,3036% |
| RAM unavailable / total | 17,2147% |
| RAM available | 55.813.431.296B |
| Disk available `/` / HDD / SSD | 46.716.518.400 /346.321.612.800 /99.346.608.128B |
| Đọc/ghi trên whole disks | 0 /17.590.418,29B/s |
| RX/TX trên NIC không phải loopback | 58.755,21 /84.507,39B/s |
| Exporter up / minimum success của5collector | 1 /1 |

Disk read bằng0 vì các counter được chọn thực sự không tăng trong cửa sổ; raw matrix vẫn đủ mẫu. Đây là số đo toàn host khi các job và dịch vụ khác đang chạy, không phải throughput Spark hoặc chi phí của riêng monitoring. NIC ảo của Docker/Kubernetes có thể làm tổng mạng đếm trùng.

![CPU, RAM, ba filesystem và disk I/O trên VM](evidence/host-monitoring-linux/host-overview.jpg)

![Network, exporter up và năm collector trên VM](evidence/host-monitoring-linux/host-network.jpg)

Hai ảnh JPEG gốc được chụp qua IAP loopback sau snapshot số học; đồ thị tiếp tục thay đổi nên không ép ảnh khớp bảng. Alias`vm-linux` là nhãn đã chọn, không phải hostname cá nhân. UI chứng minh dữ liệu đã được visualize; [query receipts](evidence/host-monitoring-linux/host/panel-queries.json) kiểm giá trị đầy đủ khi tiêu đề/legend bị giới hạn bề rộng.

- Cả11query được đọc qua Prometheus và Grafana ở **cùng timestamp scrape**. [Oracle riêng](../tests/linux_monitoring_oracle.py) tính lại CPU mean, RAM ratio, filesystem join theo mount và disk/network counter rates từ [mẫu gốc](evidence/host-monitoring-linux/host/raw-samples.json). Tất cả khớp dung sai số học; không lấy output tổng hợp làm đáp án.
- [OS sanity](evidence/host-monitoring-linux/host/native-os-sanity.json) xác nhận8CPU, `MemTotal` và tổng dung lượng cả ba filesystem đúng hệ điều hành. RAM/đĩa available của OS được đọc bất đồng bộ nên chỉ giữ snapshot, không nhận bằng chính xác mẫu scrape trước đó. Không dùng snapshot này làm peak của job.
- [4known-answer tests](../tests/test_linux_monitoring_oracle.py) trên PC kiểm CPU25%/RAM60%/ba tỷ lệ filesystem khác nhau/rate đã biết, cửa sổ thiếu mẫu và duplicate mount. Kiểm số học native11query và unit4cases là hai phạm vi riêng.35PromQL fixture expressions của mốc PC vẫn giữ nguyên.
- Năm collector Linux:cpu,meminfo,filesystem,diskstats,netdev. [Config đang chạy lúc capture](evidence/host-monitoring-linux/runtime/prometheus.json) chỉ nhập allowlist; [tên metric thực](evidence/host-monitoring-linux/host/ingested-names.json) không chứa MAC/address/hostname/process telemetry ngoài phạm vi. Filesystem chỉ`/`, `/srv/iot-data`, `/srv/iot-fast`; disk I/O chỉ whole devices để tránh cộng cả partition.
- [Ba unit đang sống](evidence/host-monitoring-linux/host/units-live.json) chạy user`nobody`/NoNewPrivileges, hard memory64/512/768MiB và CPU quota10/50/75% tương ứng exporter/Prometheus/Grafana. `MemoryCurrent` trong cgroup khoảng25/108/675MB tại snapshot, có cả cache; không gọi đây là RSS hay peak tổng. [Listener đọc thật](evidence/host-monitoring-linux/listeners.json) chỉ127.0.0.1:19100/19091/13001.

Các service này là transient units trên VM hiện có, không enable khởi động cùng máy và không thay auto-stop8giờ. State/TSDB/binary và admin credentials nằm ngoài Git. Anonymous Viewer chỉ ở loopback, đi qua IAP; đây không phải bằng chứng Vault hoặc service authentication. Dashboard A/B được chuẩn bị cùng Grafana nhưng **chưa có target A/B tại thời điểm capture này**;21file host proof không được dùng để nhận F53 đã xong.

Binary Linux đều khớp SHA nhà phát hành trước khi chạy: [Prometheus3.13.5](https://github.com/prometheus/prometheus/releases/tag/v3.13.5), [node_exporter1.12.1](https://github.com/prometheus/node_exporter/releases/tag/v1.12.1), [Grafana OSS13.3.0](https://grafana.com/grafana/download/13.3.0?edition=oss&platform=linux). [Receipt](evidence/host-monitoring-linux/downloads.json) giữ URL, SHA và byte archive; không có enterprise binary hoặc binary trong repo. Các bản tải/hash này độc lập với archive Windows.

### Tự kiểm từ clone và vận hành trên VM

```bash
python -S scripts/verify-linux-host.py
python -m unittest tests.test_linux_monitoring_oracle -v
```

Verifier cần Git history có hai commit nguồn, chỉ dùng thư viện chuẩn:21payload hashes/18native hashes,9source bindings,11query mỗi engine, OS facts, limits và loopback listeners. [Manifest](evidence/host-monitoring-linux/manifest.json) giữ21payload/421.283B; [manifest đo tại VM](evidence/host-monitoring-linux/capture.json) được giữ nguyên và archive SCP đã kiểm SHA. Raw receipts không sửa số/nhãn/timestamp; không đưa helper setup, credentials, TSDB, model hoặc dữ liệu huấn luyện vào capsule.

Trên chính VM đã triển khai, xem trạng thái bằng `sudo systemctl status iot-node-exporter-28 iot-prometheus-28 iot-grafana-28`. Không chạy thêm bản trùng khi ba port còn được dùng. Pull/clone chỉ mang code và bằng chứng; các unit/binary/state này không tự có ở laptop hoặc sau VM reboot.

Muốn dựng một lượt Linux mới từ clone trên máy đủ tài nguyên, tải/kiểm SHA các binary ở receipt, rồi dùng [CLI](../scripts/prepare-host-monitoring.py) với output mới:

```bash
python3 -S scripts/prepare-host-monitoring.py --output outputs/linux-host-new --endpoint http://127.0.0.1:19100 --family linux --host vm-linux --label "Linux actual host"
```

Chạy node_exporter với danh sách từ `ml.host_monitoring.exporter_arguments('linux',19100)`; chạy Prometheus/Grafana với các mảng argument trong`launch.json` và homepath binary Grafana tương ứng. Giữ cả ba listener loopback, cấp admin password qua environment riêng. Các lệnh này chuẩn bị/chạy dịch vụ riêng; không làm mới nguồn telemetry hoặc huấn luyện. Runtime cũ và Secret không nằm trong clone, và CLI từ chối ghi đè output đã có.

## Phần còn lại

[Hai profile](../infra/monitoring/host-profiles.json) dùng tên metric và collector riêng theo OS. Flag filesystem include đã đối chiếu [source node_exporter đúng phiên bản](https://github.com/prometheus/node_exporter/blob/v1.12.1/collector/filesystem_common.go); query fixture và lượt Linux thật được giữ riêng như trên.

F47 có bằng chứng host metrics và ảnh trên PC lẫn VM chạy platform. Không dùng số đo PC tính tài nguyên Spark trên VM. F48logs/F49traces, Vault/service authentication và serving CI còn cần kiểm riêng. Dashboard này tách khỏi [A/B metrics](ab-monitoring.md): tải host không tự quy được cho một thí nghiệm hoặc một model.
