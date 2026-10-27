# Phiên bản dữ liệu tăng dần trên MLflow

Mỗi dataset có một manifest đầy đủ, trỏ tới các tệp Parquet bằng SHA256. [Kho content-addressed](../ml/data_versions.py) chỉ ghi blob chưa có. [Publisher MLflow](../ml/mlflow_data_versions.py) upload blob mới vào data run mới, còn phần giữ nguyên tham chiếu data run cũ. Model có thể trỏ tới đúng manifest, không cần upload lại toàn bộ data vào mỗi model run.

## Kết quả đã đo thật

[Kiểm chứng cục bộ](evidence/data-versions/local/verification.json) và [kiểm chứng MLflow](evidence/data-versions/remote/verification.json) đã qua trên dataset Feast thật:

| Trường hợp | Dòng snapshot | Tệp logic | Blob dữ liệu mới | Byte dữ liệu upload mới |
|---|---:|---:|---:|---:|
| Cửa sổ ban đầu59ngày | 3.082.048 | 177 | 177 | 728.157.915 |
| Thêm ngày thứ60 | 3.132.184 | 180 | 3 | 11.845.783 |
| Phát lại cửa sổ60ngày | 3.132.184 | 180 | 0 | 0 |

Đây là thử nghiệm mở rộng cửa sổ59→60ngày trên cùng nguồn bất biến, không phải ba lần sinh nguồn mới. Data run đầu **`7f2cc519f033445198a288d9c5b96dd3`**, run thêm ngày **`49ea21884b9048efb051db5150cff842`**; replay dùng lại run thứ hai. Cả hai run FINISHED. Metadata manifest/locator được tính riêng: lần thêm ngày có61.466byte metadata; replay cũng không upload metadata mới.

Toàn dataset logic60ngày có **740.003.698byte**. Mỗi trường hợp được tải blob từ MLflow thật, dựng kho mới và khôi phục snapshot đầy đủ, kiểm lại mọi byte. Lượt full restore tải740.003.698byte: số upload0 không có nghĩa tổng lưu lượng mạng0. Không dùng cache blob cục bộ làm nguồn của remote restore.

Version60ngày: **`5e78230e8defc2c584d0b97f1e438b65e2659d7358ca44cdda1d6dd95e6db30e`**. [Manifest](evidence/data-versions/manifests/5e78230e8defc2c584d0b97f1e438b65e2659d7358ca44cdda1d6dd95e6db30e.json) liệt kê mọi tệp; remote `locations.json` chỉ rõ run chứa từng blob, nên không phải lần theo chuỗi manifest cha để khôi phục. Blob thuộc các run cũ vẫn cần được giữ.

[Lượt KFP độc lập](evidence/data-versions/kfp-binding/verification.json) đã lấy mới toàn3.132.184dòng, chuẩn hóa180tệp và nhận đúng version ở trên, **0byte blob mới**. Native report/descriptor khác lượt notebook vẫn được giữ riêng cho truy vết.

## Chuẩn hóa và tính đúng đắn

[Canonicalizer](../ml/canonical_dataset.py) đọc source đã seal, kiểm id, nhãn, grain, null/finite và ngày UTC. Nó cố định schema, thứ tự cột, sort id từng ngày và cấu hình Parquet/Snappy; mọi bảng ghi ra được đọc lại đúng giá trị. Manifest không chứa giờ chạy hoặc đường dẫn máy. Vì vậy một lần native Feast lookup khác trả cùng hàng theo thứ tự khác vẫn cho cùng content version.

Phiên này version cả cohort, bảng nhãn hai cột và key map để dễ kiểm toán, nên có chi phí lưu ban đầu và dữ liệu lặp giữa các bảng. Không so footprint này trực tiếp với các split Parquet cũ vốn có schema/phạm vi khác; không nhận đây là bố trí lưu nhỏ nhất. [Report180tệp](evidence/data-versions/canonical/canonical.json) ghi format, byte và SHA thật. Đây là cohort trung gian, không phải versioning100GB.

[Unit oracle](../tests/test_data_versions.py) kiểm thêm/sửa/xóa, replay, restore độc lập manifest cha, path traversal và blob hỏng. [Oracle HTTP thật](evidence/data-versions/remote/oracle.json) dùng JSON nhỏ trong experiment riêng: hai version lưu đúng phần đổi, replay không tạo run mới, dữ liệu khôi phục đúng đáp án. Nó cố tình làm hỏng một blob của fixture rồi kiểm bị từ chối trước restore, sau đó phục hồi đúng byte gốc. Không sửa dataset/model thật. SHA giúp phát hiện artifact bị sửa; không gọi MLflow là kho WORM không thể ghi đè.

## Tự chạy

Từ repo root trên VM có cohort native đã hoàn tất, dùng [CLI](../scripts/version-feast-dataset.py) và [Compose publisher](../infra/data-versions.compose.yaml). Nguồn, canonical output, content store, receipts và restore phải dùng đường dẫn mới/đúng mount; không có trong clone Git.

```bash
# Trong image Feast đã khóa, /ml-data trỏ đến workspace của native pull.
python -m ml.canonical_dataset --cohort /ml-data/cohort.json \
  --protocol /workspace/ml/protocols/medium-60d-v1.json \
  --destination /canonical/dataset --output /proof/canonical.json
python scripts/version-feast-dataset.py --canonical-root /canonical/dataset \
  --canonical-report /proof/canonical.json --store /data-cas \
  --output /proof/version-new.json --parent-version "$PREVIOUS_VERSION"
```

Để publish qua MLflow, đặt `IOT_DATA_CAS_ROOT`, `IOT_DATA_VERSION_OUTPUT`, `IOT_DATA_VERSION_RESTORE` tới các thư mục riêng trên VM rồi chạy publisher image3.17.2:

```bash
docker compose -f infra/data-versions.compose.yaml run --no-deps publisher \
  -m ml.mlflow_data_versions --store /data-cas --version "$DATA_VERSION" \
  --tracking-uri http://tracking:5000 --restore-to /remote-restores/new-run \
  --output /remote-proof/new-run.json
```

Tool thực tế1CPU/4GiB, UID10001, root readonly, chỉ vào Docker network MLflow riêng, không publish cổng. Kho dùng SQLite lock để serialize publication; blob hỏng không tự bị ghi đè. Pending run cùng context có thể được kiểm lại, còn receipt/restore hoàn tất không chạy đè.

[Model/data gate](../ml/model_data_link.py) kiểm protocol, nguồn, native report, input descriptor và manifest của chính split dùng train; cùng protocol nhưng khác native pull vẫn bị chặn. [CLI đăng ký model](../ml/register_incremental_model.py) và [source snapshot](../scripts/create-source-snapshot.py) chuẩn bị bước nối model với data run; tới lúc tài liệu này được tạo, model mới chưa được đăng ký bằng CLI này. Còn tích hợp thành bước tự động sau mỗi pull trong pipeline/CI.

API MLflow dùng theo [tài liệu chính thức](https://mlflow.org/docs/latest/api_reference/python_api/mlflow.client.html). [Manifest37tệp native](evidence/data-versions/capture/manifest.json) và [mapping](evidence/data-versions/capture/evidence-map.json) giữ các receipt, canonical hashes và metadata tải từ server; bulk blobs, runtime index và secrets được giữ ngoài Git.
