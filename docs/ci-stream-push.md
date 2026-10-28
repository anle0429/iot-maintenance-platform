# CI hai job stream feature: offline và online

Ngày28/10/2026 trên cùng VM8CPU/64GiB: **Jenkins `iot-stream-offline/3` SUCCESS77,814giây** và **`iot-stream-online/2` SUCCESS77,355giây**. Mỗi pipeline có7stage tính post, tự triển khai Airflow và chạy thật2task: ghi rồi replay. Source **`49115d5a7dcb8c331e8169e8a37df462743d921f`**,124file Git seal/source-after; không lấy số lần unit test thay cho kết quả SDK hoặc task thực.

[Pipeline](../infra/ci/StreamPush.Jenkinsfile), [driver](../scripts/ci-stream-push.py), [hai DAG](../infra/ci/iot_ci_stream_push.py), [worker](../feature_store/staged_job.py), [OfflineStage/OnlineStage](../feature_store/staged_push.py). [Manifest225payload](evidence/ci-stream-push/manifest.json) gồm220payload native, native-manifest và bốn JPEG gốc; SQLite/Parquet/registry runtime/helper/setup/secrets không đi vào Git.

![Offline3 SUCCESS](evidence/ci-stream-push/screenshots/jenkins-offline3-success.jpg)

![Online2 SUCCESS](evidence/ci-stream-push/screenshots/jenkins-online2-success.jpg)

## Nguồn và hai trách nhiệm

CI **đọc lại ba cửa sổ đã chốt từ Kafka/Flink thật ngày27/10**, không chạy lại nguồn đang không đổi. [Projection](../tests/fixtures/stream-finalized-native.json) khớp từng row và SHA của [source report gốc](evidence/stream-push/warm/feast/source-report.json); ba module pipeline có hash không đổi. Nguồn gốc đã có [live push](stream-feast-push.md); không đổi nhãn bằng chứng cũ thành live run mới của CI.

| Máy/window end mô phỏng | Count | Sum | Mean | Vibration max |
|---|---:|---:|---:|---:|
|P-001/00:10 ngày01/10|3|60|20|3|
|P-001/00:20 ngày01/10|1|40|40|4|
|P-002/00:10 ngày01/10|1|100|100|10|

`event_timestamp` là cuối cửa sổ ngày01/10; `created` giữ lúc Flink chốt27/10; thời điểm CI/ghi store là28/10. Native historical comparison tắt bộ lọc created để đối chiếu dữ liệu, không nhận rằng dữ liệu đã sẵn sàng tại simulated window end. Bốn feature tumbling phục vụ giám sát; model dự đoán vẫn dùng tám rolling feature riêng.

**F36/offline:** dùng native Feast `PushMode.OFFLINE`, lưu đúng3hàng/5reading, native historical retrieval đúng cả3; online của project mới vẫn thiếu toàn bộ giá trị. Job có journal riêng, đọc lại row đã ghi trước ACK để phục hồi; replay không append. [Verification](evidence/ci-stream-push/offline-3/ci/verification.json), [3row readback](evidence/ci-stream-push/offline-3/jobs/replay.json), [2task Airflow](evidence/ci-stream-push/offline-3/ci/airflow.json).

**F37/online:** chỉ nhận release offline hoàn chỉnh, cùng source/Redis service UID; mount Parquet/registry upstream **read-only**. Copy registry vào state riêng, native `PushMode.ONLINE` ghi vector có event time mới nhất, kể cả khi đọc cửa sổ cũ sau cửa sổ mới. Giá trị mean hiện tại là **40 và100** cho hai máy. SHA Parquet giữ nguyên `4ba48fd59afb7578a30625238ed5bfc3cc2cb4ddb96547368823cf4dc5bcd971`; replay cả3intent đều noop. [Verification](evidence/ci-stream-push/online-2/ci/verification.json), [online readback](evidence/ci-stream-push/online-2/jobs/replay.json), [mount/image receipt](evidence/ci-stream-push/online-2/jobs/write-container.json), [2task Airflow](evidence/ci-stream-push/online-2/ci/airflow.json).

Hai job cùng project `iot_windows_13588bdd4f42`, nguồn `staged-ci-stream-offline-jenkins-3`. Chúng không dùng worker BOTH cũ để giả lập hai vai trò: khi offline vừa xong online thực sự trống; online mới ghi sau đó. Cả hai SDK container nghiệp vụ4GiB/1CPU, swap limit4GiB, khôngOOM, exit0. Model serving6 và project feature của API không bị ghi đè.

## Kiểm tra trước deploy và image

Mỗi pipeline chạy contract tests và **bốn tiến trình SDK mới**: lỗi sau offline write/trước ACK → phục hồi; lỗi sau online write/trước ACK → phục hồi. Hai lỗi có exit3 có chủ đích và report xác nhận ghi thật, pending intent, trạng thái store đối diện. Sau đó kiểm replay/noappend, conflict bị chặn và latest40/100. [Test offline pipeline](evidence/ci-stream-push/offline-3/tests/verification.json), [test online pipeline](evidence/ci-stream-push/online-2/tests/verification.json). [Prototype trước CI](evidence/ci-stream-push/history/prior/native-prototype/verification.json) là scope riêng/source8b8d27d; không nhận nó là Jenkins SUCCESS.

Build/probe13file trong image và native pip check; dependencies thực có [freeze offline](evidence/ci-stream-push/offline-3/requirements-installed.lock)/[freeze online](evidence/ci-stream-push/online-2/requirements-installed.lock), Feast0.67.0. Mỗi task chạy đúng imageID của release. Image test/production có **ID khác**, nhưng toàn bộ RootFS layer diffIDs, architecture, OS và runtime Config bằng nhau trong [audit offline](evidence/ci-stream-push/offline-3/runtime-parity.json)/[audit online](evidence/ci-stream-push/online-2/runtime-parity.json). Không sửa ID để biến thành giống nhau.

Collector đầu dừng vì giả định hai image ID phải bằng; runtime RootFS/Config thực tế bằng nhau. Bản source491 đã đưa phép kiểm này vào CI trước deploy. Lượt offline2/online1 source1f6 giữ nguyên trong [history](evidence/ci-stream-push/history/prior/native-manifest.json), không sửa report lịch sử.

Report cũ dùng pandas JSON mặc định mili giây, trong khi Parquet thực vẫn giữ đủ micro giây. [Đọc Parquet gốc read-only](evidence/ci-stream-push/history/precise-readback/precise-offline-readback.json) xác nhận SHA không đổi và đúng timestamp nguồn. Bản491 xuất `date_unit='us'`; offline3/online2 mới bên trên kiểm đủ micro giây và runtime parity trong chính CI. Không chỉnh thời gian hoặc sinh lại dữ liệu cũ.

Lượt `iot-stream-offline/1` thất bại trước native test/deploy vì container discovery thiếu `IOT_STREAM_DESTINATION`. Sửa truyền đúng biến chọn đích, không thay source data hoặc guard tài nguyên. [Log lỗi gốc](evidence/ci-stream-push/history/prior/offline-1/ci/discover.txt) và [Jenkins build1](evidence/ci-stream-push/history/prior/jenkins/offline-1.json) được giữ.

## Tự chạy

Kiểm artifact đã lưu, không cần VM/dependencies:

```powershell
python -S scripts/verify-ci-stream-push.py
```

Kiểm contract nhỏ trong môi trường Python hiện có:

```powershell
python -m unittest tests.test_stream_staging_contract tests.test_stream_runtime_contract tests.test_window_outbox -v
```

Native cần Jenkins/Airflow/Feast base image/K3s Redis service hiện có; source snapshot có đủ hashes. Mỗi job cần còn90phút trong phiên đã được duyệt,12GiB RAM và20GB mỗi data disk; không tự mở phiên mới. Jenkins dùng Credentials `iot-airflow` cho bước verify, không đưa password/token vào file evidence. Một executor và pool `feast_jobs` một slot giữ các lần ghi tuần tự.

Để chạy lại bản đã kiểm, chọn `SOURCE_COMMIT=49115d5a7dcb8c331e8169e8a37df462743d921f` trong `iot-stream-offline`; sau SUCCESS, lấy release `stream-offline-jenkins-N` để điền `OFFLINE_RELEASE` cho `iot-stream-online` với **cùng source commit**. Mỗi build tạo state/project mới; không sửa receipt, chạy lại helper hoặc ép replay DAG run ID cũ. Online Redis TTL1giờ: không dùng snapshot cũ để nhận demo live vẫn còn fresh. Chạy offline release mới trước khi cần demo lại.

Source mới đóng gói bằng `python scripts/package-serving-release.py --commit HEAD --output outputs/stream-release.tar`; chỉ file đã commit. Chuyển packet/manifest qua IAP và niêm phong ở `data/ci/source/<commit>/` sau full hash check. Hai job dùng cùng [Jenkinsfile](../infra/ci/StreamPush.Jenkinsfile), hai string parameters SOURCE_COMMIT/OFFLINE_RELEASE, sandbox và tên cố định `iot-stream-offline`/`iot-stream-online`. Biến model version của shared transport không thực hiện model selection; đây là CI jobs, không phải CI API/promotion.

Giới hạn: input nhỏ3cửa sổ/two machines, worker source snapshot hữu hạn, một writer/local journal, chưa distributed exactly-once hoặc power-loss-safe rewrite Parquet. Chưa continuous deployment/webhook/live scheduler cho dữ liệu mới; thao tác khởi chạy CI là thủ công, các bước sau tự động. Phạm vi streaming lớn/metadata trong mini-coursework vẫn giữ ở tracker, không bị ba row này thay thế.

Cập nhật28/10: [native fullcohort](window-cohort.md) đã kiểm5.760cửa sổ/57.600reading vào hai store và replay qua256/1024/5760; Airflow/CI giữ scope fixture nhỏ riêng. [Ảnh UI Airflow gốc](final-handover.md#ba-pipeline-feature) và các ảnh Docker RUNNING/success được ghi rõ nguồn. Chuẩn đánh giá là [57dòng final](final-rubric-tracker.md), không cộng tracker93dòng lịch sử.
