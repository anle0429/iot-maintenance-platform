# Pin cùng phiên bản model giữa hai API

Predictor và API lấy feature cần thống nhất model nào đang phục vụ. Nếu một bên cập nhật trước, API phải chặn dự đoán thay vì trả điểm kèm thông tin sai phiên bản. [Binding](../services/predictor/binding.py) kiểm đủ9trường: tên, version, training run, protocol, hash weights, training commit, data version, threshold và production flag.

Source **`3b670c6d8f7f51db4b76c7fbe735b130e67e5c69`**, kiểm trên PC **THIENAN/Windows/Python3.13.14**, ngày27/10/2026. Đây là bước chuẩn bị F33/F34; **chưa build image mới, mutation test Linux mới hoặc triển khai lên VM**. Registry thật vẫn chưa được promote trong bước này. Các bản ghi native/image/load trước đây thuộc commit của từng mốc, không chứng minh deployment của mã mới.

## Quy tắc cấu hình

[Predictor](../services/predictor/runtime.py) đọc bundle đã được [exporter](serving-release.md) kiểm hash và lineage. Version lớn hơn1 phải có production flag kiểu boolean thật và receipt chọn registry khớp tên/version/run/URI, có production tag hợp lệ. StageArchived hoặc explicit false bị chặn. Startup còn kiểm thứ tự8feature, kiểu số, scaler và threshold. Không dùng aliascandidate để tự chọn release.

[CLI tạo pin](../scripts/write-model-pin.py) nạp chính bundle bằng predictor rồi ghi config mới, từ chối ghi đè file cũ. [Feature API](../services/feature_api/app.py) đọc file được chỉ định bằng `IOT_MODEL_PIN` lúc startup. File thiếu trường, thừa trường, null hoặc sai kiểu sẽ chặn startup; không quay về cấu hình mặc định. Khi không truyền cấu hình mới, legacy version1/productionfalse giữ hành vi cũ. [Pin legacy](../models/pump-failure-v1/model-pin.json) khớp chính model1 đã kiểm.

Mỗi phản hồi predictor phải khớp đủ9trường; thiếu/sai bất kỳ trường nào trả503. Decision phải phù hợp với threshold được pin. Các kiểm freshness600giây, arrival time, lease3600giây theo giờ thực và máy đang operating vẫn áp dụng. Pin không gia hạn lease và không làm dữ liệu mới hơn.

Pin kiểm tính nhất quán của release đã chuẩn bị trong môi trường tin cậy. Đây chưa là chữ ký/attestation độc lập, và predictor không truy vấn lại nhãn registry trên mỗi request. Khi thu hồi release cần thay cấu hình/deployment; không nhận tag thay đổi tự cập nhật pod đang chạy.

## Kiểm chứng đã chạy

[Bằng chứng](evidence/version-pin-preparation/verification.json) bind36source files bằng cả hash bytes thực thi và hash Git; [manifest](evidence/version-pin-preparation/manifest.json) giữ hash6payload PC. Console và coverage được giữ nguyên byte.

- **220passed/2skipped**, [console](evidence/version-pin-preparation/api-tests.txt). Hai skip là timezone POSIX không có trên Windows, vẫn cần kiểm lại trên Linux. Có fixtures/mock, boundary/partition, Hypothesis và async tests từ suite hiện có cùng54case mới về version pin/HTTP. **416statements/114branches đều100%,0excluded**, [coverage JSON](evidence/version-pin-preparation/coverage.json). HTML cục bộ sinh bằng lệnh dưới; chưa có ảnh current coverage trong mốc này.
- [Hai server HTTP thật trên loopback](evidence/version-pin-preparation/loopback-http.json):8workers/128prediction requests/0fail, cả điểm và decision khớp native Spark golden. Lượt có coverage instrumentation:0,9652giây tổng, p50~43,25ms/p95~301,97ms tính cả client validation. Sau đó9trường định danh lần lượt sai đều bị chặn503. **Feast/context và registry là fixtures**, version2 dùng weights model1 qua exporter thật; không phải live Feast, release registry thật, benchmark VM hoặc phép đo capacity. Không so latency này với Locust native trước đây.
- [CLI công khai](evidence/version-pin-preparation/public-cli.json) chạy bằng subprocess: tạo pinlegacy đúng byte, lần chạy lại bị từ chối và giữ nguyên file. [28unit tests liên quan](evidence/version-pin-preparation/adjacent-tests.txt) của exporter/CI/CAS/version gates cũng PASS. Lệnh đầu dùng nhầm tên module test không tồn tại đã lỗi import; sửa đúng tên và chạy lại, log lỗi giữ local, không nhận lượt lỗi là PASS.
- Chưa chạy mutation cho mã mới. [Cấu hình](../pyproject.toml) chỉ chọn4module API đổi lần này, không mutate cả repository; bao gồm nguyên các module đã thay đổi, không loại survivor hoặc log/exception để tăng điểm. Gate >80% native cũ được giữ đúng scope lịch sử.

## Tự chạy trên bản clone

Môi trường API dùng [dependencies đã khóa](../infra/predictor/requirements-test.txt); Linux có mutmut3.8, Windows chỉ chạy được suite thường. Tại repo root:

```bash
python -m pytest tests/api -q -ra --cov=services.predictor --cov=services.feature_api --cov=services.drift_api --cov-branch --cov-report=term-missing --cov-report=html:outputs/versioned-api-html
python scripts/write-model-pin.py --bundle models/pump-failure-v1 --output outputs/legacy-pin.json
```

File output phải mới. Để chuẩn bị release khác, chạy exporter với versionproduction đã kiểm, rồi tạo pin từ **cùng bundle đó**. Hai Dockerfile nhận input kỹ thuật ở `models/release/`, thư mục này bị Git ignore nhưng được Docker whitelist đúng các JSON cần dùng:

```bash
python scripts/export-production-model.py --tracking-uri http://tracking:5000 --version "$MODEL_VERSION" --output models/release
python scripts/write-model-pin.py --bundle models/release --output models/release/model-pin.json
docker build -f infra/predictor/Dockerfile --build-arg MODEL_BUNDLE=models/release -t iot-predictor:release .
docker build -f infra/feature-api/Dockerfile --build-arg MODEL_PIN=models/release/model-pin.json -t iot-feature-api:release .
```

Đây là lệnh cho bước build tương lai; **chưa chạy build trong mốc này**. Nếu bỏ build arguments, Dockerfile dùng bundle/pinlegacy đã commit. Credentials, dữ liệu lớn và trạng thái runtime không thuộc context whitelist.

Mutation cần hệ có fork theo [tài liệu mutmut](https://mutmut.readthedocs.io/en/latest/). Trên Linux, dùng bản source/test riêng không có cache mutation cũ, dependencies cùng lock và4GiB RAM dành riêng, rồi chạy `mutmut run --max-children 2` với `pyproject.toml` tại root. Giữ kết quả đầu, toàn bộ mẫu số, survivors/timeouts và source hash; chưa dùng score cũ để nhận gate cho mã này.

[Serving CI chuẩn bị trên PC](ci-serving.md) đã nối version pin với gói nguồn và paired stage/switch;37contract tests và207isolated tests PASS/1POSIXskip. Chưa cài job/native build/mutation hoặc A/B router; chốt full cutover từ chối khi chưa có A/B thật. VM đã STOP, câu hỏi phiên tiếp theo vẫn chờ human. Khi có phiên hợp lệ: chuyển gói training đã sửa đường dẫn trước lần Jenkins training đầu, rồi kiểm production export, images, Linux mutation/Locust, A/B và rollout/rollback/HTTP native. F33/F34 vẫn **Đang làm**.
