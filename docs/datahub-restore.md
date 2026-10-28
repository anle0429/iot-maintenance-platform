# Phục hồi DataHub, giữ nguyên metadata

Ngày28/10, năm container DataHub không khởi động vì tham chiếu network Docker đã mất sau STOP/START. Đã phục hồi MySQL, OpenSearch, Kafka broker, GMS và frontend trên bridge private hiện có`iot-metadata-restored-28b`. [Receipt](evidence/datahub-restore-linux/restore-v2/verification.json): cùng image ID và **tất cả mount cũ**, kể cả anonymous Kafka volume; không SystemUpdate/reset DB/đổi credentials hay volume thay thế. GMS18080/frontend19002 vẫn bind127.0.0.1.

Container cũ giữ hậu tố`-before-session-28d`. Lượt đầu dừng sau MySQL vì so Config.Image tag với image ID. Đọc lại xác nhận chỉ trường Image khác; lần tiếp dùng MySQL đang healthy và khởi động bốn service còn lại. [Before state](evidence/datahub-restore-linux/before.json), [mount receipt](evidence/datahub-restore-linux/restore-v2/progress.json), [Config digest parity](evidence/datahub-restore-linux/config-parity.json). Config đầy đủ có secrets chỉ giữ riêng trên VM. Hash attest phép so sánh native sau chuẩn hóa Image; capsule không cho phép tự tính lại toàn Config bí mật.

GMS health200/frontend login200. GraphQL đọc lại hai dataset compactParquet/Redis của`feast-incremental-27`, còn upstream feature, producing Airflow job và sáu assertion runEventSUCCESS. [Indexed proof](evidence/datahub-restore-linux/indexed/verification.json), [input](evidence/datahub-restore-linux/indexed/input.json), [output](evidence/datahub-restore-linux/indexed/output.json). Đây là metadata cũ còn nguyên, không phải publication/materialization mới. [Ảnh và luồng gốc](feast-materialization.md) giữ riêng; mốc này chưa có ảnh UI DataHub mới.

```powershell
python -S scripts/verify-datahub-restore.py
```

Chín payload49710byte/8nativefile/native manifest, verifier không cần credentials/VM. [Manifest](evidence/datahub-restore-linux/manifest.json). Native support tar chungSHA`c78ad198693aac3496ecfddf624d3565392970b44a07e5c4222830ace5daff10`; scan sáu giá trị secret thực và encoding không thấy trùng. Helpers/privateConfig không publish. VM đã phục hồi: không rename/recreate lại để xem bằng chứng. Compose cũ còn trỏ network cũ, phải kiểm trạng thái và dùng override phù hợp trước thao tác sau. UI qua IAP cần đăng nhập bình thường.
