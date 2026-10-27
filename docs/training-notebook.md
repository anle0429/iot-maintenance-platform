# Notebook: từ feature đến model đã lưu

Cập nhật27/10: [Notebook và KFP đã tự động version dữ liệu/đăng ký model4–5](automatic-ml-versioning.md), hai full run đã kiểm chứng. Các lượt lịch sử bên dưới giữ nguyên nguồn/bằng chứng riêng.

Cập nhật 27/10: [Notebook nguồn](../notebooks/01_failure_training.ipynb) và [pipeline Kubeflow](distributed-training.md) đều có **năm bước ML**. Lượt Notebook mới **18:35:03–18:47:57 UTC** chạy **6 ô code, 0 lỗi**: một ô khởi tạo và năm ô ML. [Receipt Jupyter](evidence/notebook-parity/execution/execution.json), [notebook đã execute](evidence/notebook-parity/execution/executed.ipynb) và [HTML nguyên bản](evidence/notebook-parity/execution/executed.html) lưu output thật trên VM 8 CPU/64 GiB.

| Bước ML | Notebook mới | Pipeline KFP đã kiểm |
|---|---|---|
| 1. Feast và nhãn | Kéo mới 3.132.184 snapshot, xuất nhãn `id,label`, merge `one_to_one` | Cùng hàm load/merge, một lần kéo mới độc lập |
| 2. Chia tập thời gian | Train 2.195.668 / validation 468.132 / test 468.384 | Cùng kernel split và protocol cố định |
| 3. Train và chọn ngưỡng | Spark `local[2]`, scaler/class weights trên train; validation chọn 0,7 | Cùng kernel ML, hai executor Kubernetes thật |
| 4. Nạp lại model và validation | 200 score portable/Spark lệch tối đa 2,22e-16; chưa mở holdout | Cùng hàm kiểm model đã lưu; task lỗi sẽ chặn bước 5 |
| 5. Tái lập holdout | Ngưỡng giữ0,7, test F1=0,5509578222 / PR-AUC=0,6151480585; toàn 468.384 score lệch tối đa 4,44e-16 | Cùng kernel evaluate, kết quả confusion và metric khớp |

Ô khởi tạo không phải bước ML. [Selection](evidence/notebook-parity/dataset/selection.json), [validation-reload](evidence/notebook-parity/dataset/validation-reload.json) và [evaluation](evidence/notebook-parity/dataset/evaluation.json) liên kết bằng SHA. Runtime chỉ cho mở holdout sau validation thành công, `holdout_rows_opened=0` và hash đúng selection hiện tại. [Test chặn truy cập](../tests/test_notebook_holdout_gate.py) kiểm validation thiếu, thất bại, đã mở holdout hoặc thuộc selection khác đều không khởi động container evaluate.

![Bước thứ năm và output thật, ô In6](evidence/notebook-parity/screenshots/five-stage-holdout-result.jpg)

Đây là **tái lập protocol đã có kết quả lịch sử**, không phải test set mới chưa quan sát. Feature, split, tham số và ngưỡng không được điều chỉnh theo test. Notebook vẫn `local[2]`; bằng chứng train phân tán nằm ở [run KFP 5 task](distributed-training.md). Cả hai dùng cohort trung gian 60 ngày; chưa training trên nguồn 100 GB. Model phục vụ vẫn version 1.

### Admission và bằng chứng nguồn

[Năm container receipts](evidence/notebook-parity/controller/preflight.json) đều exit0, không OOM, mỗi component 2 CPU/10 GiB. [Mười mẫu tài nguyên trước các bước](evidence/notebook-parity/controller/resource-samples.jsonl) ghi RAM khả dụng thấp nhất55.389.175.808byte, đĩa dữ liệu thấp nhất358.162.653.184byte và SSD104.664.289.280byte; đây là **số đo tại admission**, không phải peak RAM/shuffle trong job. Ngưỡng16GiB RAM/30GB mỗi đĩa giữ nguyên, cần hai mẫu đạt cách 2 giây, đợi tối đa 60 giây nếu có áp lực tạm thời; thiếu thời gian thì chặn ngay. Constructor vẫn cần 90 phút trước autoSTOP thật.

Lượt trước [thất bại](evidence/notebook-parity/failed/execution/execution.json) sau Feast load thành công, trước mọi container Spark. Guard cũ chỉ báo thiếu dự phòng chung, không ghi số đo tại điểm lỗi, nên **chưa xác định được đó là RAM, đĩa hay thời gian**. [Probe cùng Jupyter kernel](evidence/notebook-parity/guard-probe/measurements.json) sau đó qua. Thêm telemetry rồi chạy lại toàn bộ ở workspace mới, không sửa receipt lỗi, không ghép output hai lượt và không hạ guard. [Nguồn runtime trước bổ sung telemetry](evidence/notebook-parity/retry/source-before-resource-trace.py.txt) được giữ để đối chiếu.

[Manifest 43 tệp native](evidence/notebook-parity/capture/manifest.json), [mapping](evidence/notebook-parity/capture/evidence-map.json), [verification](evidence/notebook-parity/capture/verification.json) và [binding 9 nguồn](evidence/notebook-parity/capture/source-commit.json) nối output với đúng byte đã chạy tại Git `316358aafc0703fd4914b09f63ec39d57c2c1eef`. VM dùng bản source copy; không suy ra Git HEAD của VM. Bằng chứng bốn bước cũ bên dưới vẫn giữ nguyên, nguồn gốc được ràng buộc riêng.

## Lượt bốn bước ban đầu — giữ lịch sử

Notebook ban đầu có phần giải thích tiếng Việt và bốn bước ML. [Binding nguồn lịch sử](evidence/notebook/capture/source-commit.json), [bản đã execute](evidence/notebook/execution/executed.ipynb), [HTML nguyên bản](evidence/notebook/execution/executed.html) và [receipt Jupyter](evidence/notebook/execution/execution.json) ghi lượt thật trên cùng VM 8 CPU/64 GiB: **12:56:39–13:08:04 UTC ngày 27/10/2026**, cả **5 ô code chạy, 0 ô lỗi**. Một ô khởi tạo và bốn ô thực hiện các bước dưới đây; lượt này chưa có bước holdout thứ năm.

| Bước | Mục đích và kết quả kiểm chứng |
|---|---|
| Feast và merge nhãn | Lấy mới **3.132.184 snapshot/60 ngày**, không dùng lại daypart. Feast chỉ nhận `machine_id,event_timestamp`; tám feature trả về khớp trực tiếp nguồn offline bất biến. Xuất nhãn riêng `id,label`, ghép `one_to_one`, không mất hay tăng dòng. |
| Temporal split | Giữ protocol đã đóng băng: train 2.195.668, validation 468.132, test 468.384. Kiểm timestamp, label availability/purge, uniqueness, null/finite và class/episode support trước fit. |
| Train và chọn ngưỡng | StandardScaler, class weights và LogisticRegression fit trên train. `maxIter=40`, `regParam=0.1`, hội tụ sau 10 iterations. Chỉ validation chọn ngưỡng 0,7. Lưu native Spark model và JSON portable. |
| Nạp lại và kiểm validation | `PipelineModel.load` dự đoán lại toàn validation; confusion counts giữ nguyên. 200 score portable so với model Spark đã nạp lại có sai số lớn nhất **2,22e-16**. Không mở hàng test. |

Mỗi snapshot là một máy tại một thời điểm. Dùng riêng `machine_id` để ghép nhãn sẽ tạo quan hệ nhiều-nhiều và có thể gắn nhãn sai. [Hàm id](../ml/snapshot.py) chuẩn hóa UTC với sáu chữ số phần giây rồi SHA256 chuỗi `machine_id|timestamp`; [known-answer tests](../tests/test_snapshot.py) kiểm múi giờ tương đương và sự khác biệt giữa máy/thời điểm. Bảng ánh xạ id với máy/thời điểm/label-availability/split nằm riêng, không đưa những trường tương lai vào feature.

![Native Feast load và nhãn hai cột trong output notebook](evidence/notebook/screenshots/native-feast-label-merge.jpg)

![Các dòng đã ghép id, label và tám feature](evidence/notebook/screenshots/merged-snapshot-id-label-features.jpg)

[Load report](evidence/notebook/dataset/input/load-report.json) ghi byte hash từng partition nhãn, khóa và cohort đã merge; [ba dòng mẫu thật](evidence/notebook/dataset/input/merge-sample.json). `id` là mã snapshot, không phải ID sự cố. [Readback độc lập](evidence/notebook/label-readback/verification.json) đã mở metadata cả 60 file Parquet: đúng hai cột, 3.132.184 id duy nhất; join toàn cohort không có nhãn thiếu, thừa hoặc khác. [Script tự kiểm](../scripts/verify-training-labels.py) chạy trong container 1 CPU/4 GiB, input readonly, không có mạng. Nhãn unknown/downtime bị loại theo [protocol](ml-evaluation-protocol.md); không biến unknown thành 0.

Validation có TP 12.748 / FP 15.222 / FN 7.080 / TN 433.082; precision 0,4558, recall 0,6429, F1 0,5334 và PR-AUC 0,5958. PR-AUC là tích phân hình thang Spark `areaUnderPR`, `numBins=0`, không phải average precision. [Selection](evidence/notebook/dataset/selection.json) đã seal trước mọi holdout access; [reload verification](evidence/notebook/dataset/validation-reload.json) ghi `holdout_rows_opened=0`.

![Validation sau khi nạp lại model đã lưu](evidence/notebook/screenshots/saved-model-validation.jpg)

Model notebook có portable SHA `deec94452f7bd419c75cc313ce9cc4e84f5a1049f7d147048958a9607c61e0f3`. Dữ liệu/model binary nằm ở `data/ml-workspaces/notebook-medium-27/` trên VM; Git giữ [hệ số nhỏ](evidence/notebook/dataset/trained/portable.json) và inventory. Model này chưa được tự thay vào KServe; dịch vụ vẫn pin version1. Risk score dùng class weighting, chưa calibration, dữ liệu mô phỏng và cùng máy xuất hiện qua các thời đoạn: không diễn giải thành xác suất vận hành thực tế.

## Runtime và tái lập

[NotebookRuntime](../ml/notebook_runtime.py) dùng host kernel Jupyter để gọi Docker CLI; không mount Docker socket vào component. Feast và Spark dùng lại hai image đã kiểm chứng, mỗi component thực tế giới hạn **2 CPU/10 GiB**, giữ USER mặc định 10001 và 185. Kernel điều khiển giới hạn 1 CPU/2 GiB. Feast/NumPy2 và Spark/NumPy1.26 nằm ở hai môi trường riêng. [Bốn container receipts](evidence/notebook/controller/preflight.json) và các tệp `*-container.json` ghi image, UID, giới hạn, thời điểm, exit code.

Notebook và KFP dùng chung [load/merge](../ml/notebook_load.py), [split/train kernel](../ml/training_job.py) và [validation reload](../ml/notebook_validate.py). Notebook dùng `local[2]`; đây **chưa** là distributed training. Kernel ML/protocol không đổi so với các lượt đã kiểm trước. [Mốc KFP sau đó](distributed-training.md) đã chứng minh task train chạy trên hai executor riêng qua event log; số lượng task orchestration không tự chứng minh phân tán.

Trên Linux VM, từ repo root đã có nguồn feature, Feast registry và hai image trong [hướng dẫn Feast](feast-feature-store.md). Clone Git không chứa dữ liệu và dịch vụ. Cài môi trường Jupyter riêng rồi chạy, dùng tên run mới mỗi lần:

```bash
python3 -m venv data/runtime/notebook-venv
data/runtime/notebook-venv/bin/pip install -r infra/notebook/requirements.txt
data/runtime/notebook-venv/bin/python -m ipykernel install --sys-prefix --name python3
sudo data/runtime/notebook-venv/bin/python scripts/run-training-notebook.py \
  --run notebook-demo-new --deadline-utc "$DEADLINE_UTC"
```

`DEADLINE_UTC` phải là giờ tự STOP thật của VM, tính từ `lastStartTimestamp + maxRunDuration`, không phải thời gian tự cộng thêm từ lúc chạy lệnh. Launcher cần ít nhất 90 phút, RAM khả dụng 16 GiB và 30 GB trống trên mỗi đĩa dữ liệu/scratch trước admission; có guard trước từng bước, container có giới hạn riêng. Output mới ở `outputs/notebook-execution/<run>/`; nếu lỗi vẫn giữ notebook cùng traceback. Không chạy đè workspace đã hoàn tất.

Tooling thực tế: nbclient 0.10.4, nbformat 5.10.4, ipykernel 6.30.1, nbconvert 7.16.6; [installed lock](evidence/notebook/environment/installed.lock). Cách execute/save output theo [NBClient chính thức](https://nbclient.readthedocs.io/en/latest/client.html). [Manifest](evidence/notebook/capture/manifest.json), [map 24 tệp native](evidence/notebook/capture/evidence-map.json) và [hash ảnh](evidence/notebook/screenshots/capture.json) ràng buộc bằng chứng với đúng nguồn/ảnh thực, không sửa output hoặc pixel. Đây là cohort trung gian 60 ngày, không phải training trên nguồn 100 GB.
