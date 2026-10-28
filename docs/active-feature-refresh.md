# Làm mới feature sau phiên huấn luyện

Sau khi model7 huấn luyện xong, feature API của model6 đã hết lease một giờ. Ngày28/10/2026, tôi materialize lại **200máy** bằng Feast thật, tạo context/ConfigMap bất biến mới rồi rolling update cùng image/model6. Model7 vẫn là ứng viên `production=false`; đây không phải lần thăng cấp model.

[Receipt native](evidence/active6-feature-refresh/verification.json) tại **06:33:23UTC**: API Ready, **195dự đoán khớp phép tính logistic độc lập, sai lệch lớn nhất0**;5máy dừng trả409. Cache có200key, TTL nhỏ nhất3.074.042ms tại kiểm chứng. Context được tạo thực lúc06:24:59.606873UTC, hết lease sau một giờ; API sẽ từ chối khi hết hạn. Đồng hồ nghiệp vụ mô phỏng29/11/2026 tách biệt đồng hồ vận hành28/10/2026.

Nguồn chạy `99d6dcc2994f55f06133eace53ecff523f6fc3cb`, VM8CPU/64GiB, container materialize1CPU/8GiB. Chỉ dùng dữ liệu medium có sẵn và Redis hiện có. Không sinh lại100GB, sửa timestamp/TTL, đổi ngưỡng0,7 hoặc ghi đè state cũ. Image API/init vẫn SHA`c35ae82be5c564761e721f2c9d4bc838067ad93d7fd55988b224ea47a5504e02`; UID KServe/model6 và Deployment giữ nguyên.

## Lỗi được giữ lại

Lượt kiểm đầu đã dùng sai phép so sánh toàn bộ `reply['model']==pin`: phản hồi có đủ9trường định danh nhưng còn3trường mô tả hợp lệ. Nhánh phục hồi sau đó chọn seed cũ đã hết lease và [Helm rollback timeout](evidence/active6-feature-refresh/original-recover-original-on-failure.txt). [Ghi chú lỗi](evidence/active6-feature-refresh/original-failure-scope.json) nói rõ phản hồi HTTP đầu không được lưu; không có traceback hoặc receipt thành công được dựng lại.

Lượt kiểm sửa đối chiếu đủ9định danh, kiểm riêng3trường mô tả và tính lại xác suất cho từng máy. Nó dùng **chính state/ConfigMap mới đã materialize**, không chạy lại writer. Các phản hồi HTTP được lưu dần trước assertion để giữ dữ liệu khi lỗi. Sau rollback đã kết thúc mới upgrade lại cùng seed/image. [Log materialization](evidence/active6-feature-refresh/original-materialization.txt), TTL trước/sau, toàn bộ200HTTP và readback Helm/Pod được giữ nguyên bytes trong manifest24payload. Bằng chứng CI8 và F51 trước đó không bị viết lại.

## Tự kiểm trên máy đã clone

```powershell
python -S scripts/verify-active-feature-refresh.py
```

Lệnh chỉ dùng thư viện chuẩn và Git: kiểm hash24payload/75file nguồn lịch sử, seed seals,200HTTP, phép tính model6, physical TTL, Pod/image/UID và lỗi rollback đã giữ. Không cần VM hoặc dữ liệu lớn. `git pull` không chuyển cache/registry đang chạy sang laptop.

Khi demo đã hết lease, cần materialize một namespace/context mới từ nguồn đã bind rồi triển khai seed mới theo [quy trình feature API](feature-api.md); không sửa đồng hồ hoặc kéo TTL để làm cũ thành mới. Lệnh kiểm offline ở trên xác nhận phép đo lịch sử, không khẳng định endpoint hiện tại luôn sẵn sàng.
