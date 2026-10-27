# Phát hiện thay đổi phân bố cảm biến

API drift nhận một cửa sổ feature và so với tham chiếu huấn luyện đã cố định. Ví dụ cảm biến nhiệt bị lệch20°C và cảm biến rung nhân1,8 sẽ làm phân bố đầu vào thay đổi. Kết quả là tín hiệu cần điều tra dữ liệu; không phải bằng chứng model dự đoán sai hay máy chắc chắn sắp hỏng.

## Thiết kế và tính đúng đắn

[DriftDetector](../services/drift_api/detector.py) dùng PSI cho tám feature đã cho phép trong model. Mười bin được chọn từ quantile của reference, bỏ các mốc trùng; hai đuôi không bị cắt nên giá trị vượt khoảng huấn luyện vẫn được đếm. Feature hằng có hai bin ở giá trị hằng. Mỗi bin cộng0,5 trước khi chuẩn hóa, tránh chia0:

`PSI = Σ (q_i − p_i) log(q_i / p_i)`

Policy hiện tại: feature có PSI≥0,2 thì được gắn cờ; ít nhất25% feature bị gắn cờ thì cửa sổ có cảnh báo. Đây là ngưỡng vận hành khai báo trong artifact, không phải xác suất sai hay p-value đã hiệu chỉnh. [Tài liệu PSI của Minitab](https://support.minitab.com/en-us/model-ops/monitor-deployed-models/details-about-the-population-stability-index-psi/) giải thích phép đo; [Evidently](https://docs-old.evidentlyai.com/user-guide/customization/options-for-statistical-tests) cho phép chọn ngưỡng theo trường hợp. Dự án tự cài kernel nhỏ, không tuyên bố đã cài/deploy Evidently.

[Reference builder](../scripts/build-drift-reference.py) chỉ chọn1.024vector từ **train** của split đã seal, theo hash khóa máy/thời điểm, không chọn theo nhãn. Nó kiểm liên kết giữa model3, native Feast pull, protocol và data version trước khi đọc. Reference chứa tám feature cùng lineage, không có label; reference ID là SHA256 của JSON chuẩn hóa. Ngưỡng không được chọn lại theo holdout.

[CompareRequest](../services/drift_api/contracts.py) yêu cầu32–2.048quan sát duy nhất, snapshot nằm trong `[start,end)` và đã sẵn sàng trước hoặc đúng `end`. Feature thừa/nhãn, NaN/Inf, timestamp thiếu múi giờ và trùng ID khác nội dung bị từ chối. Tên cửa sổ và reference ID được kiểm định. Thời gian được chuẩn hóa UTC trước khi băm; thứ tự hàng và duplicate giống hệt không làm đổi định danh.

[DriftRuntime](../services/drift_api/runtime.py) lưu đánh giá trong SQLite trên volume riêng. Transaction kiểm rồi ghi bảo đảm nhiều worker gửi cùng cửa sổ chỉ tạo một bản. Cùng window ID nhưng dữ liệu khác trả409; reference khác trả409; đầy10.000cửa sổ trả507 để giữ lịch sử. Không tự xóa kết quả. Replica trên cùng node chia sẻ local PVC; đây chưa phải thiết kế nhiều node hay high availability.

[FastAPI](../services/drift_api/app.py) có `/health/live`, `/health/ready` và `POST /v1/drift:compare`. Ready kiểm cả kho trạng thái. Tác vụ CPU/database được đưa ra worker thread, tối đa4tác vụ mỗi pod. Yêu cầu POST cần Content-Length và tối đa1MiB; giới hạn byte có thể chặn trước giới hạn số hàng. Không nhận chunked request. [FastAPI async](https://fastapi.tiangolo.com/async/) và [SQLite connection/transaction](https://docs.python.org/3.12/library/sqlite3.html) là tài liệu API được tham khảo.

## Generator có cấu hình drift

[SensorDrift](../generator/drift.py) bọc simulator nhỏ hiện có. Nó thay hiệu chuẩn nhiệt/rung sau `start_ts`, theo bước hoặc ramp; quá trình hao mòn, bảo trì, sự cố và thời gian đến giữ nguyên. Mọi retry nhận cùng phép biến đổi theo event time. Telemetry ID được thêm namespace kịch bản để không lẫn hai nguồn có cùng khóa nhưng khác số đo. Các file generator đã chạy100GB giữ nguyên byte.

[Cấu hình nhỏ](../generator/configs/sensor-drift-small.json) có20máy/48giờ,57.600reading duy nhất mỗi kịch bản. Drift bắt đầu ngày thứ hai, ramp1giờ, nhiệt+20°C, rung×1,8. Lệnh tạo nguồn mới:

```bash
python -m generator.drift --config generator/configs/sensor-drift-small.json \
  --output data/source/sensor-drift-new
```

Không chạy đè thư mục đã có. Đây là generator drift nhỏ, chưa phải drift theo chunk hay dữ liệu drift100GB. [Spark verifier](../scripts/verify-sensor-drift.py) chạy hai nguồn baseline/drift qua chính kernel feature có kiểm event/arrival time và kernel nhãn; ghi bảng đúng hai cột`id,label`, join lại feature và so toàn bộ dữ liệu đọc lại. Khoảng so sensor phải bắt đầu sau cả ramp và lookback10phút. Scratch cần cho phép nạp thư viện Snappy; root filesystem vẫn readonly và `/tmp` vẫn noexec.

## Tự kiểm tra

Ngày27/10/2026 đã chạy thật trên VM8CPU/64GiB: [HTTP native](evidence/drift-api/http/http.json), [Spark nhỏ](evidence/drift-api/generator/generator-verification.json), [reference train](evidence/drift-api/reference/reference.verification.json) và [Helm/rollback](api-deployment.md).

Hai nguồn có57.600reading duy nhất mỗi nguồn. Sau censoring/downtime và kiểm có reading đã đến, mỗi bên có **10.373hàng feature/nhãn eligible**, bảng nhãn chỉ`id,label`; mọi giá trị đọc lại và join đều khớp. Nhãn hai bên giữ nguyên. Trên4.828hàng sau ramp/lookback, sai số của mức dịch nhiệt20°C lớn nhất4,27×10⁻¹⁴; sai số rung×1,8 lớn nhất0,0004 do làm tròn ba chữ số. Đây là kiểm hiệu ứng cảm biến, không đo accuracy.

![Cấu hình generator được notebook đọc nguyên bản](evidence/drift-api/screenshots/generator-configuration.jpg)

![Các hàng id/label/feature đã merge của baseline và drift](evidence/drift-api/screenshots/generator-merged-label-features.jpg)

[Notebook3ô đã execute](evidence/drift-api/notebook/executed.ipynb) đọc đúng config và report Spark đã hoàn tất, không sinh nguồn lại. [HTML gốc](evidence/drift-api/notebook/executed.html)/[receipt](evidence/drift-api/notebook/verification.json) giữ output; source ở [notebook](../notebooks/02_sensor_drift_verification.ipynb). Các ảnh là ảnh trình duyệt chụp HTML gốc.

Reference API gồm1.024vector lấy từ2.195.668hàng train, reference ID`ac366302a409d2a6a0f7f0442f8aab42c882f81310d072908b9826159b758e93`. HTTP so reference với chính nó có PSI0; đổi đúng hai cảm biến thì alertfraction0,25. Replay cùng payload đảo thứ tự/thêm duplicate không tăng số cửa sổ; ID đụng nội dung/reference khác trả409, nhãn/thời gian sai/sample31 trả422, sample32 được nhận. Probe dùng timestamp mô phỏng khai báo, không nhận là dữ liệu vận hành mới đến.

```bash
python -m pip install -r infra/drift-api/requirements-test.txt
python -m pytest tests/api/test_drift_api.py tests/test_sensor_drift.py \
  --cov=services.drift_api --cov-branch --cov-report=html
```

Chạy trong venv test riêng. Với Spark trên VM, [command JSON đã thực thi](evidence/drift-api/generator/command.json) giữ đủ image, mount readonly, UID, RAM/CPU và vị trí Snappy/scratch. Khi chạy lại, đổi tên container, thư mục output và scratch sang đường dẫn mới; kiểm RAM/đĩa/thời gian còn lại của phiên trước. Không chạy đè các thư mục evidence/dataset đã hoàn tất.

[Tests](../tests/api/test_drift_api.py) có đáp án PSI tính tay, biên histogram, kích thước sample, timestamp/availability, lỗi HTTP, kho bền qua nhiều worker và Hypothesis đổi thứ tự/duplicate. [Test generator](../tests/test_sensor_drift.py) kiểm ramp/bước và giữ nguyên truth. Mutation dùng [cấu hình riêng](../infra/drift-api/mutation.toml) trong bản copy Linux tách biệt; chỉ mutate API drift mới, không mutate lại predictor/feature API không đổi.

Native driftAPI40test qua,172statement/40branch đều100%. [Suite API kết hợp](evidence/drift-api/combined/verification.json) chạy cả predictor, feature và drift: **168test,383statement/96branch100%**; mocks/fixtures/property tests và SQLite được phân biệt với các integration HTTP thật ở trên. PC chạy API drift+generator48pass/1POSIXskip, không nhận là kết quả Linux.

![Coverage gốc của API drift](evidence/drift-api/screenshots/native-coverage.jpg)

[Mutmut3.8 vòng đầu](evidence/drift-api/mutation-initial/verification.json) có287/383killed, **74,93%**, chưa đạt. Sau khi thêm kiểm định danh JSON ổn định, sample reference ở biên, thư mục trạng thái mới, timezone và thông báo lỗi có ngữ nghĩa, [vòng hai](evidence/drift-api/mutation/verification.json) đạt **320/383=83,55%**, phát hiện thêm33mutant. Cả383đều chạy lại,0excluded/timeout/uncovered/suspicious;63survivor và mọi diff vẫn giữ trong mẫu số. Không coi mọi survivor còn lại đều tương đương. Pydantic class declarations nằm ngoài khả năng mutate function-body của tool.

![Dòng kết quả cuối trong log mutmut gốc](evidence/drift-api/screenshots/native-mutmut-console.jpg)

Đây là log gốc được mở với headerUTF-8; payload không thay đổi. [Console](evidence/drift-api/mutation/mutmut-console.txt), [surviving diffs](evidence/drift-api/mutation/surviving-diffs.json), version dependencies và SHA của source/test đều có trong evidence. Ngưỡng mutation của predictor/feature API đã kiểm ở [feature guide](feature-api.md); không chạy lại code không đổi trong vòng mutation mới.

Để kiểm HTTP trên service đang Ready, dùng [CLI](../scripts/verify-drift-api.py), truyền reference thật trên VM và thư mục output mới. CLI so reference với chính nó, thêm độ lệch có kiểm soát, replay và gửi các yêu cầu sai; timestamp của các probe là fixture mô phỏng, không gọi là telemetry hiện tại.

## Giới hạn và phần còn lại

Reference hiện gắn **model3 candidate**. Feature API đang phục vụ **model1**; chưa gán reference/model3 cho dự đoán model1. So từng phân bố biên chưa phát hiện mọi thay đổi tương quan, concept drift hoặc chất lượng dự đoán. Mẫu1.024hàng là cách giới hạn chi phí, không chứng minh đủ đại diện cho mọi chế độ vận hành. Cần thêm giám sát cửa sổ theo thời gian, dashboard/Airflow và policy retraining; không tự huấn luyện lại hoặc promote chỉ vì PSI vượt ngưỡng.

Hai API đã qua [Helm rolling update và atomic fallback thật](api-deployment.md), sau khi sửa một timeout ở lần đầu. HPA đang tắt; autoscale/CI/Airflow drift còn cần kiểm riêng. [Manifest native](evidence/drift-api/capture/manifest.json) gắn148member trong capsuleSHA`f7f7d5585cfb16b3ac54b5b28fb7baf4bc1715029de659e95e95c17da5810711`; [mapping](evidence/drift-api/capture/evidence-map.json) công khai146nativefile và nêu riêng file ignore do coverage sinh không cần publish. Suite kết hợp/ảnh được capture bổ sung, có manifest riêng.
