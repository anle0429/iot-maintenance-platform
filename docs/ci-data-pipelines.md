# CI/CD cho DP1–DP3

[Workflow GitHub Actions](../.github/workflows/data-pipelines.yml) chạy trên VM riêng hiện có. Người học chọn **Run workflow** ở nhánh `main`; từ đó test, build, deploy và kiểm chứng đều tự thực hiện. Đây là trigger thủ công, chưa có webhook cho mỗi push. Repo giữ private; workflow không nhận mã pull request vào máy có quyền Docker.

## Luồng thực hiện

1. Chạy 34 tests hiện có về generator theo chunk, source receipt, replay, corruption và artifact boundaries. Sau triển khai còn chạy các rule Spark trên dữ liệu đã lưu.
2. Đóng gói code `generator/` và `pipelines/` từ đúng Git commit vào hai image Spark và DataHub. Chạy container độc lập đọc lại hash toàn bộ source trong image, so với checkout đã test; label Git commit riêng chưa đủ chứng minh điều này.
3. Tạo release mới theo `GitHub run ID + attempt`, không ghi đè output lần cũ. Tự deploy [ba DAG CI](../infra/ci/iot_ci_data_pipelines.py) vào thư mục runtime riêng, giữ DAG gốc.
4. Gọi Airflow thật lần lượt DP1 → DP2 → DP3. Mỗi DAG phải hoàn tất ingest, validation và publish DataHub; observer đọc lại task state, report validation, metadata readback và đúng image ID đã build. Bước sau chỉ bắt đầu khi bước trước đạt.
5. Upload các receipt nhỏ dù có lỗi; không đưa Parquet, token, mật khẩu hoặc state lên artifact/Git. Khi lỗi/hủy, chỉ dừng container mang label của đúng release đó.

[Controller](../scripts/ci-data-pipelines.py) dùng pilot liên tục **20 máy × 48 giờ**, nguồn JSONL bất biến đã kiểm trước. Đây chưa phải CI xử lý100GB hoặc CI model/API production. Dữ liệu và output riêng nằm trong `data/ci/releases/<release>/work/`; scratch trên SSD hiện có. Spark giới hạn2CPU/5GiB, heap3GiB; metadata1CPU/2GiB. Pool Airflow một slot tránh các job Spark tranh RAM. Admission cần còn40phút trong phiên VM đã duyệt,16GiB RAM khả dụng và15GB trống trên mỗi đĩa.

## Runner và secrets

[Runner chính thức đã khóa version/hash](../infra/ci/runner-version.json) chỉ nhận một job rồi tự hủy đăng ký (`--ephemeral`). Runtime dùng user riêng `iot-ci`, không cài dịch vụ tự chạy sau mỗi boot. Unit được giới hạn thời gian/tài nguyên và cấp nhóm Docker trong đúng phiên unit. Docker socket có quyền mạnh trên host; cơ chế này dành cho mã `main` tin cậy của repo private, chưa phải môi trường nhiều người không tin cậy.

`IOT_AIRFLOW_PASSWORD` nằm trong GitHub Actions secret, chỉ đưa vào ba bước gọi API; controller không ghi access token hoặc response đăng nhập vào log. Checkout dùng `contents: read`, không giữ Git credential. Registration token của runner có thời hạn ngắn, dùng xong xóa bản truyền tạm; không truyền GitHub CLI token dài hạn lên VM. Hai action được pin bằng commit SHA. Xem hướng dẫn chính thức về [runner private](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners) và [bảo vệ workflow/secrets](https://docs.github.com/en/actions/reference/security/secure-use).

## Trạng thái kiểm chứng

Ngày27/10: source compile và **34 tests qua trên PC**. Runner archive tải từ GitHub chính thức đã khớp SHA, `config.sh --help` chạy được trên VM; chưa đăng ký hoặc nhận job tại checkpoint chuẩn bị. **Chưa có run CI/CD thành công** ở checkpoint này. Kết quả native, ảnh Actions/Airflow và commit thực chạy sẽ bổ sung sau khi workflow kết thúc; không dùng file YAML để đánh dấu F30–F32 đã hoàn tất.
