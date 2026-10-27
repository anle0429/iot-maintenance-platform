# CI/CD cho DP1–DP3

[Jenkinsfile](../infra/ci/Jenkinsfile) chạy trên VM riêng hiện có: **Test → Build verified images → Auto-deploy Airflow DAGs → DP1 → DP2 → DP3**. Người học khởi chạy một commit đã niêm phong; các bước sau tự thực hiện. Chưa có webhook cho mỗi push. Nguồn Git được chuyển qua IAP thành snapshot có SHA của từng file, không cấp GitHub token dài hạn cho máy chạy CI.

## Luồng kiểm chứng

1. Chạy34tests hiện có về generator theo chunk, source receipt, replay, corruption và artifact boundaries. Sau deploy còn chạy các rule Spark trên dữ liệu đã lưu.
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

## Trạng thái

Checkpoint27/10: compile và34tests PC đã qua; Jenkins source đang chuẩn bị chạy thật. Chưa đánh dấu F30–F32 có bằng chứng chỉ từ code/config. Native receipts, ảnh stage và commit thực chạy sẽ bổ sung sau khi hoàn tất.
