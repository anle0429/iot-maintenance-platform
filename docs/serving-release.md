# Chuẩn bị gói model từ registry cho serving CI

[Exporter](../ml/serving_release.py) chọn một phiên bản cụ thể của `iot-pump-failure-v1`, kiểm quyền triển khai và các file đã đánh giá, rồi tạo hai file `portable.json`/`lineage.json` làm đầu vào build image. [CLI](../scripts/export-production-model.py) dùng MLflow client; [12 kiểm thử](../tests/test_serving_release.py) dùng registry fixtures nhỏ và portable weights thật của model1.

Đây là **phần chuẩn bị F33/F34**. API, Dockerfile predictor, feature API và KServe hiện chưa được nối vào exporter; chưa có serving CI build/rollout thành công. Model registry và phiên bản đã triển khai trước đó được giữ nguyên.

## Chọn đúng phiên bản và kiểm artifact

`READY` xác nhận phiên bản đăng ký xong. Nhãn `production` điều khiển quyền triển khai. Exporter cần số version dương được pin rõ, đúng model name, statusREADY, `production=true`; cũng nhận stageLegacy `Production` nếu chưa có tag. Tag`production=false` hoặc stage`Archived` luôn chặn. Alias`candidate`/tag`latest` không được tự chọn làm release.

Sau bước chọn, exporter kiểm:

1. Source run FINISHED, protocol SHA bằng protocol đã cố định, có source commit và các SHA của evaluation/selection/data. Nếu hai tên tag source commit cùng tồn tại thì chúng phải đồng ý.
2. Hash file evaluation → selection → temporal split; selection dùng scaler fit trên train, threshold chọn trên validation và holdout chưa mở tại bước chọn model. Portable bytes phải khớp inventory của đúng model đã đánh giá.
3. Đủ8feature đúng thứ tự, scaler/weights/intercept hữu hạn, std không âm và threshold khớp cả validation lẫn evaluation. Model dạng JSON không cần fit lại trong quá trình export.
4. Với dữ liệu lưu tăng dần, model phải trỏ đúng completed data run; hash manifest tải về phải bằng data version và native report phải khớp split. Với cách lưu full-run cũ, native report của split phải khớp training data version. Serving bundle tham chiếu dữ liệu, không chứa tập Parquet huấn luyện.
5. Đọc lại model version và run tags trước khi công bố bundle; thay nhãn/source giữa chừng sẽ chặn export. Directory đích đã có được giữ nguyên. File portable và lineage được ghi vào staging riêng rồi đổi tên thành bundle hoàn chỉnh.

Download kiểm metadata kích thước trước, giới hạn mỗi JSON1MB và tối đa32file lineage phẳng. Đường dẫn traversal hoặc kết quả SDK nằm ngoài thư mục tạm bị từ chối. Hai bản sao byte-identical của split descriptor được nhận cùng một hash: registry cũ có thể lưu cả split gốc và `data-inventory.json`.

`production=true` trong bundle biểu thị nhãn triển khai của môi trường coursework mô phỏng riêng. Score vẫn là logistic có class weights, chưa hiệu chuẩn cho máy bơm thực; xem [phạm vi model](model-serving.md).

## Bằng chứng đang có

Native GET-only lúc **27/10/2026 20:48:09UTC** xác nhận **cả5version READY đều production=false**, alias`candidate→5`. [Inventory](evidence/serving-release-preparation/native-registry/verification.json), [model versions nguyên bản](evidence/serving-release-preparation/native-registry/versions.json). Không đổi tag/alias hoặc tải weights ở lần kiểm này.

Trên PC, replay chính metadata đã thu qua exporter làm **5/5 version bị từ chối trước get_run/artifact access**, không tạo bundle. [Kết quả](evidence/serving-release-preparation/pc-negative-check.json) bind source `1eb4ceb049492f48868b81232a6ad56124c22005` và SHA của metadata. Đây là kiểm cổng chọn trên snapshot thật, chưa phải một lượt download production model qua SDK đang kết nối server.

[12unit tests PASS](evidence/serving-release-preparation/pc-unit-tests.txt): positive fixtures cho legacy/incremental, stage/tag policy, candidate rejection, source/run/protocol mismatch, artifact/manifest corruption, duplicate identical descriptor, marker đổi lúc tải, unsafe/oversized artifact và bundle cũ được giữ nguyên. Các positive fixtures không tự gắn production cho registry thật. Cùng21CI-training tests có33test PASS trên PC; API source không thay đổi từ mốc này, các phép đo coverage/mutation API lịch sử giữ phạm vi riêng.

[Cloud API đọc trên PC](evidence/serving-release-preparation/pc-cloud/vm-state.json) xác nhận VM **TERMINATED**, lastStopTimestamp tương ứng20:51:55UTC, `maxRunDuration=28800s`, actionSTOP. Phiên mới vẫn cần quyền đã hỏi trước đó; không start/extend VM trong bước này. Native registry JSON được chép qua encrypted SCP trước STOP; [manifest](evidence/serving-release-preparation/manifest.json) ghi hash file nhận ở PC, chưa có capsule checksum độc lập đo trên VM cho ba file GET này.

## Tự chạy khi registry đã sẵn sàng

Trong môi trường SDK MLflow3.17.2 riêng đã chuẩn bị, pin version được đánh dấu triển khai và chọn output mới. HTTP registry address dưới đây là service riêng trong Docker network:

```bash
python scripts/export-production-model.py \
  --tracking-uri http://tracking:5000 \
  --version "$MODEL_VERSION" \
  --output "outputs/serving-bundles/version-$MODEL_VERSION"
```

Credentials dùng môi trường/credential manager của CI; URL có user/password hoặc query token bị từ chối. Với snapshot hiện tại chưa có version production, exporter sẽ chặn. Cần quyết định release của môi trường lab, rồi mới gắn nhãn và đọc lại; exporter không thực hiện promotion.

Tiếp theo: cấu hình predictor và feature API pin bundle/version/hash/threshold từ release, giữ kiểm schema/provenance; thêm Jenkins test/build/auto-deploy cho FastAPI và KServe; chạy lại API coverage/mutation phù hợp thay đổi; kiểm actual model download, image contents, rollout/rollback và HTTP predictions trên VM. Chỉ cập nhật F33/F34 hoàn tất khi các bước đó có bằng chứng thật.

Quy ước tag/alias/stage tham khảo [MLflow registry workflow chính thức](https://mlflow.org/docs/latest/ml/model-registry/workflow/); source và số đo bên trên xác định phạm vi dự án đã kiểm.
