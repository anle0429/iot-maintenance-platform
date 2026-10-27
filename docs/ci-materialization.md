# CI materialization: offline Feast → Redis

Pipeline Jenkins riêng `iot-materialization` kiểm thử, build hai image SDK và tự triển khai DAG Airflow `iot_ci_materialization`. Nguồn là bản compact 200 máy × 60 ngày đã được kiểm chứng; không sinh lại dữ liệu hoặc train model. [Jenkinsfile](../infra/ci/Materialization.Jenkinsfile), [controller](../scripts/ci-materialization.py), [DAG](../infra/ci/iot_ci_materialization.py).

## Phạm vi và thứ tự

Mỗi build nhận `SOURCE_COMMIT` gồm 40 ký tự hex. Gói nguồn chỉ lấy từ Git blob của commit đó; mỗi phase kiểm SHA trước khi chạy. Test không qua thì build/deploy không chạy. Docker image đóng gói mã đã test; một container mạng `none` đọc lại toàn bộ hash trong image trước khi deploy. DAG được copy nguyên tử và kiểm SHA ở cả controller lẫn mỗi task.

`register_bound_source → initial_materialize → validate_initial → incremental_materialize → validate_incremental → replay_checkpoint → validate_replay → publish_to_datahub`.

Jenkins lấy mật khẩu Airflow từ credential `iot-airflow`; token chỉ ở bộ nhớ. Controller và Airflow/DataHub dùng mạng riêng trên cùng VM, màn hình Jenkins chỉ qua IAP/loopback. Đây là trusted private CI của một người dùng, Docker socket có quyền cao; không phải thiết kế runner nhiều tenant.

Build được trigger bằng tay trên một source commit; test/build/deploy/verify sau đó tự chạy. Chưa cấu hình GitHub webhook hoặc nhận untrusted pull requests vào runner này.

Mỗi build có project Redis `iot_ci_mat_<build>` và thư mục checkpoint riêng `data/ci/releases/mat-jenkins-<build>/state`. Các namespace training, serving và materialization cũ được giữ riêng. Chỉ thư mục state/output của release được ghi; compact source và báo cáo batch được mount read-only. Không xóa checkpoint cũ hoặc replay ngược thời gian vào kho phục vụ.

## Known answers và TTL

Lần đầu materialize tới `2026-11-29T23:00:00Z`, incremental tới `2026-11-30T00:00:00Z`, rồi replay cùng cutoff. Đây là thời gian mô phỏng. Mỗi bước validate so **tất cả 200 máy × 8 feature** với latest offline row tại cutoff, kiểm SHA nguồn/report/seal và checkpoint. Incremental phải thay đổi 200 vector; replay phải thay đổi 0 vector và thêm 0 interval native Feast. Không dùng số hàng hoặc HTTP success đơn thuần làm bằng chứng giá trị đúng.

Feature view TTL 10 phút giới hạn lịch sử dùng ở simulated event time; Redis key TTL 3.600 giây là thời hạn cache theo wall clock. Tuổi feature tại cutoff phải trong 0–600 giây. CI không đổi event timestamp thành hiện tại để giả freshness; online values có thể hết TTL sau khi build xong. Xem [phân biệt các đồng hồ](feast-materialization.md).

Mỗi SDK container 1 CPU; Feast 8 GiB, metadata 2 GiB; pool `feast_jobs` một slot và DAG một active run. Admission riêng của workload này cần tối thiểu 25 phút trước auto-stop thật, RAM khả dụng 12 GiB và 10 GB trên mỗi đĩa. Không thay guard 40 phút của CI DP1–DP3 hoặc 90 phút của full training. Không chạy Spark/generator/ML trong pipeline này.

## Trạng thái kiểm chứng

**Build Jenkins số 1 SUCCESS**, nguồn Git `a6e36f79745350e8aac081a99fe878757bd81bb2`, chạy trên VM `iot-cw-vm` 8 CPU/64 GiB ngày27/10/2026. [Build native](evidence/ci-materialization/controller/build.json), [6 stage thành công gồm post-action](evidence/ci-materialization/controller/stage-view.json), [console gốc: 6 tests PASS](evidence/ci-materialization/controller/console.txt), [26 tệp nguồn sealed](evidence/ci-materialization/controller/source-manifest.json).

| Phép kiểm | Kết quả đo thật |
|---|---|
| Toàn Jenkins build | **107,727 giây**, SUCCESS |
| DAG Airflow | 20:14:32.974263→20:15:43.689814 UTC, **70,715551 giây**, đủ8task success/try1 |
| Initial | 200máy/8feature khớp offline; native interval22:00→23:00 |
| Incremental | **200/200 vector thay đổi**, thêm đúng interval23:00→00:00 |
| Replay | **0 vector thay đổi, 0 interval mới**, cả200 vector khớp |
| DataHub | Indexed đúng compact→online/producing CI task và **6 assertion results SUCCESS** theo run `mat-jenkins-1` |
| Runtime | 8 container exit0/OOMfalse; mỗi1CPU, Feast8GiB/metadata2GiB; project `iot_ci_mat_1` |

[Airflow API gốc](evidence/ci-materialization/reports/airflow.json), [kết quả CI](evidence/ci-materialization/reports/verification.json), [initial](evidence/ci-materialization/reports/native/initial/materialization.json), [incremental](evidence/ci-materialization/reports/native/incremental/materialization.json), [replay](evidence/ci-materialization/reports/native/replay/materialization.json), [DataHub indexed](evidence/ci-materialization/reports/datahub-indexed.json). So vector dùng native Feast rồi `allclose(rtol=atol=1e-12)` với truy vấn offline độc lập trong materializer; hash vector chứng minh replay giữ nguyên. Đây là một lượt tích hợp thành công, không phải benchmark/SLA.

![Jenkins test/build/auto-deploy và kiểm chứng đều thành công](evidence/ci-materialization/screenshots/jenkins-materialization-success.jpg)

![Artifact gốc: đủ200máy, incremental thay đổi200, replay thay đổi0](evidence/ci-materialization/screenshots/native-200-machines-incremental-replay.jpg)

Admission thực tế20:14:21UTC: RAM khả dụng **55.133.265.920B**, free tối thiểu hai đĩa **99.499.732.992B**, còn **2.130,358 giây** trước auto-stop20:49:51.550UTC; qua nguyên ngưỡng25phút/12GiB/10GB. Đây là số đo lúc nhận job, không phải đỉnh RAM/đĩa. [Receipt](evidence/ci-materialization/reports/admission.json).

Capsule native **40 payload + manifest / 40.406B**, SHA256 `6ba383ed428ba80cacf60f9b6736756f975fa4755cf5e7acc9a20d97eec341c5`; [manifest hash/byte từng payload](evidence/ci-materialization/capture/manifest.json), [mapping](evidence/ci-materialization/capture/evidence-map.json). Giữ nguyên byte native; ảnh là giao diện thật, không chỉnh pixel hoặc dựng console. Credential values đã được so trực tiếp với từng payload và không có trong gói. Dữ liệu Parquet, registry DB, helper scripts và Jenkins home không nằm trong Git.

Lỗi chuẩn bị trước build được giữ trong [journal](evidence/ci-materialization/capture/failed-helper-journal.txt): cấu hình Jenkins cũ thiếu element `description`, helper đầu dừng trước khi tạo job/build. Helper v2 thêm element, kiểm lại và dùng chính gói nguồn đã sealed; không restart controller hoặc thay security. [Wrapper terminal proof](evidence/ci-materialization/controller/verification.json). Model/serving và các namespace trước không được triển khai lại từ mốc này. F28 có bằng chứng; F29trainingCI và F33/F34productionAPI CI vẫn riêng.

## Tự chạy

Clone Git không chứa bộ compact, Redis, registry, Jenkins home hoặc credentials. Trên bản clone có Git và Python, [tool export](../scripts/package-materialization-release.py) tạo đúng26tệp Git dùng cho pipeline, không lấy working files:

```bash
python scripts/package-materialization-release.py \
  --commit a6e36f79745350e8aac081a99fe878757bd81bb2 \
  --output outputs/materialization-release.tar
```

Copy file `.tar` và `.manifest.json` sang VM đã có platform, rồi ở repo root trên VM dùng Python sau. Gói phải đến từ commit trusted vừa export; không nhận tar từ pull request không tin cậy. Script từ chối directory đã có, xác minh SHA và đường dẫn trước khi extract:

```python
from pathlib import Path
from hashlib import sha256
import json, tarfile

packet = Path('outputs/materialization-release.tar')
manifest = json.loads(packet.with_suffix('.manifest.json').read_text())
assert sha256(packet.read_bytes()).hexdigest() == manifest['archive_sha256']
target = Path('data/ci/source') / manifest['commit']
target.mkdir(parents=True, exist_ok=False)
with tarfile.open(packet) as archive:
    assert all(m.isfile() and m.name.startswith('tree/')
               and '..' not in Path(m.name).parts for m in archive.getmembers())
    archive.extractall(target, filter='data')
for name, expected in manifest['files'].items():
    assert sha256((target / 'tree' / name).read_bytes()).hexdigest() == expected
(target / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
```

Trên VM hiện tại canonical nguồn này đã tồn tại và không cần cài lại. Controller có mount `data/ci/releases` và `airflow/dags/ci` writable; `IOT_DEPLOY_ROOT`, `IOT_AIRFLOW_URL`, pool `feast_jobs` và credential `iot-airflow` đã có từ [CI dữ liệu](ci-data-pipelines.md). Khi tạo controller mới, tạo Pipeline job `iot-materialization`, parameter chuỗi `SOURCE_COMMIT`, script lấy nguyên [Materialization.Jenkinsfile](../infra/ci/Materialization.Jenkinsfile) và bật Groovy sandbox. Chọn **Build with Parameters**, nhập SHA commit đã chuẩn bị; mỗi build tiếp theo dùng state/project mới. Không đưa token/password vào parameter hoặc console. Không bấm lại khi cùng build/DAG còn chạy.

Nếu run thất bại, đọc cùng build và DAG run trước khi retry. `_MATERIALIZING.json` giữ dấu write chưa sealed; pipeline không nhận automatic resume. Chỉ cleanup container có nhãn của release hiện tại. Không xóa registry, Redis keys hoặc report để biến kết quả lỗi thành PASS.

Pipeline dùng [Jenkins Pipeline và credential binding chính thức](https://www.jenkins.io/doc/book/pipeline/jenkinsfile/). Phiên bản controller/SDK thực dùng và giới hạn private CI xem [CI dữ liệu](ci-data-pipelines.md), [Feast materialization](feast-materialization.md).
