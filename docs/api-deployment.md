# Deploy hai API bằng Helm và kiểm tra tự phục hồi

[Chart dùng chung](../infra/charts/iot-api/Chart.yaml) tạo hai release riêng: `iot-feature-api` đọc Feast rồi gọi KServe; `iot-drift-api` so cửa sổ feature với reference huấn luyện. Cả hai dùng ClusterIP trong namespace `iot-features`, trên VM hiện có. Không cần VM, đĩa cloud hay cổng Internet mới.

## Cấu hình đã chạy

Helm3.20.0/K3s1.35.9+k3s1; pod UID10001, root readonly, bỏ capabilities, không mount ServiceAccount token. API drift giới hạn1CPU/512MiB; feature API1CPU/1GiB. Giá trị riêng ở [feature values](../infra/feature-api/helm-values.yaml) và [drift values](../infra/drift-api/helm-values.yaml).

RollingUpdate đặt `maxSurge=1`, `maxUnavailable=0`. Pod có health/readiness probe thật. Hook `preStop` giữ process thêm5giây để EndpointSlice/kube-proxy kịp rút địa chỉ pod trước SIGTERM. CLI [deploy-api.py](../scripts/deploy-api.py) lint chart trước, rồi luôn chạy Helm3 `upgrade --install --atomic --wait`; mỗi lượt lưu command, exit code và console gốc. [Helm3 upgrade](https://docs.helm.sh/docs/v3/helm/helm_upgrade/) mô tả cơ chế tự rollback của `--atomic`.

Reference drift được tạo từ train của model3 rồi đưa vào ConfigMap immutable. Dataset/reference đầy đủ giữ trên VM. SQLite dùng [local PVC2GiB](../infra/drift-api/storage.yaml) trên HDD hiện có, reclaim policy Retain; nhiều replica trên cùng node đọc cùng kho. Đây là dung lượng khai báo của volume, không phải một đĩa cloud mới hoặc quota filesystem tự áp đặt.

Feature API lấy seed từ một lần native Feast preparation mới tại `data/feature-serving-helm-27/state`. Cutoff nghiệp vụ vẫn là thời gian mô phỏng, còn lease/TTL dùng đồng hồ thật1giờ. Sau khi cache mất hoặc lease hết, readiness trả503; cần preparation mới đã kiểm chứng. Không thay timestamp trong seed để làm endpoint hiện xanh. Model đang phục vụ vẫn là version1; reference drift hiện gắn model3 candidate.

## Kết quả kiểm chứng thật

[Lượt hoàn tất](evidence/drift-api/rollout/verification.json) chạy ngày27/10/2026 trên cùng VM. Mỗi mẫu kiểm cả endpoint nghiệp vụ và readiness:

| API | Rolling update | Đưa image không tồn tại | Kết quả native Helm |
|---|---:|---:|---|
| Drift | 7mẫu,0lỗi | 91mẫu,0lỗi | revision5failed, revision6deployed, `Rollback to 4` |
| Feature | 18mẫu,0lỗi | 89mẫu,0lỗi | revision4failed, revision5deployed, `Rollback to 3` |

Tổng205mẫu không lỗi ở lượt đã sửa. Observer kiểm pod cũ/mới cùng hiện diện, trạng thái `ErrImageNeverPull` thật, exit code khác0 của lần upgrade lỗi, lịch sử rollback và response nghiệp vụ giữ nguyên. Drift gọi một cửa sổ đã cache; feature gọi native Feast→KServe. Đây là thay đổi marker cấu hình pod với cùng image tốtv1, chưa phải so sánh hai bản mã ứng dụng hoặc cam kết mọi request đều không gián đoạn.

Lượt đầu bắt được **1timeout/7mẫu** lúc pod cũ dừng. [Quan sát gốc](evidence/drift-api/rollout-initial/drift/rolling-observations.json), [template trước sửa](evidence/drift-api/rollout/deployment-before-drain.yaml.txt) và [receipt sửa](evidence/drift-api/rollout/repair.json) được giữ. Hook drain được cài vào hai release trước khi chạy lại observer; lần bootstrap này là bước chuẩn bị, không nằm trong kết quả205mẫu.

![Thông báo atomic rollback gốc của Helm](evidence/drift-api/screenshots/helm-atomic-console.jpg)

![JSON helm history gốc: drift failed rồi deployed rollback](evidence/drift-api/screenshots/helm-drift-atomic-history.jpg)

![JSON helm history gốc: feature failed rồi deployed rollback](evidence/drift-api/screenshots/helm-feature-atomic-history.jpg)

Ảnh chụp khi mở log/JSON gốc, không chỉnh pixel. [Log drift](evidence/drift-api/rollout/drift/bad-image/helm-console.txt), [lịch sử drift](evidence/drift-api/rollout/drift/bad-image-history.json), [lịch sử feature](evidence/drift-api/rollout/feature/bad-image-history.json) và toàn bộ mẫu theo thời gian được gắn SHA trong [manifest native](evidence/drift-api/capture/manifest.json)/[mapping](evidence/drift-api/capture/evidence-map.json).

## Tự chạy lại

Trên VM có cluster, image đã import và runtime inputs riêng; bản clone Git chỉ có code/config mẫu. Đặt `KUBECONFIG` trỏ tới kubeconfig có quyền phù hợp và dùng binary Helm3 đã kiểm. Chuẩn bị reference/seed bằng các CLI trong [drift guide](drift-detection.md) và [feature guide](feature-api.md) trước khi deploy. Với reference lớn, tạo ConfigMap mới trực tiếp bằng `kubectl create configmap ... --from-file=reference.json=...`; lượt client-side apply ban đầu đã chạm giới hạn annotation262.144byte vì lặp dữ liệu trong last-applied. [Lỗi gốc](evidence/drift-api/helm-initial/reference-configmap.txt) và [cách tạo immutable ConfigMap](evidence/drift-api/helm/repair.json) được giữ.

```bash
python scripts/deploy-api.py --api drift --helm "$HELM" \
  --revision demo-drift --output outputs/helm-new-drift
python scripts/deploy-api.py --api feature --helm "$HELM" \
  --revision demo-feature --output outputs/helm-new-feature
```

Để kiểm chứng có kiểm soát, [observer CLI](../scripts/verify-api-rollout.py) chủ động tạo một revision dùng image không tồn tại rồi kiểm tự rollback. Chọn output/run ID mới, chạy trong cửa sổ lease còn hiệu lực của feature API và kiểm endpoint trước:

```bash
python scripts/verify-api-rollout.py --api drift --helm "$HELM" \
  --reference data/drift/reference-27/reference.json \
  --run rollback-new-drift --output outputs/rollback-new-drift
```

HPA có trong chart nhưng hiện tắt ở hai release của lượt này. Chưa dùng YAML HPA làm bằng chứng autoscale. CI/CD, gateway và telemetry vẫn là các phần triển khai tiếp theo.
