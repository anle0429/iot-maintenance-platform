# Chuẩn bị CI phục vụ model: Jenkins → KServe và feature API

**Chưa có serving CI hoặc A/B chạy thành công trên VM.** Source chuẩn bị ban đầu là **`d811a7853b431ba5bb3d248d4526aad5f3ff635e`**, kiểm trên PC Windows/Python3.13.14. VM đang chạy phiên28/10; training CI đã SUCCESS, serving CI chưa qua hết các gate. [A/B API mới trênPC](ab-testing.md) đã có routing/ledger/metrics/PSI và400HTTPpredictions, và [phase A/B đã nối mã](ci-ab-deployment.md), nhưng chưa chạy triển khai/đọc giám sát native; chốt `switch` cố ý từ chối chuyển toàn bộ lưu lượng khi chưa có bằng chứng đó. F33/F34/F52 vẫn đang làm.

Cập nhật28/10: model6 từ training CI đã được cấp marker cho **export trong lab mô phỏng riêng**, SDK kiểm đúng lineage/weights. Serving build1 dừng vì Jenkins không đọc được bundle0700; source9f7e7e6 sửa quyền đọc riêng bundle. Build2 qua61contract/202API tests Linux nhưng mutation chưa chạy vì thiếu parent của script phụ trợ; source1134ead sửa `also_copy` sang thư mục `scripts/` trong packet73file. **Build3 kết thúc FAILURE sau40phút56,998giây**:61contract/202APItests Linux PASS, console mutmut ghi đủ1.582mutants/1.245killed/337survived, khoảng78,70%. Bước xuất inventory sau đó lỗi vì mutmut3.8 cần `--all BOOLEAN`; vì vậy chưa có structured stats hoặc verification gate, không nhận console là inventory được duyệt. Chưa image rollout/A-B/cutover thành công; traffic vẫn version1. Các snapshot PC bên dưới giữ nguyên nguồn cũ.

Trong lúc build3 chạy, test A/B được bổ sung để kiểm đúng hai vector chia nhóm ở biên byte127/128, readiness trả đủ hai pin/reference, ledger giữ schema định danh khi mở lại, và metrics khớp bảng đáp án có hai nhóm khác nhau/lỗi/đúng biên histogram. Test startup giờ thực hiện prediction qua backend được lắp vào API, thay vì chỉ đọc tên experiment. **206test PASS/1POSIXskip trên PC**,425statements100%/125of128branches97,65625%; chưa phải native mutation gate cho bộ test mới. Không sửa source đã niêm phong của build3 hoặc nhận các thay đổi chữ hoa/thường tương đương là killed.

Cache thật của mutmut3.8 trên Linux còn cho thấy tên phương thức dạng `module.xǁClassǁmethod__mutmut_1`; parser cũ chỉ tách `.x_` của hàm thường. Regression test đã tái hiện lỗi, parser mới nhận cả hai dạng bằng biểu thức đầy đủ và vẫn từ chối tên lạ/sai module/thiếu inventory. **62contract tests/41subtests PASS trên PC** sau sửa; không đổi7module, số mutant, ngưỡng coverage/mutation, timeout hoặc worker. Build3 vẫn dùng source1134ead cũ; sửa này cần source/build mới để được kiểm native.

Thêm một kiểm tra chính sách: tag registry `production=false` phải chặn export kể cả khi stage cũ ghi `Production`; chỉ trường hợp **không có tag** mới được dùng stage.55test model binding PASS trên PC sau bổ sung; bộ55 này gồm một test mới so với lần đo206 ở trên.

Runner sửa thành `mutmut results --all true`; đã kiểm bằng CLI3.8 thực trong chính image Linux cũ, với **hai trạng thái fixture được khai báo rõ**, xuất được cả tên hàm/class và file stats đúng chỗ. Đây chỉ là kiểm tương thích CLI. Runner còn giữ raw metadata của7module ngay sau mutation, kể cả khi bước sau lỗi; metadata chưa hoàn tất không được dùng để vượt gate. **63contract tests/41subtests PASS trên PC** sau bổ sung kiểm bảo toàn metadata. Các bản gói chuẩn bị trước sửa CLI chưa được submit; lượt native mới phải lấy source mới, môi trường mới và đo lại toàn bộ1.582mutants.

CI này nhằm tránh tình huống feature API gọi model mới nhưng vẫn đọc Redis, metadata hoặc ảnh chạy của bản cũ. [Jenkinsfile](../infra/ci/Serving.Jenkinsfile) và [driver](../scripts/ci-serving.py) chuẩn bị từng release độc lập, rồi mới đổi API chính sau các chốt kiểm. Đây là mã ứng dụng/vận hành cần cho coursework; secrets và trạng thái chạy riêng không nằm trong Git.

## Luồng và các điều kiện

1. **Nguồn cố định:** người vận hành chọn full Git SHA đã push và model version cụ thể. Gói ban đầu có56file; [source A/B đầu](ab-testing.md) có69file và [CI bridge](ci-ab-deployment.md) có73file, cùng manifest SHA từng file; kiểm lại trước các phase. Không lấy working files hoặc tự chọn alias `candidate`.
2. **Tài nguyên thật:** còn ít nhất90phút trước thời điểm auto-stop thực,12GiB RAM khả dụng và20GB trên cả HDD/SSD. Các phase sau vẫn cần15phút dự phòng. Đây là guard của serving; guard100phút ban đầu/90phút runner,24GiB/40GB của training giữ riêng.
3. **Model production:** [exporter](serving-release.md) kiểm trạng thái registry, run, protocol, data/model lineage và artifact hash. Hai API dùng cùng [pin9trường](versioned-serving.md). CI không tự promote model. Snapshot registry thật trước VM dừng có5version `production=false`; version2 trong kiểm thử này chỉ là fixture.
4. **Kiểm thử Linux:** [test runner](../scripts/test-serving-ci.py) yêu cầu mọi API test thực thi, coverage statement và branch đều **>90%**, không excluded lines. Mutation **>80% killed/total** trên đủ7module đang chờ native gate:4module version pin và3module A/B mới; không tính timeout, skipped, thiếu test hoặc interrupted là killed. Môi trường mới, tối đa2worker/4GiB; kết quả từng mutant và dependency lock được giữ. Build3 thất bại ở CLI export sau mutation; bộ test mới chưa có native gate PASS.
5. **Build/import:** probe source/model/pin trong image predictor/feature và source trong image A/B. Hash config blob thực từ Docker-save rồi so với CRI config ID; không giả định Docker manifest ID là config ID. Import chỉ rõ socket containerd của K3s và namespace `k8s.io`.
6. **Stage độc lập:** release `serve-jenkins-<build>` có KServe InferenceService mới, Redis ClusterIP mới và ConfigMap seed immutable riêng. Chuẩn bị Feast từ cohort medium hiện có, đọc200máy và32vector oracle trước khi đổi API chính. Lease dùng giờ thực, không sửa timestamp để làm dữ liệu có vẻ mới.
7. **A/B trước cutover:** phase mới dùng [chốt kiểm báo cáo](../ml/serving_ci.py), yêu cầu đúng candidate pin/ISVC UID, hai nhóm tối thiểu100request khác ID, phản hồi/model identity hợp lệ, counters khớp và drift8feature không dùng nhãn. Các con số này là mức kiểm kỹ thuật được khai báo, **không phải phép kiểm chất lượng hay độ tin cậy thống kê**. Router/traffic split và readback counters/PSI đã kiểm quaHTTPthật trênPC với registry/Feastfixtures; xem [A/B](ab-testing.md). [PhaseJenkins/nativeadapter](ci-ab-deployment.md) đã có mã tạo deployment/probe/readback/SQLite và kiểm ISVCUID; chưa có lượt native. Giữ record dưới5phút và recheck tài nguyên trước cutover. Toàn pipeline chưa có bằng chứng SUCCESS; phải kiểm native trước khi nhận hoàn tất.
8. **Switch/verify sau khi đủ A/B:** Helm `--atomic --wait` đổi cùng lúc API image, init image, seed, Redis endpoint và KServe URL. Đọc lại generation/Ready pods/CRI hashes/ISVC UID; đối chiếu HTTP200máy với latest-row lookup độc lập và arithmetic portable. Lưu response và pod receipts. Các bước này hiện mới là mã chuẩn bị, chưa có rollout thật.

Rollback chỉ hướng tới revision trước của chính release; không ghi đè thay đổi ngoài release. Giữ model/Redis cũ để phục hồi, không tự xóa dữ liệu. Lệnh rollback thành công chưa đủ chứng minh dịch vụ cũ khỏe; lease cũ có thể đã hết hạn sau VM STOP. HPA tối đa2replica cùng một VM, không nhận là HA nhiều máy.

## Phần chuẩn bị d811 đã kiểm chứng trên PC

[Bằng chứng hiện tại](evidence/serving-ci-preparation/current/verification.json) gắn15source hashes với commit d811a78:

- **37test hợp đồng/export/training PASS**,38subtest; `ml.serving_ci`121statements/70branches **100%**,0excluded. Có kiểm đường dẫn Jenkins→host Docker, ranh giới tài nguyên, strict coverage/mutation counts, paired rollout, seed/image/UID, thiếu hoặc sai báo cáo A/B. Hai nhóm100request trong test A/B là dữ liệu kiểm thử chủ động, không phải request gửi VM.
- **207test PASS/1POSIX timezone skip** trong bản sao37file theo allowlist đơn giản của test context. Hai API serving đạt244statements/74branches100%; không thay cho mutation Linux hoặc Locust native. Bản sao là filesystem thông thường, chưa chứng minh Docker builder xử lý context giống hệt.
- **Helm3.20.0 lint/template thực:** dựng Service/Deployment/HPA cho Kubernetes1.35.9; image/init cùng release, private ClusterIP, seed và model URL khớp, HPA max2. [Rendered YAML](evidence/serving-ci-preparation/current/helm-rendered.yaml) không có status/UID giả.
- Gói serving56file SHA256 `55dd626baf926948a460afbbb7ef4a39d2d7b4c1d9a705973e4d45fbc50cc5b8`; [manifest](evidence/serving-ci-preparation/current/source-v4.manifest.json). Gói training mới89file SHA256 `a87a161546db6839c0d6b1032cc339fa3f6b7a979b4639d7562d2cb135673428`; [manifest](evidence/serving-ci-preparation/current/training-source-v4.manifest.json). Cả44file ML guard byte-identical với d83b338; chưa đổi kernel/protocol.
- Gói training lịch sử d83b338/84file được tái tạo đúng SHA `cd8b42111ea3c2daa581ee3ac7814f584c629aadd40756e629b1b82f25866b82`. [Capsule ban đầu source3eff](evidence/serving-ci-preparation/verification.json) giữ nguyên36test/206test trước khi thêm A/B guard; chỉ capsule `current/` chứng minh guard mới. Lượt đóng gói source417 thiếu `tests/__init__.py` đã lỗi; [probe](evidence/serving-ci-preparation/old-source-rejection.json) xác nhận exporter hiện từ chối trước khi tạo archive, không nhận gói lỗi là thành công.

Hai manifest giữ31payload nhỏ của hai thời điểm riêng. Hai console mỗi capsule chỉ bỏ đường dẫn PC bằng phép thay được ghi rõ, có hash raw/published. Các payload khác giữ bytes gốc; không có weights runtime, credentials, tar lớn hoặc binary Helm trong Git. [Manifest hiện tại](evidence/serving-ci-preparation/current/manifest.json).

## Tự kiểm và tiếp tục

Trên môi trường Python test của dự án đã cài dependency tương ứng:

```bash
python -m pytest tests/test_serving_ci.py tests/test_ci_training.py tests/test_serving_release.py -q
python -S scripts/ci-serving.py --help
python scripts/package-serving-release.py --commit d811a7853b431ba5bb3d248d4526aad5f3ff635e --output outputs/serving-review.tar
helm lint infra/charts/iot-api -f docs/evidence/serving-ci-preparation/current/fixture-feature-values.json
helm template iot-feature-api infra/charts/iot-api -n iot-features --kube-version 1.35.9 -f docs/evidence/serving-ci-preparation/current/fixture-feature-values.json
```

Tên output phải chưa tồn tại; tool từ chối overwrite. Hai lệnh Helm trên chỉ lint/render fixture, không deploy. Linux test image dùng [38dependency pins](../infra/ci/requirements-serving-tests.lock); không cài mutmut/fork trên Windows hoặc bật Docker khi thiếu RAM.

[Training CI đã SUCCESS](ci-training.md) trên VM với sourcece510c6, KFP mới đủ5task/hai executor/model candidate6. Bước tiếp theo còn cần model production theo policy đã chốt, liveSDKexport, API Linux coverage/mutation/load, image probes, router/monitor A/B và native CI rollout/rollback kèm ảnh thật. Jenkins `iot-serving` chưa được cài trên VM. Chỉ dùng private trusted Jenkins, source do người vận hành chọn; không webhook/untrusted PR hoặc public endpoint mới.

Tham chiếu chính thức: [KServe custom predictor](https://kserve.github.io/website/docs/model-serving/predictive-inference/frameworks/custom-predictor), [Helm3 upgrade](https://docs.helm.sh/docs/v3/helm/helm_upgrade/), [mutmut](https://mutmut.readthedocs.io/en/latest/). Tài liệu nêu mục đích các API; bằng chứng chạy của dự án được tách riêng ở trên.

Phần A/B mới ở sourceadaba5a có20test và sealed227PASS/1POSIXskip, coverage425statements100%/125of128branches97,66%; [hướng dẫn và proof hiện tại](ab-testing.md). Gói d811/56file và các số đo ở mục trên được giữ theo source cũ, không relabel thành native CI.

[CI bridge source da1b180](ci-ab-deployment.md) bổ sung73file/49contracttests/262sealedtests và800HTTPtrial với healthprobes nền. Các mục số đo d811 phía trên vẫn là lịch sử; không cộng hoặc đổi nguồn bằng chứng.
