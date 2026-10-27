# Thiết kế lớp ở các biên của vòng đời ML

Năm lớp dưới đây đang có trong mã và được dùng ở các mốc đã chạy. Mỗi lớp giữ một trách nhiệm: lưu snapshot, publish dữ liệu, nạp model, lấy feature để suy luận, hoặc đo drift. Split/train/label vẫn là các hàm Spark có protocol cố định; tài liệu này không biến chúng thành lớp chỉ để tăng số lớp.

| Lớp và mã | Giao diện chính | Đầu vào/đầu ra và trách nhiệm | Kiểm chứng |
|---|---|---|---|
| [ContentAddressedDatasetStore](../ml/data_versions.py) | `publish(files, metadata, parent_version=None)`, `manifest(version)`, `restore(version, destination)` | Tệp canonical và metadata→version SHA/manifest đầy đủ. Chỉ lưu blob mới; kiểm hash/path và serialize publication bằng lock. Không chịu trách nhiệm train hay ghi MLflow. | [59→60ngày/replay/restore](incremental-data-versioning.md), unit oracle thêm/sửa/xóa/blob hỏng. |
| [MlflowDatasetPublisher](../ml/mlflow_data_versions.py) | `publish(version, restore_to)` | Version cục bộ→completed data run cùng locator cho mọi blob. Reuse run chứa blob cũ; kiểm tải từ server rồi restore vào kho mới trước khi seal. Không upload thêm một full dataset vào mỗi model run. | [Native MLflow](evidence/data-versions/remote/verification.json),177→3→0blob upload, corruption fixture. |
| [PortablePumpModel](../ml/portable_model.py) | `load_context(context)`, `predict(context, model_input, params=None)` | Artifact scaler/logistic đã fit và DataFrame tám feature→risk score/quyết định theo threshold đã chọn. Không fit lại hoặc dùng nhãn khi suy luận; từ chối cột sai/thứ tự sai. Là adapter theo giao diện MLflow `PythonModel`. | [Registry model3](evidence/data-versions/registry/registration.json),200predictions so Spark sau khi nạp registry. |
| [FeatureRuntime](../services/feature_api/runtime.py) | `features(machine)`, `predict(machine)` | Machine ID, context và backend→feature trace hoặc prediction. Kiểm đồng hồ/lease/arrival/operating state trước khi gọi model; phân biệt404/409/503 qua API. Không tự biết chi tiết Feast protobuf hay KServe transport. | [Feature API](feature-api.md),200máy/195prediction, freshness/availability tests và native load. |
| [DriftDetector](../services/drift_api/detector.py) | Khởi tạo bằng reference bundle; `compare(window)` | Reference train cố định và cửa sổ feature→PSI từng feature/alert theo policy. Không nhận label hoặc suy ra accuracy. Việc lưu cửa sổ/idempotency thuộc `DriftRuntime`. | [Drift guide](drift-detection.md), PSI tính tay, HTTP control/shift/replay,172statement/40branch100%. |

Luồng liên kết: native Feast pull tạo cohort và canonical files; store/publisher version dữ liệu; training tạo model; model/data gate gắn đúng data run vào registry. Khi phục vụ, `FeatureRuntime` gọi backend; `PortablePumpModel` là implementation registry đã so khớp với Spark, còn image predictor dùng cùng phép tính portable qua `PumpPredictor`. Drift là một phép đánh giá feature riêng. Hiện serving pin model1, reference drift gắn model3 candidate; chưa gán lineage của hai version cho nhau.

## Pattern và ranh giới phụ thuộc

**Adapter:** [FeastKServeBackend](../services/feature_api/runtime.py) đổi native Feast response và KServe HTTP thành hai thao tác `fetch(machine)`/`predict(features)` mà `FeatureRuntime` dùng. Tests có backend fixture cùng giao diện để kiểm nghiệp vụ bằng đáp án biết trước, còn integration riêng kiểm transport thật.

**Dependency injection:** runtime nhận backend và clock từ ngoài; `create_app(runtime=...)` nhận runtime fixture khi test. Production factory tạo backend thật từ runtime config. Vì vậy test mô phỏng lỗi upstream, timezone hoặc lease mà không cần sửa trạng thái dịch vụ đang chạy.

**Factory function:** [create_app](../services/drift_api/app.py) tạo một ứng dụng, khởi tạo/đóng runtime theo lifespan. Đây là factory ở cấp hàm, không gọi nhầm là GoF Factory Method dựa trên kế thừa.

![Factory function và runtime injection trong HTML coverage native](evidence/drift-api/screenshots/factory-dependency-injection.jpg)

Ảnh chụp source được coverage tool xuất sau lượt native40test. [Bản HTML gốc](evidence/drift-api/mutation/coverage-html/z_da970418c10ed19b_app_py.html) và source hashes trong [manifest](evidence/drift-api/capture/manifest.json) cho phép đối chiếu. Mỗi factory có runtime riêng; endpoint async đưa tác vụ đồng bộ ra thread và giới hạn concurrency, thay vì giữ event loop chờ SDK/SQLite.

Các lớp không tự mở cloud resource, promote model, đổi protocol hoặc quyết định retrain. Những thao tác đó thuộc orchestration/policy riêng và cần có receipt tương ứng khi được triển khai.
