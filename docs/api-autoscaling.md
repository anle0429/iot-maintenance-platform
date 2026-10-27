# Kiểm chứng autoscale hai API bằng số đo CPU thật

HPA quan sát CPU của pod so với `requests.cpu`, rồi điều chỉnh số replica. [Chart](../infra/charts/iot-api/templates/hpa.yaml) đặt ngưỡng60%, tối thiểu1, tối đa2 và cửa sổ ổn định khi giảm120giây. Trong lượt này request100mCPU/pod, nên mức CPU958% tương ứng khoảng958mCPU, không phải dùng gấp9lần một CPU vật lý.

Ngày27/10/2026 trên VM8CPU/64GiB hiện có, Helm3.20/K3s1.35.9 và metrics-server thật: **cả feature API lẫn drift API đã tự tăng rồi giảm1 → 2 → 1 pod Ready**. [Receipt hoàn tất](evidence/api-autoscale/native/verification.json), [timeline feature](evidence/api-autoscale/native/feature/timeline.json), [timeline drift](evidence/api-autoscale/native/drift/timeline.json). Không thay replica bằng tay hoặc giả metrics; chưa mở thêm node/cloud resource.

| Workload thực,16client/pacing50ms | Thời gian tải đo thật | Request thành công/lỗi | RPS quan sát | P50/P95 |
|---|---:|---:|---:|---:|
| GET feature P-001 từ native Feast |184,841s|48.265/0|261,117|7,028/25,449ms|
| POST drift replay một window32rows |187,210s|30.907/0|165,092|31,976/117,715ms|

Feature hoàn tất17:07:28UTC; drift17:14:04UTC. Observer yêu cầu baseline một Ready pod/CPU thấp, thấy HPA `current=desired=Ready=2` dưới tải, rồi tự trở về một pod sau ngừng tải. Response nghiệp vụ được hash và kiểm mỗi request. Drift lưu thêm đúng một cửa sổ, tổng5 → 6, dù nhận hàng chục nghìn replay; các replica dùng cùng SQLite PVC trên một node.

Đây là workload có giới hạn để kích hoạt và chứng minh HPA, **chưa phải công suất tối đa, tốc độ prediction, benchmark thuật toán drift trên window mới hoặc HA nhiều máy**. Endpoint feature ở bảng chỉ lấy feature, chưa gọi model. Drift dùng kết quả đã cache. Dịch vụ khác của coursework vẫn chạy; một tác vụ chuẩn bị runner giới hạn0,5CPU diễn ra trong một phần lượt drift, nên không coi môi trường đã cô lập để benchmark hiệu năng.

![HPA feature dưới tải: CPU thật và hai replica](evidence/api-autoscale/screenshots/feature-native-hpa-two.jpg)

![HPA drift dưới tải: CPU thật và hai replica](evidence/api-autoscale/screenshots/drift-native-hpa-two.jpg)

[Ảnh feature1→2→1](evidence/api-autoscale/screenshots/feature-one-two-one.jpg), [ảnh drift1→2→1](evidence/api-autoscale/screenshots/drift-one-two-one.jpg) và [capture hashes](evidence/api-autoscale/screenshots/capture.json).

## Cache và thời gian

Trước lượt này lease cũ đã hết. [Refresh native](evidence/api-autoscale/cache-refresh/verification.json) dựng lại riêng Redis cache tạm `iot-serving-online`, kiểm pod UID mới/DBSIZE0, chạy lại preparation Feast trên nguồn bất biến và tạo ConfigMap immutable mới `iot-feature-serving-state-helm-v2`. [HTTP thật sau refresh](evidence/api-autoscale/cache-refresh/http.json) đã gọi được prediction model1.

Cutoff dữ liệu mô phỏng vẫn29/11/2026 23:50UTC; lease dùng đồng hồ thật, chuẩn bị27/10/2026 16:45:24UTC và hết hạn sau một giờ. [CLI deploy](../scripts/deploy-api.py) thêm `--feature-seed` để chọn seed mới. Không sửa timestamp cũ để vượt guard; các Redis/Feast dự án khác giữ nguyên. Sau khi lease hết, readiness fail là hành vi dự kiến, cần preparation mới trước demo tiếp.

## Lỗi và bằng chứng

Lượt đầu dừng trước tải vì Kubernetes trả `currentMetrics: null` ngay sau khi HPA tạo. [Console lỗi gốc](evidence/api-autoscale/initial/feature-console.txt) và [source observer trước sửa](evidence/api-autoscale/native/verify-api-autoscale-before-null-metrics.py.txt) được giữ. Observer sửa cách đọc metrics thiếu/null, chờ số đo thật và dùng output directory mới; không sửa kết quả lần lỗi thành PASS.

[Capsule manifest](evidence/api-autoscale/capture/manifest.json) gắn SHA256 của55tệp native và8file source/config; trong capsule có55payload và chính manifest. [Mapping import](evidence/api-autoscale/capture/evidence-map.json) kiểm archiveSHA và từng byte khi đưa vào repo. HPA state gốc dưới tải và sau giảm giữ ở các tệp `hpa-at-two.json`/`hpa-after-load.json`; các ảnh đi kèm được chụp khi mở chính JSON gốc, không dựng lại terminal hoặc sửa pixel.

## Tự chạy

Trên VM đã có image, metrics-server, reference drift và một seed Feast mới còn ít nhất20phút lease. Dùng tên run/output mới; đặt kubeconfig và `$HELM` theo môi trường riêng:

```bash
python3 scripts/verify-api-autoscale.py --api feature --helm "$HELM" \
  --feature-seed iot-feature-serving-state-helm-v2 \
  --run autoscale-feature-new --output outputs/autoscale-feature-new
python3 scripts/verify-api-autoscale.py --api drift --helm "$HELM" \
  --reference data/drift/reference-27/reference.json \
  --run autoscale-drift-new --output outputs/autoscale-drift-new
```

Mỗi CLI tự bật HPA bằng Helm readiness gate rồi quan sát kết quả. Chỉ dùng tên seed mẫu nếu seed đó thực sự mới/còn hiệu lực; bản clone không mang cache hoặc dataset. Xem thêm [Helm/rollback](api-deployment.md) và [tài liệu HPA chính thức](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/).
