# Kiểm tải API sau khi thêm telemetry

Ngày28/10/2026 trên VM8CPU/64GiB, Locust2.47.0 đã kiểm **feature API có wrapper OpenTelemetry**, source API2fceb32/image `observe-28b-v2`. GETfeature là workload riêng với prediction; không coi số đo này là predictionTPS hoặc sức tải tối đa.

| Cấu hình | Request cuối | Lỗi | Req/s | P95 | P99 |
|---|---:|---:|---:|---:|---:|
|1user, pacing0,1giây |598 |0 |10,0155 |6ms |7ms |
|10users, pacing0,1giây |5.767 |0 |96,4190 |28ms |44ms |

Cả hai lệnh cấu hình60giây/ramp2user mỗi giây; HTML hiển thị khoảng59giây theo thời điểm sample đầu/cuối. SLA khai báo từ trước:≥100request,0fail,P95≤500ms. Một replica cố định trong phép đo, HPA1–2 được khôi phục sau đó. Máy dùng chung/private network; pacing/ramp và thời gian ngắn giới hạn kết luận. Không dùng số này để nhận tối ưu nhanh hơn source cũ.

- [HTML1user](evidence/observed-api-load-linux/locust-fixed1/users-1.html), [HTML10users](evidence/observed-api-load-linux/locust-fixed1/users-10.html), [verification](evidence/observed-api-load-linux/locust-fixed1/verification.json).
- [Ảnh1user](evidence/observed-api-load-linux/screenshots/locust-1-user.jpg), [ảnh10users](evidence/observed-api-load-linux/screenshots/locust-10-users.jpg): screenshot trình duyệt của HTML gốc, giờ America/Chicago.
- CSV được Locust ghi định kỳ trước aggregate cuối; giữ nguyên CSV/history/HTML/summary, verifier kiểm các thời điểm và không sửa CSV cho bằng số cuối.

## Chuẩn bị và tính đúng

Feature được materialize thật lúc15:18:09UTC: Redis0→200key/minTTL3.578.344ms, seed ConfigMap mới bất biến,200máy/195prediction tính lại đúng công thức và5stopped409. Model6/run be3b/portable60dff giữ nguyên; model7 vẫn candidate. Kiểm tải kết thúc15:27:59UTC trong lease1giờ; không sửa timestamp/TTL. Source workload258beb9 được giữ bằng manifest2file Git và command thực. [Chuẩn bị](evidence/observed-api-load-linux/baseline-refresh/verification.json), [200HTTP](evidence/observed-api-load-linux/baseline-refresh/http-all-machines.json).

Lượt đầu có598/5.772request và0lỗi nhưng guard sau tải phát hiện tập Pod trước/sau khác nhau; **lượt đầu không đạt toàn bộ guard**. Snapshot đều có hai Pod được chọn, một UID thay đổi; HPA đang bật và có thay đổi replica, chưa suy riêng nguyên nhân của mọi thay đổi Pod từ các snapshot này. [Unit lỗi](evidence/observed-api-load-linux/load-unit.txt)/[journal](evidence/observed-api-load-linux/load-journal.txt)/HTML cũ giữ nguyên. Lượt mới giữ một Pod cùngUID/image digest trước và sau hai bài đo; không sửa lượt cũ thành thành công.

Chính sách HPA được patch có testUID/spec rồi trả về1–2 trong finally. [Trước](evidence/observed-api-load-linux/fixed1-control/hpa-before.json), [cố định](evidence/observed-api-load-linux/fixed1-control/hpa-fixed.json), [khôi phục](evidence/observed-api-load-linux/locust-fixed1/hpa-restored.json). Lịch drift Airflow đã trả về trạng thái không pause; file gốc và hash được giữ trong75payload/2ảnh. Đây là kiểm tải vận hành, không phải JenkinsCI mới. [CI telemetry](ci-api-observability.md) được theo dõi riêng.

## Tự kiểm từ clone

```powershell
python -S scripts/verify-observed-api-load.py
```

Không cần VM hoặc cài thêm thư viện. Verifier kiểm byte gốc, workload Git, HTML/SLA/CSV, Pod/image/HPA,195prediction/5stopped, TTL và thời gian lease. Live rerun cần phiên VM đã duyệt, seed mới hợp lệ và thư mục kết quả mới; không chạy lại helper đã tạo state/receipt cũ.
