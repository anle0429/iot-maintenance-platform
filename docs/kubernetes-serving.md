# KServe trên Kubernetes của VM

KServe quản lý vòng đời pod dự đoán, kiểm tra readiness và tạo dịch vụ nội bộ. Pod dùng **image chứa sẵn model**, nhận tám feature và trả điểm rủi ro cùng thông tin truy vết. Nguồn → feature → MLflow run/version → model SHA → prediction dùng cùng các binding trong [training](ml-training.md) và [predictor](model-serving.md).

## Kết quả thực ngày 27/10/2026

[K3s verification](evidence/kserve/cluster/verification.json) xác nhận một node `iot-cw-vm` Ready, **v1.35.9+k3s1**, trên VM hiện có 8CPU/64GiB. [Ansible playbook](../infra/ansible/kubernetes.yml) có role riêng; lượt áp dụng thứ hai **changed=0, failed=0, unreachable=0**. K3s giữ state/containerd trên SSD; service thực có giới hạn 2CPU/8GiB. Đây là giới hạn control plane, không phải tổng giới hạn của mọi pod. Kubeconfig0600, secrets mã hóa; không đưa kubeconfig hoặc token vào Git.

[KServe verification](evidence/kserve/controller/verification.json) **PASS lúc11:21:55UTC**. Helm3.20.0 cài chart CRD và predictive controller **v0.20.0**, cert-manager**v1.21.3**. [Giá trị Helm](../infra/kserve/values.yaml) chọn Standard, tắt ingress creation và local-model controller. Không cần Knative cho cấu hình này. [Tài liệu Standard](https://kserve.github.io/website/docs/admin-guide/configurations) mô tả các khóa cấu hình sử dụng; [release KServe](https://github.com/kserve/kserve/releases/tag/v0.20.0) là nguồn chart đã khóa.

| Kiểm chứng | Quan sát thực |
|---|---|
| InferenceService | `iot-serving/iot-pump-failure-v1`, Ready=True |
| Pod | `iot-pump-failure-v1-predictor-774894bdbb-q82m4`,1/1 Ready,0restart khi chụp |
| Owner | KServe → Deployment → ReplicaSet → Pod; [deployment](evidence/kserve/capture/predictor-deployments.json), [replicaset](evidence/kserve/capture/predictor-replicasets.json) |
| Container | NonrootUID/GID10001; readonly root, drop ALL, không gắn service-account token; request100m/128Mi, limit500m/512Mi |
| Model | MLflowversion1, run`c629f87591294157a6b30040c8c12715`, portableSHA`699eb9d0d831960fe7eb9bc3f8719f88af530881543bd3ee5245d8537c9f2bc9` |
| HTTP | health/live và health/ready qua;32mẫu Spark có cùng quyết định, max sai số điểm`2.220446049250313e-16` |
| Service | ClusterIP, không externalIP/LoadBalancer/NodePort hoặc public ingress |

Docker29 lưu image ID theo OCI manifest **8e9ca402…**; Kubernetes/containerd trả imageID config **57457f83…**. [OCI manifest thật](evidence/kserve/controller/oci-manifest.json) nối hai digest này bằng `config.digest`; chúng không phải hai model khác nhau. Giữ cả hai thay vì sửa receipt cho giống nhau.

Ảnh nguyên bản từ dịch vụ thật, đi qua SSH/IAP:

![Pod có label và owner KServe](evidence/kserve/screenshots/pod-identity.jpg)

![Pod Ready và tài nguyên thực](evidence/kserve/screenshots/pod-ready.jpg)

![Swagger gọi pod KServe trả200 và lineage](evidence/kserve/screenshots/http-prediction.jpg)

38file nhỏ đã đối chiếu SHA trong [capture manifest](evidence/kserve/capture/manifest.json) và [mapping](evidence/kserve/capture/evidence-map.json); archive SHA`462dd4aa7dcd470f40109e2efead846fc61df9049ed9f0df16d18de0a84fdcee`. [Hash ảnh](evidence/kserve/screenshots/capture.json) gắn với ba JPEG không chỉnh sửa. Không commit image tar, dataset, model Spark lớn hoặc thông tin cloud riêng.

## Tự kiểm tra và chạy lại

Chạy trên VM đã được cấp quyền và có các đĩa mount đúng. [Role Kubernetes](../infra/ansible/roles/kubernetes/tasks/main.yml) yêu cầu vendor assets đã kiểm checksum, đủ RAM/SSD và full-source verification đã hoàn tất; không bỏ guard. Project ID truyền bằng biến cục bộ `expected_project_id`; không lưu giá trị thật trong repo. Phiên VM vẫn tự STOP sau8giờ như đã duyệt.

```bash
ansible-playbook -i localhost, infra/ansible/kubernetes.yml --extra-vars=@/path/to/private-target-vars.json
sudo k3s kubectl get nodes
sudo k3s kubectl get inferenceservice,pods,svc -n iot-serving
```

Với cluster mới, dùng đúng bản cert-manager/chart trong [vendor receipts](evidence/kserve/assets/helm/verification.json), kiểm checksum trước khi áp dụng. Đặt `KUBECONFIG=/etc/rancher/k3s/k3s.yaml` cho Helm, cài cert-manager rồi chờ ba deployment Ready. Cài chart `kserve-crd` trước `kserve-resources`, namespace`kserve`, truyền `-f infra/kserve/values.yaml`. Giữ tên release/namespace hiện có khi tái lập; không chạy `helm install` trùng lên release đang tồn tại.

Image predictor được build theo [hướng dẫn predictor](model-serving.md), sau đó import vào containerd của K3s. Docker và K3s có kho image riêng:

```bash
docker save -o /srv/iot-fast/predictor-import.tar iot-coursework-predictor:v1
sudo k3s ctr images import /srv/iot-fast/predictor-import.tar
sudo k3s kubectl apply -f infra/kserve/pump-failure.yaml
sudo k3s kubectl wait --for=condition=Ready inferenceservice/iot-pump-failure-v1 -n iot-serving --timeout=360s
sudo k3s kubectl port-forward -n iot-serving svc/iot-pump-failure-v1-predictor --address=127.0.0.1 18080:80
```

Từ PC/laptop, mở SSH/IAP forward cổng18880 tới127.0.0.1:18080 của VM, rồi vào `http://127.0.0.1:18880/docs`. Chọn POST prediction, model_name`iot-pump-failure-v1`, dùng `features` của một mẫu trong [fixture32mẫu](../tests/fixtures/predictor-native-spark.json) làm phần tử `instances`. Đối chiếu score và prediction với expected của mẫu đó. Không gửi nhãn hoặc toàn bộ object fixture làm feature.

Ảnh pod dùng proxy Kubernetes chỉ đọc, chỉ chấp nhận đường dẫn pod/InferenceService của namespace này; [kiểm thử boundary](evidence/kserve/capture/proxy-boundaries.json) cho thấy đọc pod200 nhưng đọc secrets và POST bị chặn. Proxy chỉ bind loopback; firewall cloud vẫn chỉ cho IAP SSH. API K3s bind0.0.0.0 để embedded agent dùng được loopback, còn advertise/nodeIP ở VPC private; không gọi đây là API chỉ bind loopback. Đừng mở cổng6443 ra Internet để xem pod.

## Giới hạn và phần tiếp theo

Đây là model từ cohort mô phỏng60ngày, điểm logistic có class weight chưa hiệu chuẩn; metadata vẫn `production=false`.32mẫu là đối chiếu tích hợp, không phải đánh giá holdout mới. `minReplicas=1/maxReplicas=2` là cấu hình HPA, chưa chứng minh scale dưới tải hoặc rollout/rollback. API hiện nhận feature tường minh; API kéo Feast, chặn dữ liệu cũ và lấy trạng thái vận hành còn phải làm. Những phần đó không được tính hoàn thành từ pod KServe này.
