# CI/CD cho DP1–DP3

[Jenkinsfile](../infra/ci/Jenkinsfile) chạy trên VM riêng hiện có: **Test → Build verified images → Auto-deploy Airflow DAGs → DP1 → DP2 → DP3**. Người học khởi chạy một commit đã niêm phong; các bước sau tự thực hiện. Chưa có webhook cho mỗi push. Nguồn Git được chuyển qua IAP thành snapshot có SHA của từng file, không cấp GitHub token dài hạn cho máy chạy CI.

## Luồng kiểm chứng

1. Chạy34tests hiện có về generator theo chunk, source receipt, replay, corruption và artifact boundaries. Tạo receipt mới bằng full readback pilot với đúng validator của commit này; receipt lịch sử giữ nguyên. Sau deploy còn chạy các rule Spark trên dữ liệu đã lưu.
2. Đóng gói `generator/` và `pipelines/` từ đúng source commit vào hai image Spark và DataHub. Container độc lập đọc hash toàn bộ source trong image để so với snapshot đã test; label commit riêng chưa đủ chứng minh điều này.
3. Mỗi Jenkins build có release/workspace/output mới. Tự deploy [ba DAG CI](../infra/ci/iot_ci_data_pipelines.py) vào thư mục runtime riêng, giữ các DAG gốc.
4. Gọi Airflow thật DP1 → DP2 → DP3. Mỗi DAG phải hoàn tất ingest, validation và publish DataHub. Observer đọc task state, validation, metadata readback và đúng image ID đã build; bước sau chỉ bắt đầu khi bước trước đạt.
5. Lưu artifact nhỏ ngay cả khi lỗi; không đưa Parquet, token, mật khẩu hoặc state lên Git. Khi lỗi/hủy, chỉ dừng container mang label đúng release đó.

[Controller](../scripts/ci-data-pipelines.py) dùng pilot liên tục20máy×48giờ đã kiểm trước. Đây chưa phải CI100GB hoặc CI model/API production. Output ở `data/ci/releases/<release>/work/`, scratch trên SSD hiện có. Spark2CPU/5GiB/heap3GiB; metadata1CPU/2GiB. Pool Airflow một slot. Admission cần còn40phút trong phiên VM đã duyệt,16GiB RAM khả dụng và15GB trống ở mỗi volume thật; không đo nhầm overlay filesystem trong container CI.

## Quyền và secrets

[Controller image](../infra/ci/Jenkins.Dockerfile) pin Jenkins LTS/JDK21 bằng digest và khóa trực tiếp các plugin. Transitive plugin versions/image thực phải ghi lại khi chạy. Jenkins chỉ publish loopback trên VM, xem qua IAP. Giao diện kết quả chỉ đọc nằm sau IAP; thao tác build/config cần tài khoản riêng. Tắt đăng ký tài khoản và cổng inbound worker. Một executor, không tự khởi chạy sau boot.

[Bootstrap](../infra/ci/jenkins-bootstrap.groovy) đọc secret từ mount runtime riêng, lưu Airflow credential trong Jenkins Credentials và chỉ bind vào ba bước API. Credentials Binding che giá trị trong console; controller không ghi access token/response đăng nhập. File secret, Jenkins home và master key không vào image/Git/evidence. Docker socket cho phép quyền mạnh trên host: chỉ dùng cho nguồn đã chốt của người sở hữu coursework, chưa phải môi trường nhiều tenant không tin cậy.

Cách dùng: chuẩn bị snapshot có commit/file hashes, chọn `SOURCE_COMMIT` trong job `iot-dp1-dp3`, khởi chạy và chờ toàn bộ stage xanh. Mỗi lần dùng build/release mới, không ghi đè lần lỗi. Clone Git không mang secret, nguồn pilot, runtime hoặc dữ liệu CI.

[Docker Jenkins chính thức](https://www.jenkins.io/doc/book/installing/docker/), [Pipeline](https://www.jenkins.io/doc/book/pipeline/), [Credentials Binding](https://plugins.jenkins.io/credentials-binding/).

## Kết quả native ngày27/10

**Jenkins build#2 SUCCESS**, commit nguồn `c9c29533a57b88f5ed7b4028faf2bff456fdf1ad`, runtime6phút38giây. Đây là một release phối hợp ba data pipeline, dùng chung test/build/deploy và kiểm riêng từng DAG. [Receipt cuối](evidence/ci-data-pipelines/native/verification.json), [stage API gốc](evidence/ci-data-pipelines/capture/stage-view.json), [commit trong UI](evidence/ci-data-pipelines/screenshots/jenkins-build2-source-commit.jpg).

![Jenkins native: build2 xanh, build1 lỗi vẫn giữ](evidence/ci-data-pipelines/screenshots/jenkins-build2-all-stages.jpg)

| Pipeline thực | Task ingest/validate/publish | Rules Spark | Đầu ra pilot |
|---|---|---:|---|
| [DP1](evidence/ci-data-pipelines/reports/dp1-verification.json) |3success|17/17|58.752 Bronze telemetry rows|
| [DP2](evidence/ci-data-pipelines/reports/dp2-verification.json) |3success|32/32|57.600 unique Silver/Gold telemetry|
| [DP3](evidence/ci-data-pipelines/reports/dp3-verification.json) |3success|15/15|11.520 feature rows và bảng nhãn riêng|

Native **34tests qua**; pilot được full readback bằng đúng validator của release trước khi build. Hai image có source hash đã kiểm trong container độc lập; cả9container nghiệp vụ chạy đúng image ID mới đã build, exit0/khôngOOM. [Build/source/image receipt](evidence/ci-data-pipelines/reports/build.json), [source validation](evidence/ci-data-pipelines/reports/source-receipt.json), [test console](evidence/ci-data-pipelines/native/console.txt).

[GraphQL readback14datasets](evidence/ci-data-pipelines/indexed/datahub.json) còn kiểm được producing job của đúng CI DAG và assertion run ID `jenkins-2-1`, không chỉ acknowledgment ghi metadata. Adapter [ci_metadata.py](../pipelines/ci_metadata.py) dùng đúng `iot_ci_dp*` trong tiến trình publish riêng; giữ nguyên publisher và các DAG thủ công cũ. Đây là pilot20máy48giờ; không nhận là CI100GB, CI production-model API hoặc CI huấn luyện.

[Ảnh DP1](evidence/ci-data-pipelines/screenshots/dp1-native-verification.jpg), [DP2](evidence/ci-data-pipelines/screenshots/dp2-native-verification.jpg), [DP3](evidence/ci-data-pipelines/screenshots/dp3-native-verification.jpg) chụp đúng artifact do Jenkins lưu. Response HTTP của cả ba artifact giống từng byte với native report đã niêm phong. [Capture hashes](evidence/ci-data-pipelines/screenshots/capture.json), [83member capsule/map](evidence/ci-data-pipelines/capture/evidence-map.json), [manifest82payload](evidence/ci-data-pipelines/capture/manifest.json), [source bindings thành công/thất bại](evidence/ci-data-pipelines/capture/source-commit.json).

## Lần lỗi và cách sửa

Build#1 dừng ở test vì Python dùng `/tmp`128MiB, thấp hơn reserve1GiB của generator:34tests,28errors. [Console gốc](evidence/ci-data-pipelines/failed-build/console.txt). Các bước build/deploy/DP phía sau bị skip; Stage View tô đỏ chúng nên cần đọc console để hiểu đúng. Không ghi lại kết quả cũ thành PASS.

Jenkins cũng từng đánh node offline do Java temporary space dưới1GiB. Sau đó **cả `TMPDIR` và `-Djava.io.tmpdir` chuyển sang `/var/jenkins_home/tmp` trên HDD hiện có**, giữ nguyên ngưỡng, memory2560MiB/CPU1,5 và guard16GiB/15GB cho CI. [Số đo trước/sau](evidence/ci-data-pipelines/python-temp-repair/verification.json), [runtime thực](evidence/ci-data-pipelines/capture/controller-runtime.json). Bootstrap đã sửa idempotence để restart không thêm bản sao tham số job. Client giữ cookie phiên khi dùng crumb CSRF; CSRF vẫn bật. API plugin cần quyền admin nên công cụ capture dùng xác thực riêng, không mở thêm quyền đọc anonymous.

## Tự xem và chạy lại trên VM đã chuẩn bị

1. Mở IAP tunnel loopback18787 tới cùng cổng18787 của VM, rồi vào `http://127.0.0.1:18787/job/iot-dp1-dp3/`. Xem build#2 và Full Stage View; từ danh sách gốc chọn build#2 để xem artifact/parameters.
2. Để chạy lại, đăng nhập tài khoản Jenkins `learner` bằng credential riêng trên VM, chọn **Build with Parameters** và giữ source commit đã niêm phong `c9c29533a57b88f5ed7b4028faf2bff456fdf1ad`. Mật khẩu nằm ở runtime riêng `data/runtime/ci/jenkins-private/bootstrap.json`, không gửi vào chat/Git. Airflow credential đã ở Jenkins Credentials.
3. Build mới tạo workspace/release riêng và chạy cả chuỗi; cần đủ giờ còn lại trước autoSTOP. File dữ liệu/secret/runtime không đi theo clone Git. Source SHA mới phải được xuất từ Git và nhập thành snapshot có manifest trước khi chọn trong job; nhập SHA không có snapshot sẽ bị chặn.

Đây chưa phải SCM webhook. CI materialize/training/model-serving/stream push vẫn là các mốc riêng tiếp theo.
