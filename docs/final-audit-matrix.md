# Ma trận đối chiếu57dòng final

Bảng này dẫn từ từng yêu cầu gốc tới mã và proof đã đọc. [Tracker đầy đủ](final-rubric-tracker.md) giữ nguyên sáu cột nguồn và deliverable; [kết luận/giới hạn rà soát](final-audit.md) giải thích phạm vi. Dòng có proof không tự quy đổi thành điểm. A2 được đối chiếu ở README/diagram, docstrings và repository audit.

| Dòng | Yêu cầu | Điểm vào mã/thiết kế | Proof để mở | Bộ kiểm liên quan |
|---|---|---|---|---|
| F03 | Có sử dụng FastAPI + data validation (với pydantic) + healthcheck for k8s | [app.py](../services/feature_api/app.py) | [Feature API native](feature-api.md); [Helm rolling/fallback](api-deployment.md) | native-serving |
| F04 | Sử dụng async | [app.py](../services/feature_api/app.py) | [Async feature API](../services/feature_api/app.py); [Helm rolling/atomic](api-deployment.md) | native-serving |
| F05 | Deploy to k8s with helm + rollingupdate + auto fallback (see --atomic) | [app.py](../services/feature_api/app.py) | [Helm feature API](api-deployment.md) | native-serving |
| F06 | Có sử dụng FastAPI + data validation (với pydantic) + healthcheck for k8s | [app.py](../services/drift_api/app.py) | [Drift API native](drift-detection.md); [Helm rolling/fallback](api-deployment.md) | drift-api capsule / drift-eventing |
| F07 | Sử dụng async | [app.py](../services/drift_api/app.py) | [Async drift API](../services/drift_api/app.py) | drift-api capsule / drift-eventing |
| F08 | Deploy to k8s with helm + rollingupdate + auto fallback (see --atomic) | [app.py](../services/drift_api/app.py) | [Helm drift](api-deployment.md) | drift-api capsule / drift-eventing |
| F09 | Web API kéo dữ liệu | [hpa.yaml](../infra/charts/iot-api/templates/hpa.yaml) | [Native CPU HPA1→2→1](api-autoscaling.md) | api-autoscale capsule |
| F10 | Web API cho drift detection | [hpa.yaml](../infra/charts/iot-api/templates/hpa.yaml) | [Native CPU HPA1→2→1](api-autoscaling.md) | api-autoscale capsule |
| F11 | Unit test với Test Coverage > 90% | [test-api-observability.py](../scripts/test-api-observability.py) | [Native serving8](ci-serving.md); [API drift không đổi](drift-detection.md) | native-serving / ci-api-observability |
| F12 | Có sử dụng kỹ thuật equivalence partitioning vs boundary value analysis khi thiết kế test cases để parametrize. | [run-focused-api-validation.py](../scripts/run-focused-api-validation.py) | [Native boundary/partition](api-validation.md) | focused-api |
| F13 | Có sử dụng mutation testing để đánh giá hiệu quả của các test (cái này đã dạy ở lớp rồi nha cả nhà, là cái https://mutmut.readthedoc… | [test-api-observability.py](../scripts/test-api-observability.py) | [Mutation native hoàn chỉnh5/source914d494](ci-serving.md); [Drift API source không đổi](drift-detection.md) | native-serving / ci-api-observability |
| F14 | Idempotency testing sử dụng property-based testing (chuyên để tìm ra cái test case có thể tạo ra bug), ví dụ, để kiểm tra xem model … | [run-focused-api-validation.py](../scripts/run-focused-api-validation.py) | [Native Hypothesis](api-validation.md) | focused-api |
| F15 | Load test the Web API (cái Web API kéo dữ liệu) to understand throughput (req/s) and latency | [feature_api.py](../tests/load/feature_api.py) | [Locust native8](ci-serving.md); [Locust overlay2f](observed-api-load.md) | observed-api-load |
| F16 | Simulate data drift | [drift.py](../generator/drift.py) | [SensorDrift nhỏ](drift-detection.md) | drift-api capsule |
| F17 | Using generator configuration | [drift.py](../generator/drift.py) | [Config JSON](../generator/configs/sensor-drift-small.json) | drift-api capsule |
| F18 | Tạo bảng label có 2 cột id và label. Bảng này sẽ dùng để join với các bảng feature cho training.  | [drift.py](../generator/drift.py) | [Native nhỏ](drift-detection.md); [Cohort training](training-notebook.md) | drift-api capsule |
| F19 | Build data pipeline (ví dụ Airflow) để incremental materialize dữ liệu mới nhất từ offline qua online store | [batch.py](../feature_store/batch.py) | [Materialization/TTL](feature-jobs-and-ttl.md); [UI Airflow gốc](final-handover.md#ba-pipeline-feature) | final-handover / ci-materialization capsule |
| F20 | Deploy job chịu trách nhiệm push streaming feature (ở mini-coursework) vào OFFLINE store | [staged_push.py](../feature_store/staged_push.py) | [OfflineCI3](ci-stream-push.md); [UI gốc](final-handover.md#ba-pipeline-feature) | window-cohort / ci-stream-push |
| F21 | Deploy job chịu trách nhiệm push streaming feature (ở mini-coursework) vào ONLINE store | [staged_push.py](../feature_store/staged_push.py) | [OnlineCI2](ci-stream-push.md); [UI gốc](final-handover.md#ba-pipeline-feature) | window-cohort / ci-stream-push |
| F22 | Define TTL cho từng bảng feature, giải thích tại sao chọn TTL như thế | [repository.py](../feature_store/repository.py) | [TTL từng view/store/lease](feature-jobs-and-ttl.md) | final-handover / feature-jobs-and-ttl.md |
| F23 | Jupyter notebook để kéo dữ liệu từ offline store (thông qua Feast) để tạo ra model (dùng ML/DL). | [01_failure_training.ipynb](../notebooks/01_failure_training.ipynb) | [Notebook5bước đã execute](training-notebook.md) | notebook / notebook-parity capsules |
| F24 | Chuyển đổi code từ Jupyter notebook thành training pipeline với số lượng step giống ở phần notebook bên trên (sử dụng Kubeflow pipel… | [kfp_notebook.py](../ml/kfp_notebook.py) | [Notebook5ML/6ô PASS](training-notebook.md); [KFP5task PASS](distributed-training.md) | distributed-training capsule / ci-training |
| F25 | Trong training pipeline, ở step train, thay bằng distributed training, ví dụ nếu mọi người dùng model XGBoost thì có thể coi tutoria… | [kfp_notebook.py](../ml/kfp_notebook.py) | [NativeSparkKubernetes](distributed-training.md) | distributed-training capsule / ci-training |
| F26 | Khi training pipeline hoàn tất, thông tin của MODEL (weight, hyperparam) cần được lưu trữ lại ở Model Registry (qua MLFlow) | [run-versioned-training.py](../scripts/run-versioned-training.py) | [Tự động model4–5](automatic-ml-versioning.md) | automatic-versioning capsule / ci-training |
| F27 | Mỗi lần kéo dữ liệu từ Feast về để training, cần version lại DATA theo cơ chế incremental (ví dụ lần train thứ 2 data chỉ có 1 chút … | [snapshot.py](../ml/snapshot.py) | [Hai freshpulls tự động](automatic-ml-versioning.md); [59→60ngày](incremental-data-versioning.md) | data-versions / automatic-versioning capsules |
| F28 | Materialize Pipeline | [ci-materialization.py](../scripts/ci-materialization.py) | [Jenkins materialization](ci-materialization.md) | ci-materialization capsule |
| F29 | Training Pipeline | [ci-training.py](../scripts/ci-training.py) | [Jenkins build2 SUCCESS](ci-training.md) | ci-training |
| F30 | DP 1 (xem ở sheet mini-coursework) | [ci-data-pipelines.py](../scripts/ci-data-pipelines.py) | [Jenkinsbuild2SUCCESS](ci-data-pipelines.md) | ci-data-pipelines capsule |
| F31 | DP 2 (xem ở sheet mini-coursework) | [ci-data-pipelines.py](../scripts/ci-data-pipelines.py) | [Jenkinsbuild2SUCCESS](ci-data-pipelines.md) | ci-data-pipelines capsule |
| F32 | DP 3 (xem ở sheet mini-coursework) | [ci-data-pipelines.py](../scripts/ci-data-pipelines.py) | [Jenkinsbuild2SUCCESS](ci-data-pipelines.md) | ci-data-pipelines capsule |
| F33 | Web API (với FastAPI) | [ci-serving.py](../scripts/ci-serving.py) | [Jenkins serving8 SUCCESS307949ms](ci-serving.md); [Refresh vận hành riêng](active-feature-refresh.md) | native-serving / ci-api-observability |
| F34 | Inference Engine (ví dụ KServe) | [ci-serving.py](../scripts/ci-serving.py) | [Serving8 KServe native](ci-serving.md); [TelemetryCI2](ci-api-observability.md) | native-serving / ci-api-observability |
| F35 | Cho real-time drift detection Web API (sử dụng KNative Eventing kết hợp với KServe) | [ci-drift-eventing.py](../scripts/ci-drift-eventing.py) | [Jenkins drift Eventing3](drift-eventing.md) | drift-eventing |
| F36 | Job 1: Push stream feature to OFFLINE store | [ci-stream-push.py](../scripts/ci-stream-push.py) | [CI stream offline](ci-stream-push.md) | ci-stream-push |
| F37 | Job 2: Push stream feature to ONLINE store | [ci-stream-push.py](../scripts/ci-stream-push.py) | [CI stream online](ci-stream-push.md) | ci-stream-push |
| F38 | Service để coi metric (ví dụ Grafana) | [deploy-private-gateway.py](../scripts/deploy-private-gateway.py) | [Gateway private native](private-gateway.md) | private-gateway |
| F39 | Service để coi log (ví dụ Kibana) | [deploy-private-gateway.py](../scripts/deploy-private-gateway.py) | [Gateway private native](private-gateway.md) | private-gateway |
| F40 | Service để coi trace (ví dụ Jaeger) | [deploy-private-gateway.py](../scripts/deploy-private-gateway.py) | [Gateway private native](private-gateway.md) | private-gateway |
| F41 | Web API kéo dữ liệu | [deploy-private-gateway.py](../scripts/deploy-private-gateway.py) | [Gateway private native](private-gateway.md) | private-gateway |
| F42 | Setup authentication & rate limit cho Web API kéo dữ liệu | [deploy-private-gateway.py](../scripts/deploy-private-gateway.py) | [Gateway private native](private-gateway.md) | private-gateway |
| F43 | Làm cái này cho Web API kéo dữ liệu. Mọi người có thể tham khảo 2 đồ án sau: cái này và cái này | [deploy-private-gateway.py](../scripts/deploy-private-gateway.py) | [Gateway private native](private-gateway.md) | private-gateway |
| F44 | Dùng Terraform để setup GKE hoặc các cloud services (để ý cách chia folder theo từng service nếu có, ví dụ như sau) | [main.tf](../infra/gcp/vm/main.tf) | [IaC verified](iac-verification.md) | iac-final |
| F45 | Dùng Ansible để configure và deploy các service lên VM  | [bootstrap.yml](../infra/ansible/bootstrap.yml) | [Ansible roles verified](iac-verification.md) | iac-final |
| F46 | Web API monitoring metrics such as req/s, num. of reqs, num. of failures, etc. | [ci-observability.py](../scripts/ci-observability.py) | [API metrics native](api-observability.md); [Locust wrapper](observed-api-load.md) | api-observability |
| F47 | Collect và visualize metrics với Prometheus + Grafana (hoặc tool tương tự) | [host_monitoring.py](../ml/host_monitoring.py) | [Host metrics PC + Linux VM](host-monitoring.md) | host-monitoring / linux-host |
| F48 | Tương tự cho logs | [log_discovery.py](../ml/log_discovery.py) | [Log VM thực](log-monitoring.md); [Dynamic Pod discovery](log-discovery.md) | linux-logs / log-discovery |
| F49 | Tương tự cho traces | [ci-observability.py](../scripts/ci-observability.py) | [Jaeger2.22 native](api-observability.md) | api-observability |
| F50 | Airflow data drift pipeline to periodically pull data from offline feature store and compare with groundtruth and update to Grafana … | [verify-offline-drift.py](../scripts/verify-offline-drift.py) | [Native Airflow→Feast→Pushgateway→Grafana](offline-drift-monitoring.md) | offline-drift |
| F51 | Trigger retrain by calling Kubeflow API (you can design by adding one step at the end of the Airflow data drift pipeline) | [verify-drift-retraining.py](../scripts/verify-drift-retraining.py) | [Airflow→KFP native](drift-retraining.md) | drift-retraining |
| F52 | Perform A/B traffic split for 2 versions of inference service (deploy via CI/CD) | [ci-serving.py](../scripts/ci-serving.py) | [A/B native qua servingCI8](ci-ab-deployment.md) | native-serving / ab-monitoring |
| F53 | Setup monitoring dashboard to monitor 2 versions. | [ab_monitoring.py](../ml/ab_monitoring.py) | [Dashboard nativeVM](ab-monitoring.md) | ab-monitoring |
| F54 | Centralize secret management via Hashi Corp Vault (or similar tools) so it will be used across the whole organization. | [vault_client.py](../infra/security/vault_client.py) | [Vault native2.2.1/Raft/TLS](vault.md) | vault / resume-demo-services live |
| F55 | Using service mesh to authorize access from service to service | [install-private-mesh.py](../scripts/install-private-mesh.py) | [Istio1.31.3 native](private-mesh.md) | private-mesh |
| F56 | Clean Code + clean repo + demonstrate the use of Design Pattern | [runtime.py](../services/feature_api/runtime.py) | [Pattern/source](ml-class-design.md); [repoaudit](repository-quality.md) | final-handover / repository-quality.md |
| F57 | Propose 5 key classes to present our main focus: | [ml-class-design.md](../docs/ml-class-design.md) | [Năm lớp đã có](ml-class-design.md) | 5classes/design pattern document |
| F58 | Idea 1 | [joint_drift.py](../ml/joint_drift.py) | [MMD controlled](joint-drift.md); [nativecohort](joint-cohort.md) | joint-cohort |
| F59 | Idea 2 | [check-deployment-policy.py](../scripts/check-deployment-policy.py) | [OPA native policy](deployment-policy.md); [Learner confirmation](evidence/course-scope/learner-confirmation.json) | deployment-policy |

`verify-NAME.py` là bộ kiểm offline nếu NAME thuộc whitelist của `scripts/audit-final-coursework.py`. “Capsule” là nhóm payload native được kiểm toàn bộ byte/hash/map; phép thử runtime gốc và output được giữ trong tài liệu liên kết. Những CLI rollout/autoscale/materialization có thao tác deploy không được tự chạy bởi audit offline.

[Cách tự demo và trả lời câu hỏi](demo-walkthrough.md) tách việc đọc proof với việc thực hiện request live.
