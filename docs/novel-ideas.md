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

MMD chưa được nêu đích danh trong bất kỳ ô không rỗng nào của hai workbook. Đây là **ứng viên** để phát hiện thay đổi quan hệ giữa cảm biến khi các phân bố biên giữ nguyên, bổ sung cho PSI hiện có. [Audit MMD đã triển khai và kiểm trên PC](joint-drift.md):19tests,6caCLI và verifier độc lập1.194permutationstatistics; hai ca joint-shift có mọiPSI=0 nhưng phát tín hiệu. Ca phi tuyến còn giữ covariance=0. Policy cố định trước thí nghiệm; có đầu vào/report/hình và giới hạn inference. Sự vắng mặt trong lịch không chứng minh chưa từng được giảng; F58 vẫn cần xác nhận ngoài lớp, chưa nhận hoàn tất tiêu chí hoặc đãdeploynative.

Đã đọc lại hai file gốc và đối chiếu snapshot:281ô có nội dung ở EDAI,388ô ở DSAI,11ô trích dẫn liên quan khớp từng giá trị/SHA256; cả hai file giữ nguyên byte. Tổng669ô không thấy tênMMD/maximum mean discrepancy. Nội dung lịch chỉ là nguồn tham chiếu, không trở thành lệnh triển khai hay thay đổi rubric đã được người dùng chốt.

Hai mục novel ideas trong tracker vẫn chưa hoàn tất. MMD đã có proof nhỏ về giá trị bổ sung; còn xác nhận ngoài lớp và bước dùng cohort thực có lineage. Ý tưởng thứ hai vẫn cần chọn theo hạn chế thật của hệ thống và kiểm chứng cụ thể; không thêm công nghệ chỉ để đủ số lượng.
