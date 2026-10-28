# Drift API qua Knative Eventing và KServe

Ngày28/10/2026, **Jenkins `iot-drift-eventing/3` SUCCESS**,143,427giây/8stage trên cùng VM8CPU/64GiB. SourceCI `869a02ee4d5dd23435175bf70f603f7f51974a19`,106file Git seal; source workload tải `1e7dc8e9686399a79e97e693af0b30806707295c`. Đây là F35: test → build → tự deploy Broker/Trigger/KServe → kiểm giao nhận thật. Model6 production-tag của lab được lấy lại qua MLflow SDK; không có promotion/model training mới.

[Pipeline](../infra/ci/DriftEventing.Jenkinsfile), [driver](../scripts/ci-drift-eventing.py), [deployment contract](../ml/drift_eventing.py), [event receiver](../services/drift_api/events.py). [Build gốc](evidence/drift-eventing-linux/jenkins/build-3.json), [8stage](evidence/drift-eventing-linux/jenkins/build-3-stages.json), [native verification](evidence/drift-eventing-linux/current/verification.json), [source sau chạy](evidence/drift-eventing-linux/sources/actual-workspace-after.json), [manifest270payload](evidence/drift-eventing-linux/manifest.json). 265payload native, native-manifest và bốn JPEG giữ byte gốc;26file bookkeeping/attempt/cache được khai rõ trong capture-scope, không có helper/setup chat, secret hoặc database runtime.

![Jenkins3 SUCCESS](evidence/drift-eventing-linux/screenshots/jenkins-eventing3-success.jpg)

## Luồng và điều kiện thành công

Publisher gửi CloudEvent binary tới Broker nội bộ. InMemoryChannel chuyển qua filter của Trigger; đúng `dev.edai.iot.drift.window` mới tới `/v1/events/drift` của KServe Standard. API xác thực envelope cố định, kiểm cửa sổ dữ liệu và lưu kết quả bằng transaction SQLite trên HDD PV/PVC Retain. **Chỉ sau khi ghi thành công mới trả204 rỗng**. API `/v1/drift:compare` tiếp tục trả JSON cho việc gọi trực tiếp.

Theo [Knative sinks](https://knative.dev/docs/eventing/sinks/) và [CloudEvents HTTP binding1.0.2](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/bindings/http-protocol-binding.md), phản hồi có thể tham gia luồng sự kiện. Trả JSON thông thường từ sink làm broker-filter báo “non-empty response not recognized as CloudEvent”. Trả ACK rỗng tránh phát sinh một reply event; không biến HTTP ACK thành đảm bảo exactly-once.

Native đã kiểm:

- **101test chính+20subtest**, không skip trênLinux; PC và clone sau guard reference101test+24subtest, một kiểm timezone chỉ POSIX được skip trênWindows. Coverage65statements/10branches **100%**, không exclude; mutation **103/119=86,5546%**,16survived và mọi trạng thái chưa hoàn tất bằng0. Scope là app HTTP đã đổi và module nhận event mới; PSI/store cũ chạy regression, không nhận score mới cho toàn bộ hệ thống. [JUnit](evidence/drift-eventing-linux/current/tests/gate/junit.xml), [coverage HTML](evidence/drift-eventing-linux/current/tests/gate/coverage-html/index.html), [gate](evidence/drift-eventing-linux/current/tests/gate/verification.json).
- Từ registry lấy model6/runbe3b/portable60dff, kiểm production marker, pin và image bytes. Reference vẫn ghi **model3**,1.024training samples; nó có **cùng data version/protocol/schema tám feature** với model6. Không đổi nhãn reference thành6 và không lấy reference từ window đang đo. [Selection](evidence/drift-eventing-linux/current/selection.json), [reference](evidence/drift-eventing-linux/current/reference.json), [image/CRI](evidence/drift-eventing-linux/current/build.json).
- Một event sai type được ACK nhưng không vào store trong khoảng quan sát3giây. **Chín transport ID khác nhau → chín request204 thực ở KServe → một cửa sổ lưu**, đối chiếu SDK histogram với SQLite đọc riêng. Replay trực tiếp trả đúng JSON đã lưu; cùng window ID mà đổi dữ liệu trả409. [Publisher](evidence/drift-eventing-linux/current/publisher.json), [metrics trước replay trực tiếp](evidence/drift-eventing-linux/current/metrics-after-delivery.prom), [SQLite readback](evidence/drift-eventing-linux/current/sqlite-readonly.json), [conflict](evidence/drift-eventing-linux/current/conflict.json).

## Tải thực trên cùng VM

Locust2.47.0,1và10người gửi, pacing0,1giây/người/ramp2người mỗi giây, mỗi lượt cấu hình60giây; HTML ghi cửa sổ lấy mẫu59giây. Mỗi thành phần Broker ingress/filter và KServe sink giữ1replica trong phép đo. Đây là **replay một cửa sổ đã có**, với transport ID khác nhau; chưa đo throughput tính PSI cho các cửa sổ mới, maxcapacity hoặc mạng Internet.

| Người gửi | Requests | Lỗi | ACK/s | P95 ACK | P99 ACK |
|---|---:|---:|---:|---:|---:|
|1|598|0|10,0123|11ms|12ms|
|10|4.267|0|71,2389|210ms|880ms|

Tổng **4.865ACK khớp delta KServe204:5.079→9.944**; kết quả và một hàng SQLite không đổi. Pod identities ổn định trước/sau, HPA ingress/filter khôi phục **1–10** trong finally. P99 ở10người là880ms và max thực3.085ms; P95 qua ngưỡng500ms đã khai trước không có nghĩa mọi request dưới500ms. [Verification](evidence/drift-eventing-linux/load/fixed1/verification.json), [HTML1người](evidence/drift-eventing-linux/load/fixed1/users-1.html), [HTML10người](evidence/drift-eventing-linux/load/fixed1/users-10.html), [HPA restored](evidence/drift-eventing-linux/load/fixed1-control/hpas-restored.json). Periodic CSV có thể dừng sớm hơn aggregate cuối của HTML; không sửa số đếm CSV cho bằng HTML.

![Locust10người, báo cáo gốc](evidence/drift-eventing-linux/screenshots/locust-eventing-10-users.jpg)

## Các lỗi giữ lại

Pilot ban đầu Ready nhưng guard không chấp nhận service alias. IMC thực dùng `ExternalName` tới `imc-dispatcher.knative-eventing.svc.cluster.local`; đã thêm kiểm resolve tới ClusterIP private, từ chối alias Internet. Pilot tiếp theo gửi đúng event nhưng JSON reply làm Broker500; [publisher gốc](evidence/drift-eventing-linux/pilot-failed/resume/publisher.json)/[filter log](evidence/drift-eventing-linux/pilot-failed/mt-broker-filter-logs.txt) vẫn giữ. Không nhận pilot này là9event thành công.

CI1 bị test gate chặn:37/52mutants killed, và module mới không có mutant khi logic nhận event còn nằm trong closure. Tách logic nhận header/ACK thành hàm kiểm được trực tiếp; bổ sung property test ID/media type và lỗi ghi không được ACK/không lộ thông tin backend. Giữ [kết quả lỗi](evidence/drift-eventing-linux/failed-ci/1/tests/gate/mutmut-cicd-stats.json). Không giảm ngưỡng hay loại mutant. CI2 qua gate mới nhưng gọi K3s trực tiếp trong Jenkins không có binary; [console lỗi](evidence/drift-eventing-linux/jenkins/build-2-console.txt) giữ nguyên. CI3 dùng container điều khiển/mount CLI và socket hiện có để import image/đọc Kubernetes, không cài cluster mới.

Lượt tải đầu598/4.472requests không lỗi nhưng **guard Pod-set thất bại**: ingress/filter thêm một Pod mỗi thành phần, HPA đọc lại desired2. [Before](evidence/drift-eventing-linux/load/pods-before.json)/[after](evidence/drift-eventing-linux/load/pods-after.json)/[diagnostic](evidence/drift-eventing-linux/load/post-failure/pod-difference.json) giữ nguyên. Lượt cố định bên trên chạy mới sau hội tụ1replica; đây không phải benchmark tối ưu trước/sau hay phép đo HA.

## Tự chạy và trình bày

Kiểm chứng đã lưu, không cần bật VM hoặc tải dependencies:

```powershell
python -S scripts/verify-drift-eventing.py
```

Kiểm logic nhỏ với môi trường API đã có dependencies:

```powershell
python -m pytest tests/api/test_drift_api.py tests/api/test_drift_events.py tests/test_drift_eventing.py tests/test_drift_eventing_gate.py tests/api/test_observed.py -q
```

Trên VM đã có Jenkins/MLflow/K3s, Eventing và các dependency serving trước đó, job `iot-drift-eventing` nhận `SOURCE_COMMIT=869a02ee4d5dd23435175bf70f603f7f51974a19`, `MODEL_VERSION=6`. Source106file tương ứng đã niêm phong ở `data/ci/source/<commit>/`. Chạy Build with Parameters trong phiên được phép; job chọn build/release mới, không chạy lại helper hoặc sửa kết quả cũ. Job cần namespace `iot-drift-events` có PSA restricted, parent image `iot-coursework-drift-api:observe-jenkins-2`, referenceCM `iot-drift-reference-v1` và registry model6 production-tag. Nếu dựng từ cluster trống, tạo namespace lab/PSA sau khi cài dependency; installer chỉ cài platform Eventing. Không coi clone Git là đã có các runtime này.

Đối với source mới, `python scripts/package-serving-release.py --commit HEAD --output outputs/drift-eventing-release.tar` chỉ đóng gói file đã commit; chuyển packet+manifest qua IAP và kiểm toàn bộ hashes trước khi đặt vào nguồn CI. Pipeline có trong [Jenkinsfile](../infra/ci/DriftEventing.Jenkinsfile), dùng Pipeline sandbox và hai string parameters, kế thừa cấu hình private của [Jenkins hiện có](ci-data-pipelines.md). Không đưa credentials hay source runtime vào Git.

Platform pin [Knative Eventing1.24.0](https://github.com/knative/eventing/releases/tag/knative-v1.24.0), phát hành27/10/2026; [lịch hỗ trợ](https://github.com/knative/community/blob/main/mechanics/RELEASE-SCHEDULE.md) yêu cầu Kubernetes≥1.35, cluster thật1.35.9+k3s1. [Asset declaration](../infra/eventing/assets.json) pin byte/SHA; [installer](../scripts/install-eventing.py) dùng [quy trình YAML chính thức](https://knative.dev/docs/admin/install/eventing/install-eventing-with-yaml/), cần90phút phiên/24GiB RAM/20GB mỗi data disk và từ chối namespace platform đã có. CI admission riêng giữ90phút/12GiB/20GB mỗi disk. Không tự mở phiên VM hoặc public port.

Giới hạn: Broker memory không đảm bảo durability qua restart, chưa DLQ/poison-message recovery hoặc đa node; SQLite là store lab một VM. Window32quan sát có thời gian mô phỏng, không phải cảm biến live/groundtruth. Tiêu chí F35 này độc lập với luồng offline Airflow→KFP và [telemetry CI](ci-api-observability.md); phần còn thiếu của coursework giữ trong tracker.

Sau lần nativeCI3, guard deployment được siết32..2.048reference rows cho đúng giới hạn của detector;5contract tests với biên31/32/2.048/2.049 đã kiểm riêng. Native dùng1.024rows nên các receipt/source commit của lần chạy đó giữ nguyên; mã app/events trong image không thay đổi sau nativeCI3.
