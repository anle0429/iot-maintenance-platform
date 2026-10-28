# CI cho telemetry của API

Pipeline [Observability.Jenkinsfile](../infra/ci/Observability.Jenkinsfile) dùng [driver](../scripts/ci-observability.py) và namespace release `observe-jenkins-N`. Mục đích: test/build/auto-deploy instrumentation cho ba API đang phục vụ cùng model production. Pipeline serving/A-B cho việc đổi phiên bản model giữ riêng; không đổi model7 candidate thành production để kiểm telemetry.

Ngày28/10/2026, **Jenkins `iot-api-observability/2` SUCCESS**,274.304ms/8stage. Source `372163145c9e5d56fa02c868cf85a587981ce70c`,92file Git seal trước/sau. Native8contract tests/299test chính+21subtest PASS; coverage wrapper74statements/16branches100%, mutation221/250=88,4%/29survived/0unfinished. Ba image `observe-jenkins-2` được build/probe/import/auto-deploy;200HTTP gồm195prediction đúng công thức và5stopped409. Model6/run be3b/weights60dff giữ nguyên, model7 vẫn candidate.

[Build](evidence/ci-api-observability/jenkins/build-2.json), [stages](evidence/ci-api-observability/jenkins/stages-2.json), [ảnh SUCCESS](evidence/ci-api-observability/screenshots/jenkins-telemetry2-success.jpg), [ảnh deploy/verify](evidence/ci-api-observability/screenshots/jenkins-telemetry2-stages.jpg), [gate](evidence/ci-api-observability/current/telemetry-tests/gate/verification.json), [200HTTP](evidence/ci-api-observability/current/http-all-machines.json). Verifier96payload/92Gitinputs PASS; HTML/JUnit/mutation meta/Pod digests/source-after giữ byte gốc. JUnit có299phần tử testcase và suite count320 tính21subtest, hai field đã phân biệt rõ.

Lượt1 FAILURE ở build test overlay: `FROM sha256:...` bị Docker coi là repository `docker.io/library/sha256`, chưa deploy. Đổi sang tag nội bộ, kiểm imageID trước/sau; giữ [log lỗi gốc](evidence/ci-api-observability/history/build-1/tests-overlay-build.txt), không sửa nhãn lượt cũ. Sau STOP/START đã khôi phục Jenkins/MLflow bằng cùngimage/env/mount; stopped containers cũ được giữ. Registry chạy trên bridge nội bộ mới, không mở thêm port/public service. Bản sửa helper/CI được100PCtests/69subtests kiểm riêng; không cộng các số đó vào native regression.

Pipeline này deploy instrumentation cho model đang production, **không nhận là đổi model mới hoặc A/B mới**. [ServingCI8](ci-serving.md)/A-B1–6 giữ scope lịch sử; [Locust wrapper](observed-api-load.md) là phép đo vận hành riêng trướcCI2 với cùng mã API/dependency, chưa claim Locust trên Pod CI2. Feature lease15:18→16:18UTC hợp lệ lúc verify16:00:49UTC; demo sau đó cần prepare mới.

## Các bước

1. Đọc source packet Git có SHA từng file; kiểm phiên VM thật còn90phút, RAM/đĩa theo guard serving hiện có. Release mới không ghi đè đường dẫn servingCI8.
2. SDK lấy lại đúng model có production marker từ MLflow. So9field identity và SHA weights với API hiện tại; từ chối đổi model qua pipeline telemetry. Giữ parent images và mã nghiệp vụ hiện có.
3. Contract tests; source copy/scratch mới; regression ba API, statement/branch coverage>90%, mutation>80% chỉ wrapper mới, mọi mutant phải hoàn tất. Không lấy lại score cũ hoặc cho80% đúng bằng qua gate.
4. Build overlay từ image ID đã quan sát; kiểm source/model bytes/nonroot/pip check; import và đọc CRI config digest.
5. Guard UID/resourceVersion/image trước patch KServe; Helm feature/drift dùng atomic, feature init/API cùng image. Cấu hình store/seed/model giữ nguyên; endpoint OTLP cố định private.
6. Kiểm Pod image IDs/Ready, metrics endpoints và200máy bằng công thức portable;195operating và5stopped409. Nếu lỗi sau deploy, khôi phục có điều kiện khi tài nguyên vẫn thuộc release, không ghi đè thay đổi của người khác. Lệnh rollback chưa phải proof healthy rollback.

Native cần Jenkins/MLflow/K3s/Jaeger đang chạy và feature đã materialize thật, còn ít nhất15phút lease tại bước bind/deploy. Guard phiên VM90phút không đổi; không sửa TTL/timestamp để qua gate. Các unit test này dùng fixture, không thay proof registry/Kubernetes thật.

## Tự kiểm source trước native

Trong môi trường API/CI đã có dependency lock:

```powershell
python -S scripts/verify-ci-api-observability.py
python -m pytest tests/test_observability_ci.py tests/test_serving_ci.py tests/test_ab_ci.py tests/test_serving_gate_reuse.py -q
python -m pytest tests/api/test_observed.py tests/test_api_observability_gate.py -q
python scripts/package-serving-release.py --commit HEAD --output outputs/telemetry-release.tar
```

Packet chỉ lấy file đã commit. Source runtime, model export, secrets, scratch và helper vận hành không đi vào Git. [Metrics/traces đã chạy thủ công](api-observability.md) giữ scope riêng cho source2f; CI2 có receipt riêng cho source372, không đổi nhãn receipt cũ.
