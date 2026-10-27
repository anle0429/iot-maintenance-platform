# Notebook và huấn luyện phân tán thật

Cập nhật27/10: [pipeline5task có tự động incrementaldata/model registration](automatic-ml-versioning.md) đã SUCCEEDED, model5/candidate; phần dưới giữ run phân tán đầu tiên để đối chiếu.

[Pipeline](../ml/kfp_notebook.py) đã chạy thành công trên backend Kubeflow 2.17.2, run **`bfb3e4d0-1473-42f7-a732-94ce8d7fdc43`**. [Receipt](evidence/distributed-training/controller/verification.json) hoàn tất lúc **14:05:29 UTC ngày 27/10/2026**; controller đo 942,862 giây, UI đo riêng workflow 15 phút 35 giây. Có năm task: lấy Feast/merge nhãn → split → train phân tán → nạp lại model/validation → đánh giá holdout theo protocol cũ.

![Năm task thành công](evidence/distributed-training/screenshots/five-tasks-succeeded.jpg)

![Run Succeeded và thời gian thật](evidence/distributed-training/screenshots/run-succeeded-detail.jpg)

Pipeline dùng chung các kernel với [notebook đã execute](training-notebook.md). Notebook ban đầu có bốn bước; [lượt Notebook 5 bước hoàn tất 18:47:57 UTC](evidence/notebook-parity/execution/execution.json) đã bổ sung holdout reproduction sau kiểm model, thống nhất **5 bước ML ở cả hai phía**; ô init không tính là bước ML. Native Feast của mỗi lượt lấy mới **3.132.184 dòng**; train/validation/test vẫn là **2.195.668 / 468.132 / 468.384**. Không dùng lại daypart của lượt notebook hoặc chuyển ranh giới/ngưỡng theo kết quả test.

## Điều gì chứng minh phân tán?

Task train dùng Spark `k8s://...`, driver trong pod KFP và **hai executor pod riêng**, mỗi executor 1 CPU, heap 4 GiB và overhead 1 GiB. [Submit wrapper](../ml/distributed_submit.py) giữ driver heap 6 GiB; main container giới hạn 2 CPU/10 GiB. Executor chạy UID185/GID10001, không mount service-account token, bỏ capabilities và dùng seccomp RuntimeDefault. [Pod template](../infra/kfp/spark-executor.yaml), [hai pod đang chạy được observer chụp](evidence/distributed-training/controller/two-executors-running.json), [trạng thái sau chạy](evidence/distributed-training/controller/executor-pods.json).

[Event-log verification](evidence/distributed-training/controller/distributed-verification.json) ghi `ApplicationEnd`, executor ID1 và2, không có task attempt lỗi. Các **stage7–17 `treeAggregate at RDDLossFunction.scala:61`** của quá trình tối ưu LogisticRegression đều có task thành công trên **cả hai executor**. Đây là bằng chứng làm việc của bước fit; chỉ nhìn hai pod rảnh hoặc năm task orchestration sẽ chưa đủ.

![Tổng79task thành công,0task lỗi](evidence/distributed-training/screenshots/two-executor-task-counts.jpg)

![Executor1 thực hiện40task và executor2 thực hiện39task](evidence/distributed-training/screenshots/executor-1-and-2-rows.jpg)

79task gồm fit, validation và serialization trong application train; không gọi tất cả là gradient tasks. History hiển thị `Active` khi event log không có ExecutorRemoved trước ApplicationEnd; pod thật đã `Completed`. Dùng [pod JSON](evidence/distributed-training/controller/executor-pods.json) và ApplicationEnd để chốt trạng thái, không coi chữ Active trong bản History là tiến trình còn chạy. History đọc bản sao byte-identical của event log gốc, có [hash và nguồn](evidence/distributed-training/capture/history-event-copies.json).

Đây là **nhiều JVM/executor trên cùng một VM vật lý**, chưa kiểm nhiều máy hay khả năng chịu mất node. Các PVC hostPath/RWO dùng đĩa sẵn có; dung lượng khai báo PVC không phải quota filesystem. Không nhận tăng tốc so với local[2] vì chưa có benchmark cùng điều kiện; Spark application train khoảng1,5phút còn toàn workflow gồm Feast và các bước khác.

## Kết quả và bảo vệ dữ liệu

[Evaluation](evidence/distributed-training/capture/task-artifacts/evaluation.json): ngưỡng0,7 chọn bằng validation, test precision0,4743 / recall0,6572 / F1=0,5510 / PR-AUC0,6151; TP13.115/FP14.536/FN6.842/TN433.891. PR-AUC là tích phân hình thang Spark không chia bin, không phải average precision. Portable score khớp native Spark trên toàn 468.384hàng test, delta lớn nhất4,44e-16. Đây là tái lập protocol đã đóng băng, không có tuning mới từ holdout.

[Năm typed artifacts](evidence/distributed-training/capture/artifact-verification.json) đã được tải từ object store KFP thật; inventory mọi tệp data/model tham chiếu đã được đọc và so SHA. Credentials object store chỉ tồn tại trong bộ nhớ tiến trình đọc bằng chứng. Dữ liệu/model binary và event log gốc ở VM, Git giữ các receipt/ảnh nhỏ. Cohort này thuộc nguồn trung gian60ngày, không phải training100GB. Dữ liệu mô phỏng, score chưa calibration và model chưa tự thay vào dịch vụ version1.

## Tự chạy và các lỗi đã sửa

Trước pipeline lớn, [oracle Kubernetes](../scripts/run-distributed-oracle.py) chạy360hàng có đáp án biết trước qua chính hai executor. Lượt [oracle cuối](evidence/distributed-training/oracle/verification.json) mất94,205giây, qua purge cutoff, confusionTP60/TN60, portable và threshold isolation; không dùng metric fixture làm chất lượng IoT. Oracle đầu có cảnh báo thiếu seccomp, đã giữ nguồn trước sửa và kiểm lại cấu hình RuntimeDefault.

Từ repo root Linux VM có cluster, source/Feast/PVC/image của [mốc KFP](kfp-cluster.md), dùng tên run mới và deadline thật:

```bash
sudo data/runtime/kfp-venv/bin/python scripts/run-distributed-oracle.py \
  --run distributed-oracle-new --deadline-utc "$DEADLINE_UTC"
sudo data/runtime/kfp-venv/bin/python scripts/run-notebook-kfp.py \
  --run distributed-training-new --deadline-utc "$DEADLINE_UTC" \
  --oracle outputs/distributed-oracle/distributed-oracle-new/verification.json
```

Runner compile từ source hiện tại; không phụ thuộc file prepared riêng của phiên trước. Nó cần 90 phút, RAM khả dụng24GiB và40GB trống trên các đĩa data/scratch; chỉ hủy run của mình khi reserve/deadline chạm guard. [Role](../infra/kfp/spark-driver-rbac.yaml) bổ sung pods/services/configmaps trong namespace coursework, không cấp Secret/cluster/cloudIAM. Đây là namespace một người dùng, chưa phải thiết kế multi-tenant.

Các lượt lỗi được giữ: [Concat literal làm sai đường dẫn](evidence/distributed-training/attempt-path/diagnosis/load-main.txt), [HOSTNAME bị cắt63ký tự](evidence/distributed-training/attempt-identity/diagnosis/cancellation-reason.json), [lượt gửi sớm đã dừng](evidence/distributed-training/attempt-premature/stop-before-repair.json), [image chỉ có python3](evidence/distributed-training/attempt-python/diagnosis/train-main.txt). Backend ghi `FAILED` sau yêu cầu terminate; không sửa thành CANCELED. Sửa bằng Python path wrapper, DownwardAPI metadata.name/status.podIP, đúng interpreter và oracle nhỏ trước lần chạy tiếp. Không đổi nhãn, kernel ML hoặc protocol để biến lượt lỗi thành thành công.

Thiết kế theo [Spark3.5.9 Kubernetes client mode](https://dlcdn.apache.org/spark/docs/3.5.9/running-on-kubernetes.html) và [KFP DownwardAPI](https://kfp-kubernetes.readthedocs.io/en/kfp-kubernetes-2.17/source/kubernetes.html). [Manifest102tệp native](evidence/distributed-training/capture/manifest.json), [mapping](evidence/distributed-training/capture/evidence-map.json) và hash ảnh gắn đúng runtime. CI/CD, drift và tự động nối data version vào mỗi lần training vẫn là mốc tiếp.
