# VM Google Cloud đã chạy và kiểm chứng tăng tốc Spark

Ngày26/10/2026 người dùng đã chọn phương án accelerated. Terraform đã apply **14 resources**; lần plan sau apply báo **No changes**. VM `iot-cw-vm` ở `us-central1-a`, **8vCPU/64GiB**, Ubuntu24.04, boot50GiB SSD balanced +data500GiB HDD standard +scratch150GiB SSD balanced. Đây là hạ tầng đã chạy thật, không chỉ YAML/plan. [Deployment](evidence/gcp-runtime/cloud-deployment.json), [apply đã che định danh tài khoản](evidence/gcp-runtime/terraform-apply-redacted.txt), [đối chiếu tổng hợp](evidence/gcp-runtime/verification.json).

SSH qua IAP/OS Login đã gọi thử thành công; firewall riêng chỉ ingress22 từ IAP, không mở UI/API/database public. VM có IPv4 ephemeral cho tải package/image. GCE trả đúng maxRunDuration28800giây và hành động STOP, automaticRestart=false. Đây là **giới hạn8giờ/start được cấu hình**, chưa chờ8giờ để quan sát tự dừng. [Mốc nguồn lớn](scale-100gb.md) đã kiểm STOP/START thủ công khi không có job đang chạy: mount,9 dịch vụ, DAG và nguồn trung gian phục hồi; chưa kiểm crash giữa job. Máy hiện dùng cùng tài khoản/project đã được người dùng cung cấp; các định danh và Terraform state chỉ giữ cục bộ, ngoài Git.

## Vì sao phương án này giúp tiếp tục bài nhanh hơn

- RAM64GiB tạo dư địa cho Spark và [Airflow/DataHub/feature đã triển khai tiếp trên pilot](orchestration.md). Chưa tuyên bố tất cả platform đã chạy hoặc đủ tài nguyên cho mọi workload.
- HDD giữ source/output; SSD giữ cả Docker data-root và containerd image store, cùng scratch cho Spark. Chỉ đổi Docker data-root không đủ với Docker29: image store containerd có đường riêng. Systemd có RequiresMountsFor để daemon chờ SSD; không dùng thư mục trống trên boot làm kho thay thế khi mount chưa sẵn sàng.
- Launcher Linux mới đọc profile rõ ràng, kiểm CPU/RAM/đĩa/mount thật, ghi phiên bản/hash và giới hạn Docker đã resolve. Nó không kế thừa local[2]/heap1GiB của launcher PC khi chọn optimized.
- Generator vẫn một tiến trình Python: VM nhiều vCPU chưa tự làm sinh dữ liệu nhanh theo số core. Sau khi đo throughput và chọn session đủ dài, có thể sinh dữ liệu có log trong khi triển khai các phần khác, giữ nguyên tính liên tục/seed/nhãn và guard dung lượng. Chưa tự chạy100GB.

## Đo cùng workload trên cùng VM

Nguồn JSONL liên tục200máy×288giờ, **1.117.608.156byte offline**, được chuyển từ PC qua IAP, không tái sinh với seed khác. Receipt semantic và toàn bộ part bytes/catalog được adapter kiểm trên VM; Spark kiểm input lại sau run. Hai lượt dùng cùng generator/Spark/model code, cùng image ID và source manifest. Mỗi cấu hình có **một full job**, không phải thống kê ổn định qua nhiều lần chạy.

| Số đo | Baseline `cloud-baseline-26` | Optimized `cloud-optimized-26` |
|---|---:|---:|
| Spark local threads / driver heap | 2 /1GiB | 6 /20GiB |
| Container CPU / RAM limit | 2 /2.5GiB | 6 /28GiB |
| Shuffle/output partitions | 8 | 16 |
| Scratch | HDD | SSD |
| Spark job wall time | **223,942s** | **100,892s** |
| Disk bytes spilled, cộng các task | **967.250.334** | **0** |
| Memory bytes spilled, cộng các task | 5.895.410.980 | 0 |
| Cgroup memory.peak trước shutdown | 2.375.548.928byte | 9.126.223.872byte |
| Bronze telemetry rows | 3.525.120 | 3.525.120 |
| Silver unique rows | 3.456.000 | 3.456.000 |
| Spark checks / host unittest | 13 /84 | 13 /84 |
| Năm table Parquet, chưa tính fixture/CRC | 202.868.144byte | 218.332.992byte |

Trong lượt đo này, job nhanh **2,2196lần**, giảm khoảng55% thời gian. Đây là thay đổi phối hợp threads/heap/container/partitions/scratch trên **cùng VM64GiB**, không chứng minh riêng RAM64 nhanh hơn32 hoặc riêng SSD đóng góp bao nhiêu. OS cache, JIT và hoạt động nền chưa cô lập. Thời gian job không gồm upload/build image và thu thập metrics sau job; cả hai không UI pause. Spill là tổng công việc các task, không phải đỉnh scratch đồng thời hoặc dung lượng dataset.

Kết quả đọc lại vẫn đúng: multiset Bronze hai chiều, v1 pressure NULL/v2 present, latest export/conflict/replay, machine FK, grain và codec footer Snappy đều qua. Metadata/source schema và đáp án query count/sum/max của hai lượt khớp; tổng số thực so với tolerance. Median ba lượt query JSONL5,304→2,453s; Parquet0,181→0,110s. Đây là ba query trong mỗi full job, không phải ba lần chạy toàn job.

Tăng partitions làm nhiều file hơn và Parquet output lớn hơn khoảng7,6% trong lượt này. Mục tiêu kiểm này là tốc độ/spill; chưa compaction/z-order hoặc storage optimization hoàn chỉnh. [Report baseline](evidence/gcp-runtime/baseline/ingest-report.json), [optimized](evidence/gcp-runtime/optimized/ingest-report.json), [metrics baseline](evidence/gcp-runtime/baseline/spark-metrics.json), [optimized](evidence/gcp-runtime/optimized/spark-metrics.json).

Baseline Spark đã hoàn tất và có success marker, nhưng wrapper ban đầu exit1 ở bước đọc event log riêng của UID185. Metrics được đọc lại bằng chính UID trong image, không chạy lại Spark và không nới quyền log. RAM trống trước run chưa kịp persist nên context baseline để NULL. Launcher đã sửa cho optimized; cả command metrics và Ansible pilot đều thành công. [Context phục hồi trung thực](evidence/gcp-runtime/baseline/cloud-context.json), [context optimized](evidence/gcp-runtime/optimized/cloud-context.json). Hai manifest nguồn v1/v2 lưu khác biệt launcher/Ansible; Spark/generator/model bytes không đổi.

## Ansible và runtime thực

[Playbook](../infra/ansible/bootstrap.yml) chia bốn role: common, storage, docker và pilot. Nó đối chiếu metadata project/VM trước thao tác, chỉ tạo ext4 trên đĩa đúng symlink/tên/kích thước, không có signature; từ chối filesystem/label lạ, không force format. Lượt đầu đã quan sát data/scratch trống; những lượt sau giữ UUID/mount/data. Role pilot triển khai đúng job optimized; success marker làm lần chạy lại bỏ qua job đã hoàn tất.

Lần kiểm cuối: **ok52, changed0, failed0**; [log](evidence/gcp-runtime/ansible-idempotent.txt). [Log triển khai pilot](evidence/gcp-runtime/ansible-pilot.txt). Bootstrap đầu gặp tên plugin containerd cũ; sửa schema3/URI đầy đủ và thêm validate trước copy config. Quyền Docker/containerd tự quản cho daemon directories tránh đổi mode lặp lại. Log lỗi giữ ở outputs, không dùng làm proof thành công.

[Runtime thật](evidence/gcp-runtime/host-report.json): AMD EPYC7B12,8vCPU, Linux ghi nhận67.419.459.584byte RAM, host Python3.12.3; Docker29.9.2/Compose5.6.1/containerd2.4.2. Ansible-core2.21.5 chạy trong venv Linux. Spark image vẫn3.5.9/Python3.10.12/Java17.0.19 và digest base đã khóa. Ubuntu image thực `ubuntu-2404-noble-amd64-v20261024`. Cgroup peak gồm accounting process/cache/kernel, không phải host RSS hoặc JVM heap riêng.

## Tự chạy và bàn giao

Từ PC/laptop đã đăng nhập Google CLI bằng tài khoản được cấp quyền:

```powershell
# Chỉ start nếu VM đang dừng; không tạo lại VM hoặc apply lại để đăng nhập.
gcloud compute instances start iot-cw-vm --project=YOUR_PROJECT_ID --zone=us-central1-a
gcloud compute ssh iot-cw-vm --project=YOUR_PROJECT_ID --zone=us-central1-a --tunnel-through-iap
```

Trong shell VM:

```bash
cd /srv/iot-data/workspace/iot-maintenance-platform
python3 scripts/run-cloud-ingest.py --run-name practice-fast --mode optimized --source data/source/chunk-ingest-26 --receipt outputs/cloud-source-receipt.json --skip-build
# Cấu hình/kiểm lại và không chạy lại pilot đã có marker:
$HOME/.venvs/iot-control/bin/ansible-playbook -i localhost, infra/ansible/bootstrap.yml --extra-vars=@$HOME/pilot-vars.json
```

Chọn run-name mới, không ghi đè/replay vào output cũ. Source/output nằm trên HDD; scratch từng run ở SSD. [Launcher](../scripts/run-cloud-ingest.py), [Compose override](../infra/ingest.cloud.compose.yaml). User/source receipt ở trong project, Google credentials không mount vào container. Chỉ dùng run khi có report passed và `_RUN_SUCCESS.json`. Máy dừng vẫn giữ ba đĩa, chi phí storage vẫn phát sinh.

Project trên VM là **runtime snapshot của working tree chưa commit**, chuyển qua IAP, chưa phải clone GitHub đã cập nhật. Manifest/hash ràng buộc code đã chạy; dữ liệu/events/models/volumes/state không đồng bộ qua Git. Terraform state chính/backup ở PC, đã persist profile accelerated trong tfvars ignored để không vô tình trở về basic. Helper mặc định dùng profile cục bộ khi có; quota cho update tính phần đã dùng bởi tài nguyên Terraform sở hữu, không đòi thêm8CPU cho chính VM đang chạy. Sau apply, plan thật xác nhận không drift. Chưa có remote backend; không apply từ laptop thiếu state.

SDK Windows dùng PuTTY: SCP từng remote source riêng và tránh khởi động đồng thời nhiều gcloud từ cùng cwd. Secrets/tfvars/state/raw plan chỉ local; public log đã che định danh tài khoản. VM chạy hạ tầng của riêng coursework; không dừng container hmaa_postgres trên PC hoặc đụng dự án khác.

## Phần còn lại và chi phí

Phiên VM8h +giữ đĩa24h dự toán **5,52USD**, gồm30% dự phòng, chưa mạng/log/snapshot/thuế/tỷgiá; chưa phải hóa đơn. VM dừng vẫn giữ đĩa khoảng **1,315USD/ngày** trước dự phòng. Chưa nâng cấp Paid billing, chưa xác minh credit còn lại qua Billing UI. [Nguồn giá và so sánh](gcp-vm.md), [estimate accelerated](evidence/gcp-options/accelerated.json).

[Budget theo volume](evidence/gcp-runtime/storage-budget-estimate.json) chỉ là estimate: HDD cần295GB, SSD135GB theo các margin đã ghi, free snapshot tương ứng khoảng520GB/153GB nên fits_now=true. Gold/feature/warehouse, platform state và peak scratch100GB vẫn chưa đo thật. Trước sinh lớn phải kiểm lại free space/throughput/giới hạn phiên; guard mặc định generator2GiB chưa tự bỏ. Không nhận kết quả1,12GB/ngoại suy thành100GB.

**Cập nhật cùng ngày:** [Airflow DP1–DP3/DataHub đã chạy thật](orchestration.md), gồm validation lỗi và phục hồi, ảnh UI qua IAP,64rules/14outputs và91unitPC/VM. [Mốc tiếp nối](scale-100gb.md) đã tính lại budget: HDD400 GB gồm cap nguồn115 GB và reserve285 GB, SSD35 GB cho tăng trưởng platform/khoảng trống. Spark lớn phải chuyển scratch sang HDD và kiểm lại; budget135 GB SSD cũ chỉ là lịch sử. Nguồn trung gian5,59 GB đã qua full readback,13Sparkchecks và99unit; có mẫu scratch/RAM thực. Nguồn lớn đã sinh và full readback **102.447.847.856 byte offline** sau bốn gate; chưa có bằng chứng Spark100 GB.

Lần STOP/START này giữ cùng VM/đĩa nhưng CPU platform GCE đổi AMD Rome→Intel Broadwell; [sessions](evidence/scale-source/vm-restart/sessions.json). Không dùng calibration trước restart làm cam kết tốc độ của phiên mới. Tiếp feature/label/model protocol và serving/agent; model cũ vẫn test0positive. Chưa Kubernetes/KFP/KServe/LLM/API hoặc ảnh GCP Console. Deadline tạm31/10 giữ nguyên, tracker đủ93tiêu chí.
