# Policy trước khi triển khai bằng OPA

OPA là ý tưởng bổ sung thứ hai đã được kiểm chứng: diễn đạt điều kiện được phép triển khai thành policy có thể chạy trong CI. Mục đích cụ thể là bắt lỗi thiếu seccomp đã xảy ra trong dự án, cấu hình cloud vượt phạm vi đã duyệt và model/context sai lineage trước khi tới bước triển khai. Người dùng xác nhận28/10 chưa học OPA/Rego; [xác nhận](evidence/course-scope/learner-confirmation.json) áp dụng cho kỹ thuật policy cụ thể này, không phải quản lý ngân sách nói chung.

[Policy Rego](../infra/policy/deployment.rego), [cấu hình đã duyệt](../infra/policy/approved.json), [runner48cases](../scripts/check-deployment-policy.py) và [Jenkinsfile](../infra/ci/DeploymentPolicy.Jenkinsfile) dùng binary OPA **1.21.4** từ release chính thức, SHA256 `2ab571f367f293bb19523b76f92f1945bbf245003392c7f392903f95c2b9b630`. Binary không đưa vào Git. [Release](https://github.com/open-policy-agent/opa/releases/tag/v1.21.4), [hướng dẫn CI/CD chính thức](https://www.openpolicyagent.org/docs/cicd).

## Điều kiện đang kiểm

| Đầu vào | Điều kiện được kiểm |
|---|---|
| Projection của plan Terraform thật | Đủ14address/type không trùng; không delete; đúng một VM8CPU/64GiB/STOP28800giây, boot50GiB/haiđĩa500+150GiB; giữ một giao diện/cấu hình egress STANDARD hiện có; SSH chỉIAP/TCP22/đúngtag; không thêm resource chưa duyệt. |
| Manifest Deployment probe thật | Namespace thuộc phạm vi; user không root, RuntimeDefault, không hostNetwork/hostPath, không tự mount API token, rootfs chỉ đọc/capdropALL/noescalation; CPU/memory limit phải tồn tại và thuộc miền duyệt. Init container cần policy riêng. |
| Release envelope | Đúng cả9trường production model6/run/data/protocol/threshold/portablehash; bytes model và context protocol phải khớp. Model7 candidate bị từ chối. |

Đầu vào Kubernetes là manifest probe đã chạy cùng envelope model6 thật; không tuyên bố policy đã xác thực mọi manifest của toàn platform hay đã được gắn admission webhook. Đây là policy gate độc lập trước triển khai; CI kiểm này **không chạy lệnh deploy**.

## Kết quả native Jenkins

Source `dcdab7cb8d15a3285247b730903fabce13a76a3f`,19Gitinputs được kiểm trước/sau cả hai build cuối:

- Build3 **SUCCESS**,7,970giây: strict compile;48ca biết đáp án gồm2allow/46deny; candidate hợp lệ gate exit0; bước accepted thực sự chạy.
- Build5 **FAILURE mong đợi**,2,530giây: chính manifest thiếu seccomp lịch sử, gate exit1; console xác nhận bước accepted bị bỏ qua, không deploy. Jenkins `wfapi` kế thừa nhãn FAILED cho downstream này; giữ nguyên JSON, dùng console gốc để chứng minh không thực thi bước accepted.

![Build hợp lệ](evidence/deployment-policy-linux/screenshots/safe-build3.jpg)

![Console build bị chặn](evidence/deployment-policy-linux/screenshots/rejected-build5.jpg)

[Capsule](evidence/deployment-policy-linux/manifest.json) giữ nguyên cả48input/eval/gateoutputs, source seals, Jenkins console/stages/artifacts và lỗi ban đầu. Verifier độc lập `python scripts/verify-deployment-policy.py` kiểm565payload/550native+10supplement, 48quyết định, exit0/1, stage không chạy và Gitbindings. Các giới hạn1CPU/512MiB/networknone/nonroot/readonly được cấu hình trong Jenkinsfile; không có inspect riêng của từng container đã tự xóa, nên không nhận số đo RAM/CPU thực hay benchmark.

## Những lỗi được bắt và sửa

1. `version --format=json` không được phiên bản này hỗ trợ; dùng `version` thông thường, vẫn kiểm binarySHA chính thức.
2. VM có một external config cho egress hiện có; policy ban đầu giả định không có IP nên từ chối cả plan hợp lệ. Sửa policy khớp cấu hình đã duyệt, không đổi network VM hoặc mở cổng.
3. Build1: truy vấn `allowed == true` trả một giá trị booleanfalse vẫn có kết quả xác định; `--fail` không biến nó thành exit1. Chuyển sang **unification** `allowed = true`, khi không thỏa thì query rỗng và exit1.
4. Build2: field limit bị thiếu làm biểu thức membership không phát denial. Dùng giá trị mặc định rỗng cho trường bắt buộc; ca `missing-limits` phải bị chặn. Build3 kiểm lại toàn bộ48ca sau sửa.

Giữ dữ liệu lỗi giúp chứng minh kiểm thử có khả năng bắt lỗi, không chỉ chạy trường hợp đẹp. Policy chỉ bảo đảm các điều kiện/miền đầu vào đã khai báo; không chứng minh an toàn cho JSON bất kỳ hoặc thay thế review IAM/network/application.

## Tự chạy

Lấy đúng binary/checksum từ [asset lock](../infra/policy/opa-assets.json), xác minh SHA trước khi chạy trên Linux. `python scripts/check-deployment-policy.py --opa /path/to/opa --output outputs/my-policy-check` (thư mục mới). Trong Jenkins `iot-deployment-policy`, chọn cùng SOURCE_COMMIT đã seal; SCENARIO=`safe` phải xanh, `historical-missing-seccomp` phải đỏ ở gate. Không chỉnh credential hoặc trigger job triển khai khác. Tự thêm một ca sai biết đáp án trước khi mở rộng policy.

Build4 cũng bị từ chối thật nhưng helper submit chọn source của prepare-v3, khác build3; audit source seal phát hiện điều này. Đã giữ nguyên build4 và chạy negative build5 tại đúng source của build3. [Supplement gốc](evidence/deployment-policy-linux/negative5/verification.json) đối chiếu cả19hash bằng nhau; không sửa report cũ.
