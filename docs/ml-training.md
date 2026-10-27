# Bốn bước ML với protocol đã đóng băng

Pipeline [KFP](../ml/kfp_training.py) tách việc lấy dữ liệu, chia tập, huấn luyện và đánh giá thành bốn container có phụ thuộc rõ. Mục đích là truy được một dự đoán về model, tập feature và nguồn đã tạo ra nó.

## Quy tắc không đổi sau khi nhìn metric

[Protocol60ngày](ml-evaluation-protocol.md) đã commit trước lượt huấn luyện mới, SHA `e0bfcea57331b88bb6914f9342a9477eab6687f6a3929b8cb9834251fa9b8a96`. File protocol giữ nguyên bytes; trường `new_model_metrics_observed:false` mô tả thời điểm **khai báo**, không phải trạng thái cập nhật sau từng run.

- Chia theo event time: train42ngày, validation9ngày, test9ngày; purge khi nhãn chưa có trước ranh giới tiếp theo.
- Chỉ tám feature trong allowlist; không dùng label, episode ID, trạng thái vật lý hoặc thông tin sự cố tương lai làm đầu vào.
- Scaler và trọng số class fit trên train. Spark logistic regression `maxIter=40`, `regParam=0.1`.
- Chọn một ngưỡng từ0,1đến0,9 bằng validation: F1 cao nhất, rồi precision, rồi ngưỡng cao hơn. Sealed selection/model phải tồn tại trước khi mở các hàng test.
- Đánh giá test một lần trong workspace mới. Không sửa split/hyperparameter/ngưỡng theo kết quả test. Đối chứng dùng prevalence của train, với cùng ngưỡng đã chốt.

## Bốn thành phần

| Bước | Xử lý | Điều kiện chặn lỗi |
|---|---|---|
| `load-feast` | Native Feast service lấy lại toàn cohort đủ điều kiện vào60dayparts mới | Source/protocol/report/marker/hash phải khớp; không dùng lại cached dayparts của pipeline cũ |
| `split-time` | Spark tự suy lại split/purge, kiểm uniqueness/null/finite/class/episode rồi ghi ba tập | Split hoặc label availability sai ranh giới, duplicate, thiếu class/episode đều bị chặn |
| `train-model` | Mở train/validation, fit scaler/weights/LR, chọn ngưỡng validation, lưu native model và hệ số portable | Không mở Parquet test trong component này; descriptor/model/file inventory được seal |
| `evaluate-model` | Kiểm selection còn nguyên, tạo marker mở holdout, đánh giá test và đối chứng | Từ chối chạy lại vào output đã có; portable probability phải khớp Spark trên toàn holdout trong sai số1e-12 |

[Hợp đồng](../ml/training_contract.py), [load](../ml/training_load.py), [Spark job](../ml/training_job.py), [launcher](../scripts/run-kfp-local.py). Descriptor là JSON nhỏ trỏ tới dữ liệu và kèm inventory SHA từng file; dữ liệu Parquet/model binary nằm ngoài Git.

PR-AUC dùng `Spark3.5.9 BinaryClassificationEvaluator`, `metricName=areaUnderPR`, `numBins=0`: tích phân hình thang trên đường PR không chia bin. Không gọi số này là average precision. Confusion counts tính bằng Spark aggregate, không thu toàn bộ hàng test về driver.

`risk_score` nằm trong0..1 nhưng model dùng class weights và chưa được calibration. Không diễn giải số này thành phần trăm xác suất sự cố thực của thiết bị. Dữ liệu mô phỏng, cùng máy xuất hiện qua các giai đoạn thời gian và các snapshot gần nhau chia sẻ cùng sự cố; metric không chứng minh khả năng vận hành trên máy bơm thật.

## Kiểm tra biết đáp án trước cohort thật

[Oracle](../ml/training_oracles.py) tạo fixture tuyến tính360hàng, mỗi split120hàng/60positive/60negative. [Bốn kiểm tra thực Spark](evidence/ml-training/metadata-repair/oracle-verification.json) đã qua: label available đúng tại cutoff bị loại; confusion test đúngTP60/TN60/FP0/FN0; scaler có feature constant vẫn tạo portable probability khớp; threshold được seal trước test. Metric fixture chỉ kiểm thuật toán, không phải chất lượng dự đoán IoT.

145 unit tests qua trên [PC](evidence/ml-training/pc-unit-v4/unit-tests.json) và [VM](evidence/ml-training/parent-permissions/unit-tests-vm.txt), gồm kiểm tra biên thời gian, tie break, sigmoid/zero standard deviation, không phát hành artifact chưa hoàn tất và phạm vi chia quyền thư mục. Không dùng số test thay cho API coverage hoặc mutation score.

Lượt oracle đầu bị lỗi import `pandas` gián tiếp qua helper `pyspark.ml.functions`; sửa bằng scalar probability UDF, giữ nguyên image Spark đang chạy nguồn100GB. Lượt KFP local đầu bị lỗi quyền mở `data/feast/write.lock`, chưa huấn luyện. Launcher sửa để Feast giữ USER10001 và Spark giữ USER185 của image. Chỉ workspace/artifact/scratch mới, chứa dữ liệu mô phỏng không có secrets, được chia quyền ghi; không đổi quyền lock hay toàn repo. Giữ console/snapshot của các lượt lỗi.

Lượt local tiếp theo lấy xong3.132.184hàng qua Feast, rồi [lỗi](evidence/ml-training/initial-kfp-v2/controller-console.txt) vì chỉ ghi file descriptor mà chưa phát hành metadata `ExecutorOutput` của typed artifact. Không có bước train ở lượt này. SDK2.17.3 cũng tự dọn `workspace_root` sau pipeline, kể cả khi thất bại; bản copy native vừa lấy trong workspace đó đã bị dọn, trong khi nguồn/registry và bộ Feast đã kiểm trước vẫn còn nguyên. [Ghi nhận cleanup](evidence/ml-training/metadata-repair/previous-run.json) giữ đúng phạm vi mất bản copy này.

Launcher mới đặt dữ liệu cần giữ ở mount riêng `/ml-data`, chỉ cho SDK dọn thư mục tạm không chứa dataset, và đối chiếu lại inventory sau khi pipeline kết thúc. [Publisher metadata](../ml/kfp_artifacts.py) chỉ xuất typed artifact sau descriptor success. [Smoke hai task](../scripts/verify-kfp-artifacts.py) đã [PASS](evidence/ml-training/metadata-repair/typed-transport/verification.json): task sau nhận đúng giá trị7 qua IOStore của KFP. Không dùng đường dẫn truyền ngoài framework hoặc sửa SDK; không coi file đơn lẻ/log bước load là thành công toàn pipeline.

Lượt V3 sau đó giữ được dataset và ghi đủ ba tập nhưng lỗi lưu descriptor: thư mục cha pipeline do Feast tạo với quyền755 khiến Spark UID khác không tạo được thư mục task kế tiếp. [Log](evidence/ml-training/initial-kfp-v3/controller-console.txt), [ghi nhận dữ liệu vẫn còn](evidence/ml-training/parent-permissions/previous-run.json). Writer nay chỉ chia quyền777 cho **thư mục artifact mới** nằm dưới root được launcher khai báo; workspace thường và lock đang có không đổi quyền. [Hai task dùng đúng hai image khác nhau](evidence/ml-training/parent-permissions/typed-transport/verification.json) đã qua, output ghi UID thực10001→185. Đây là kiểm tra quyền giữa các component; oracle thuật toán chạy bằng root không thể tự chứng minh điều này.

## Runtime và phần còn thiếu

Lượt `training-medium-27-v4` đã **PASS cả bốn task** lúc09:00:22UTC ngày27/10/2026: [verification](evidence/ml-training/full-cohort/controller/verification.json), [console thật](evidence/ml-training/full-cohort/controller/console.txt). Native Feast lấy3.132.184hàng mới/60dayparts; [marker](evidence/ml-training/full-cohort/dataset/_LOADED_SUCCESS.json) và [load report](evidence/ml-training/full-cohort/native-load/report.json). Pipeline kéo dài925,888giây khi VM còn chạy Spark100GB song song; đây không phải benchmark ML độc lập. Mỗi container được quan sát đúng2CPU/10GiB, một Feast và ba Spark; USER mặc định được giữ.

| Tập | Số hàng | Positive | Sự cố riêng |
|---|---:|---:|---:|
| Train | 2.195.668 | 93.603 | 7.870 |
| Validation | 468.132 | 19.828 | 1.677 |
| Test | 468.384 | 19.957 | 1.687 |

Scaler/weights fit trên train; logistic regression hội tụ sau10iterations trong giới hạn40. [Selection](evidence/ml-training/full-cohort/task-results/train-model/selection.json) chốt ngưỡng **0,7** từ validation trước [marker mở holdout](evidence/ml-training/full-cohort/evaluation/holdout-opened.json). Kiểm hash đọc byte của cả ba tập để bảo vệ tính toàn vẹn; không giải mã hàng test để fit hoặc chọn ngưỡng.

| Metric | Validation | Test |
|---|---:|---:|
| Precision | 0,4558 | 0,4743 |
| Recall | 0,6429 | 0,6572 |
| F1 | 0,5334 | 0,5510 |
| PR-AUC Spark, không chia bin | 0,5958 | 0,6151 |

[Evaluation đầy đủ](evidence/ml-training/full-cohort/task-results/evaluate-model/evaluation.json): testTP13.115/FP14.536/FN6.842/TN433.891. Đối chứng constant bằng prevalence train có testPR-AUC0,04261/F1=0 ở cùng ngưỡng. Baseline còn nhiều false alarms; không tự đổi threshold/split sau khi nhìn test. Test48h cũ không có positive vẫn được giữ, không coi là số đo chất lượng để so cải thiện trực tiếp.

[Hệ số portable](evidence/ml-training/full-cohort/model/portable.json) tái tạo native Spark probability trên **toàn bộ468.384hàng test**, sai số tuyệt đối lớn nhất **4,44e-16**, dưới gate1e-12. Model phục vụ có thể dùng phép tính này mà không cần JVM; phép so sánh model tải lại từ MLflow sẽ được kiểm riêng. Controller kiểm lại inventory dữ liệu/model sau cleanup; dữ liệu thật vẫn ở `data/ml-workspaces/training-medium-27-v4/training/`, không đưa Parquet/binary model lên Git.

SDK `KFP2.17.3`, Docker SDK7.1.0, Python3.12.3 cài riêng trong `data/runtime/kfp-venv`. Launcher dùng KFP `DockerRunner`, source mount read-only, internal network Feast, không mount Docker socket vào component. DSL CPU/memory settings bị local runner bỏ qua và có warning; giới hạn thực áp qua DockerRunner là2CPU/10GiB, được observer kiểm từ container. Spark heap6GiB/local[2]; source100GB dùng image/process/scratch riêng và19file codefence không thay đổi.

Đây là kiểm chứng **KFP local**. YAML compile và chạy DockerRunner không thay cho cluster Kubeflow hoặc ảnh pipeline thành công trên KFP UI. M2-13 còn đang làm tới khi có bằng chứng triển khai/chạy trên cluster. Không cài K3s hoặc đổi host networking khi full Spark còn hoạt động.

MLflow3.17.2 có image riêng, NumPy2.5.4/PyArrow25/pandas2.3.3, không thay FeastArrow26 hoặc SparkNumPy1.26. [Compose](../infra/mlflow.compose.yaml) dùng SQLite backend, artifact persistence và cổng127.0.0.1:15000. [Image/server health](evidence/ml-training/mlflow-image/verification.json) đã qua; việc đăng ký model/data và đọc lại phải có report riêng mới được coi là kiểm chứng. [Model portable](../ml/portable_model.py) và [registration](../ml/register_model.py) chuẩn bị alias `latest` sau readback; không tự gắn production.

## Tự chạy

Trên Linux VM có dữ liệu/registry/image thật của [Feast](feast-feature-store.md), từ repo root. Clone Git không mang theo dữ liệu hay dịch vụ. Với SDK đã cài theo lock, có thể compile để xem DAG; compile không chạy training:

```bash
data/runtime/kfp-venv/bin/python -m ml.kfp_training --output outputs/training-pipeline-new.yaml
```

Chạy oracle vào một thư mục mới trước, dùng image Spark đã kiểm:

```bash
mkdir -p outputs/ml-oracle-practice
docker run --rm --cpus 2 --memory 10g --user 0:0 \
  --entrypoint /opt/spark/bin/spark-submit \
  --mount type=bind,source="$PWD/ml",target=/workspace/ml,readonly \
  --mount type=bind,source="$PWD/outputs/ml-oracle-practice",target=/proof \
  iot-coursework-spark:3.5.9 --master 'local[2]' --driver-memory 6g \
  /workspace/ml/training_oracles.py \
  --protocol /workspace/ml/protocols/medium-60d-v1.json \
  --workspace /proof/known-answers --output /proof/oracle-verification.json
```

Lượt thật cần90phút còn lại, RAM khả dụng24GiB, SSD40GB/HDD30GB trước admission. Thay deadline bằng thời điểm autoSTOP thực, chọn tên mới và proof oracle đúng code SHA:

```bash
sudo data/runtime/kfp-venv/bin/python scripts/run-kfp-local.py \
  --run training-practice-1 --deadline-utc '<actual-stop-time-UTC>' \
  --oracles outputs/ml-oracle-practice/oracle-verification.json \
  --ingest-fence outputs/scale-ingest-100gb-27/preflight.json
cat outputs/kfp-local/training-practice-1/verification.json
```

`--ingest-fence` dùng nếu run full Spark tương ứng còn hoạt động. Launcher không hỗ trợ resume một workspace dở dang; giữ lượt lỗi, sửa nguyên nhân và chọn run mới. Mỗi component phải có descriptor success, đồng thời observer phải thấy đủ bốn container đúng image/limits, mới có verification cuối.

Tham khảo chính thức: [KFP local execution](https://www.kubeflow.org/docs/components/pipelines/user-guides/core-functions/execute-kfp-pipelines-locally/), [container components](https://www.kubeflow.org/docs/components/pipelines/user-guides/components/container-components/), [MLflow registry workflow](https://mlflow.org/docs/latest/ml/model-registry/workflow/), [Python model](https://mlflow.org/docs/latest/api_reference/python_api/mlflow.pyfunc.html). Số đo kết quả thật cần đọc từ bằng chứng của từng run, không suy ra từ các hướng dẫn này.
