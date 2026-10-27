# Đối chiếu ý tưởng bổ sung với nội dung lớp

Người dùng cung cấp hai lịch **EDAI-K9** và **DSAI-K9** ngày27/10 để đối chiếu nội dung đã học. Chúng là tài liệu tham chiếu nội dung; chuẩn chấm vẫn là **K11 final-coursework**, không thay bằng lịch K9. Workbook gốc giữ nguyên ngoài repo. [Snapshot các ô liên quan và SHA256](evidence/course-scope/source-summary.json) không chứa recording URL, học phí hoặc thông tin giảng viên.

| Nguồn | Ô đã kiểm | Nội dung có trong lịch | Ảnh hưởng tới lựa chọn |
|---|---|---|---|
| EDAI, `EDAI-1 (Data Engineering)` | C31:C32 | Spark/AQE/shuffle/skew/dynamic allocation; Flink/window/late data/memory tuning | Tối ưu tài nguyên nói chung đã thuộc nội dung lớp. |
| EDAI, `EDAI-2 (ML Engineering)` | C28; C31 | Kubernetes resource management; quota/budget/request prioritization | Không nhận quản lý tài nguyên/ngân sách nói chung là ý tưởng mới. Admission theo pilot vẫn hữu ích cho vận hành dự án. |
| EDAI, `EDAI-2 (ML Engineering)` | C33:C34 | DVC/MLflow/versioning, observability và drift | Versioning hoặc drift nói chung chưa đủ tính bổ sung. |
| DSAI, `DSAI-1 (Data Science & Machine ` | D24; D28:D29 | Hypothesis testing, covariance/correlation, feature evaluation, kernel methods | Không nhận kiểm định thống kê hoặc kernel nói chung là mới. |
| DSAI, `ML` | E9; E16 | Data drift/incremental learning; t-test/chi-square/ANOVA | Cần kỹ thuật cụ thể và một phép thử cho thấy giá trị bổ sung. |

Người dùng trước đó đã xác nhận availability guard và replay reconciliation đã học. Giữ hai cơ chế này vì tính đúng đắn, không nhận là hai novel ideas.

MMD chưa được nêu đích danh trong bất kỳ ô không rỗng nào của hai workbook. Đây là **ứng viên** để phát hiện thay đổi quan hệ giữa cảm biến khi các phân bố biên giữ nguyên, bổ sung cho PSI hiện có. Sự vắng mặt trong lịch không chứng minh chưa từng được giảng; chưa đánh dấu mục này có bằng chứng triển khai hay đã đáp ứng tính ngoài lớp.

Hai mục novel ideas trong tracker vẫn chưa hoàn tất. Bước lựa chọn tiếp theo cần gắn kỹ thuật cụ thể với hạn chế thật của hệ thống, phép thử có đáp án và phạm vi nội dung lớp; không thêm công nghệ chỉ để đủ số lượng.
