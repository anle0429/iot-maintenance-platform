# CI training: Jenkins → KFP → dữ liệu và model candidate

**Chưa có build huấn luyện CI thành công.** Mã CI, SDK riêng, compile và private control oracle đã được kiểm trên VM. F29 vẫn chưa hoàn tất cho tới khi có một lượt Jenkins tạo run KFP mới, chạy đủ năm bước và đọc lại model/data thực tế. Những run KFP thành công trước đây không được tính thành kết quả CI.

[Training.Jenkinsfile](../infra/ci/Training.Jenkinsfile) điều khiển `admission → test → build/import → auto-deploy → fresh KFP verification`. [Controller](../scripts/ci-training.py) dùng cùng [pipeline năm bước](automatic-ml-versioning.md) và cùng kernel/protocol đã cố định. Không tuning thêm từ holdout đã quan sát; model mới chỉ đăng ký **candidate**, không đổi model phục vụ.

## Điều kiện một build được nhận là thành công

1. Gói nguồn lấy từ Git commit đã push, không lấy working files. Mỗi phase so lại SHA của mọi file trong packet; image cũng phải chứa đúng source đó.
2. Admission dùng thời điểm auto-stop thực của phiên VM: CI cần ít nhất100phút ban đầu để dành10phút chuẩn bị, rồi runner vẫn cần90phút/24GiB RAM khả dụng/40GB trên **cả hai đĩa**. Khi đang chạy, guard8GiB/20GB/10phút còn lại giữ nguyên. Không thay số đo để nhận job.
3. Kiểm thử chặn stale run, source/model/data mismatch, production promotion và mở holdout sai validation. Build hai image application trên SDK Feast/Spark đã khóa; probe container mạng `none` kiểm mọi file source trong image. Import vào containerd và xác nhận CRI config ID khớp SHA256 của **config blob thực trong Docker export**; Docker29 image ID có thể là manifest ID khác với config ID, được ghi riêng.
4. Compile cùng năm task, thay riêng image reference bằng tag duy nhất của release. KFP YAML có hai document (pipeline + Kubernetes platform); giữ cả hai, bao gồm mounts, UIDs, resources, DownwardAPI và cache disabled. Không nhận một file YAML là bằng chứng đã chạy.
5. Deploy tự kiểm không còn Notebook/KFP đang hoạt động, backup source thay đổi và copy source đã test sang workspace VM. Snapshot44file gắn đúng source commit; pipeline package được bind SHA trong deployment manifest. Image chứa cùng mã đã đóng gói, các pod đọc bản mounted đã so hash; không nhận là code trong image tự được dùng khi mount che nó.
6. Submit **run mới** `train-jenkins-<build>` vào KFP, workspace mới. Chỉ PASS khi backend SUCCEEDED, có đủ5main container chạy đúng image đã kiểm, hai executor thật cùng thực hiện LR stages, automatic data publication/restore và model-only registration đều PASS. Preflight phải sau khi CI verify bắt đầu; source/data/model version phải liên kết đúng với submission của build này.

Driver train dùng Spark registry image của release; executor giữ SDK Spark3.5.9 đã có và đọc cùng source từ PVC đã bind. Hai executor nằm trên cùng VM, không suy ra multi-host hoặc speedup. Init không tính là bước ML. Dataset vẫn là cohort medium3.132.184hàng, không train toàn102,45GB.

## SDK, quyền và secrets

[Controller Dockerfile](../infra/ci/TrainingController.Dockerfile) đặt KFP2.17.3/kfp-kubernetes2.17.3 trong **venv riêng**. [39 dependency pins](../infra/ci/requirements-training-controller.lock) được lấy từ native KFP environment; `pip check` phải chạy trong venv đó. Không đổi protobuf/numpy/pyarrow của runtime Feast/Spark để cài SDK controller.

Jenkins riêng hiện có một executor, Docker socket trusted và source packet chỉ nhận từ người vận hành. Container controller giới hạn1CPU/2GiB, kết nối mạng host/private KFP, mount kubeconfig hiện có read-only trong thời gian deploy/verify/cleanup. Kubeconfig/token không được copy vào image, artifact hay parameter; không tạo credential/role/endpoint công khai mới. Các bước compile/source probes dùng mạng `none`.

Build được người vận hành trigger với `SOURCE_COMMIT`; các bước sau tự chạy. Chưa có GitHub webhook hoặc chạy untrusted pull request. Controller cũ không cần restart để thêm job. Xem giới hạn quyền Docker và cấu hình Jenkins trong [CI dữ liệu](ci-data-pipelines.md).

## Tự chuẩn bị và chạy

Trên clone có Git/Python, chọn SHA source đã push:

```bash
python scripts/package-training-release.py \
  --commit <source-commit> --output outputs/training-release.tar
```

[Tool export](../scripts/package-training-release.py) có allowlist và từ chối overwrite; tạo tar cùng manifest SHA. Copy sang VM, xác minh/extract theo [quy trình sealed packet](ci-materialization.md#tự-chạy), đặt tại `data/ci/source/<commit>/{tree,manifest.json}`. Giữ nguyên bộ dữ liệu, CAS, registry, oracle distributed và các PVC hiện có; clone Git không chứa runtime này.

Trên Jenkins tạo job Pipeline `iot-training`, parameter chuỗi `SOURCE_COMMIT`, dùng nguyên Jenkinsfile nêu trên với Groovy sandbox. `IOT_DEPLOY_ROOT` và quyền private node đã có từ mốc CI trước. Chỉ trigger khi VM thực còn đủ thời gian. `data/runtime/ci/session.json` phải được ghi từ thời điểm start/stop thực đã được phép; không tự cộng giờ vào file này để vượt guard.

Pipeline bảo toàn report cũ: workspace `data/ml-workspaces/training-cluster-27/train-jenkins-<build>`, receipts `outputs/versioned-training/train-jenkins-<build>`, source/image/deployment manifest `data/ci/releases/train-jenkins-<build>`. File source cũ được backup trước deploy; source snapshot của release được lưu riêng. Pointer mặc định cho lượt Notebook sau cần trỏ tới snapshot mới tương ứng source đã deploy, không dùng binding cũ với mã mới.

Nếu CI thất bại, cleanup chỉ dừng controller của release và đọc đúng backend run ID từ submission, xác nhận display name khớp trước khi yêu cầu terminate khi run vẫn active. Không blanket-delete pods hoặc dữ liệu. Kiểm lại trạng thái run đó trước retry; chưa hỗ trợ resume full training.

## Phần chuẩn bị đã kiểm và phần còn thiếu

- Source cuối **`d83b338c159c8f0b096b033f783925aac7ccb994`** đã push; **21unit tests PASS trên PC và VM**, gồm Docker-save legacy/OCI layouts, config thay đổi dù giữ filename và manifest thiếu/không duy nhất.
- Packet Git có84tệp,44tệp trong ML guard; archive SHA256 `cd8b42111ea3c2daa581ee3ac7814f584c629aadd40756e629b1b82f25866b82`. [Final manifest](evidence/ci-training-preparation/final-source/source-manifest.json), [21native tests](evidence/ci-training-preparation/final-source/unit-tests.txt), [final preparation](evidence/ci-training-preparation/final-source/verification.json).
- SDK controller image `sha256:3edb872a72669b7f50cf4a62393ac3d6e52c18e08c03f7343071d257e411a966`: Python3.12.16, KFP/kfp-kubernetes2.17.3 trong `/opt/kfp-venv`, [pip check sạch](evidence/ci-training-preparation/sdk-preparation/pip-check.txt). SDK build đầu từ nguồn `bc3ca82`; lock/Dockerfile/kernel không đổi ở d83, chỉ sửa controller OCI check và thêm3tests. Không nhận source mới đã dùng để train.
- [Compile thực](evidence/ci-training-preparation/final-source/pipeline.yaml) có đủ5task và document Kubernetes, SHA `a47f6df784d6f30e5e6394f8aade192d69f847d24ce8c3a0e851945a643f8d7c`. Image tags trong package chuẩn bị là **placeholder tương lai**, chưa build/import/deploy runtime application.
- [Private control oracle](evidence/ci-training-preparation/private-control/verification.json) đã dùng transient controller đọc CRI qua socket, kubectl qua kubeconfig read-only và KFP SDK GET thật. Docker manifest ID `865c69e1...`, export config digest/CRI config ID cùng `6ba07ec4...`; không đổi image hoặc registry. Kubeconfig không được copy vào bằng chứng.
- Lượt gọi CRI đầu thiếu `crictl.yaml`; [negative oracle gốc](evidence/ci-training-preparation/private-control/expected-missing-config.txt) giữ lỗi. Đã chỉ rõ `--config=/dev/null` và endpoint socket theo [tài liệu crictl](https://github.com/kubernetes-sigs/cri-tools/blob/master/docs/crictl.md). Probe helper từ Windows có CRLF, Git source LF; [binding](evidence/ci-training-preparation/final-source/export-function-binding.json) giữ raw hash riêng và kiểm nội dung hàm sau universal-newline decoding bằng đúng body Git. Không chỉnh receipt native để làm khớp hash.
- Job Jenkins `iot-training` đã cài/readback đúng source d83, **lastBuild=null/nextBuildNumber=1**. [Binding](evidence/ci-training-preparation/final-source/job-source-binding.json). Mã ML hiện trên VM vẫn snapshot44 cũ và đã so hash không đổi, chưa deploy source mới.
- [Guard đo thật](evidence/ci-training-preparation/final-source/current-session-rejected.json) đã từ chối phiên hiện tại vì còn dưới90phút trước auto-stop20:49:51.550UTC; không gửi training. Yêu cầu mở thêm phiên cùng VM đang chờ người dùng xác nhận theo file mục tiêu.
- Capsule **25payload + manifest /23.445B**, SHA256 `8fc7d8fd2928a99625fb43ad23ef7936ee78068245ad747e110c232ebd68e0ab`; [manifest](evidence/ci-training-preparation/capture/manifest.json), [mapping](evidence/ci-training-preparation/capture/evidence-map.json). Native JSON/YAML/log giữ byte gốc; exact credential values vắng mặt, helpers/bulk/source secrets không nằm trong gói. Không có ảnh CI thành công vì chưa chạy build.
- Còn cần phiên có đủ90phút cho lượt mới, Jenkins build SUCCESS,5task/two-executor proof, model/data registry readback, ảnh thật và clean-clone audit trước khi cập nhật F29Có bằng chứng.

Pipeline dùng [KFP container components chính thức](https://www.kubeflow.org/docs/components/pipelines/user-guides/components/container-components/); các kết quả chạy thật trước CI giữ tại [automatic versioning](automatic-ml-versioning.md), không đổi phạm vi bằng chứng lịch sử.
