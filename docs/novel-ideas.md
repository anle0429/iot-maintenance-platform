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

MMD phát hiện thay đổi quan hệ giữa cảm biến khi các phân bố biên giữ nguyên, bổ sung cho PSI hiện có. [Phép thử controlled trên PC](joint-drift.md):19 tests,6 ca CLI và verifier độc lập1.194 permutation statistics; hai ca joint-shift có mọi PSI=0 nhưng phát tín hiệu. Ca phi tuyến còn giữ covariance=0. Policy cố định trước thí nghiệm; có đầu vào/report/hình và giới hạn inference. Sự vắng mặt trong lịch không được dùng để tự khẳng định chưa từng được giảng; xác nhận trực tiếp của người dùng ngày28/10 được lưu riêng bên dưới.

Đã đọc lại hai file gốc và đối chiếu snapshot:281ô có nội dung ở EDAI,388ô ở DSAI,11ô trích dẫn liên quan khớp từng giá trị/SHA256; cả hai file giữ nguyên byte. Tổng669ô không thấy tênMMD/maximum mean discrepancy. Nội dung lịch chỉ là nguồn tham chiếu, không trở thành lệnh triển khai hay thay đổi rubric đã được người dùng chốt.

Hai ý tưởng đã chọn là MMD và OPA/Rego. Mỗi ý tưởng có mục đích cụ thể, đầu vào biết đáp án và kiểm chứng native riêng; không nhận drift/kernel hoặc quản lý ngân sách nói chung là kỹ thuật ngoài lớp.

## Bằng chứng bổ sung ngày28/10

[MMD native cohort](joint-cohort.md) đã chạy trên64 train reference và64 máy trong genuine gateway capture, có lineage, direct oracle/permutations/replay; đây là diagnostic lịch sử, chưa API/autoretrain. [OPA pre-deployment gate](deployment-policy.md) bắt thiếu seccomp/limits, cloud ngoài profile và model/context không cùng lineage. Native48 cases gồm2 allow/46 deny; Jenkins3 success và5 rejection dùng cùng19 Git inputs, không deploy. Hai phép thử có scope độc lập, không nhận gate này đã thay admission của toàn platform.

## Xác nhận cuối28/10

Người dùng trả lời rõ **“Chưa học cả hai”** cho MMD và OPA/Rego. [Record xác nhận](evidence/course-scope/learner-confirmation.json) chỉ lưu nội dung kỹ thuật, không credential hoặc thông tin riêng. F58/F59 ghi Có bằng chứng với controlled/native MMD và native OPA/Jenkins đã kiểm. Phạm vi proof vẫn là diagnostic/gate độc lập, không nhận MMD API/autoretrain hoặc admission toànplatform.
