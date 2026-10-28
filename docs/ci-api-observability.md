# CI cho telemetry của API

Pipeline [Observability.Jenkinsfile](../infra/ci/Observability.Jenkinsfile) dùng [driver](../scripts/ci-observability.py) và namespace release `observe-jenkins-N`. Mục đích: test/build/auto-deploy instrumentation cho ba API đang phục vụ cùng model production. Pipeline serving/A-B cho việc đổi phiên bản model giữ riêng; không đổi model7 candidate thành production để kiểm telemetry.

Bản chuẩn bị28/10/2026:6contract tests mới và các test phục vụ/guard liên quan đã kiểm trên PC; chưa có Jenkins SUCCESS cho pipeline này. Chỉ nhận native khi có console/stages, ảnh và readback thật.

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
python -m pytest tests/test_observability_ci.py tests/test_serving_ci.py tests/test_ab_ci.py tests/test_serving_gate_reuse.py -q
python -m pytest tests/api/test_observed.py tests/test_api_observability_gate.py -q
python scripts/package-serving-release.py --commit HEAD --output outputs/telemetry-release.tar
```

Packet chỉ lấy file đã commit. Source runtime, model export, secrets, scratch và helper vận hành không đi vào Git. [Metrics/traces đã chạy thủ công](api-observability.md) giữ scope riêng cho source2f; không đổi nhãn receipt cũ thành CI mới.
