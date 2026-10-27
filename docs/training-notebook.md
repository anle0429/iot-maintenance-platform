# Notebook: từ feature đến model đã lưu

[Notebook nguồn](../notebooks/01_failure_training.ipynb) có phần giải thích tiếng Việt và bốn bước ML có thể chạy lần lượt. [Bản đã execute](evidence/notebook/execution/executed.ipynb), [HTML nguyên bản](evidence/notebook/execution/executed.html) và [receipt Jupyter](evidence/notebook/execution/execution.json) ghi lượt thật trên cùng VM 8 CPU/64 GiB: **12:56:39–13:08:04 UTC ngày 27/10/2026**, cả **5 ô code chạy, 0 ô lỗi**. Một ô khởi tạo và bốn ô thực hiện các bước dưới đây.

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

Notebook và KFP dùng chung [load/merge](../ml/notebook_load.py), [split/train kernel](../ml/training_job.py) và [validation reload](../ml/notebook_validate.py). Notebook dùng `local[2]`; đây **chưa** là distributed training. Kernel ML/protocol không đổi so với các lượt đã kiểm trước. Mốc KFP tiếp theo cần chứng minh task train chạy trên executor riêng; không lấy số lượng task orchestration làm bằng chứng phân tán.

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
