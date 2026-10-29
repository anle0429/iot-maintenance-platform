# Rà soát rubric final và khả năng tự demo

Chuẩn kiểm là **57 dòng có trọng số của sheet final-coursework và yêu cầu A2**, theo xác nhận của học viên. Tổng trọng số nguồn là100; đây không phải điểm tự chấm. Các phần triển khai đều có bằng chứng trong phạm vi lab đã mô tả. Đợt rà soát bổ sung tìm thấy các lỗi ở công cụ kiểm và bàn giao vận hành; không thể suy từ một tracker màu xanh rằng máy vừa bật lên đã sẵn sàng demo.

## Những thiếu sót tìm được và đã xử lý

| Thiếu sót trước rà soát | Thay đổi cụ thể | Kiểm chứng |
|---|---|---|
| Hai bộ kiểm metrics PC/Linux từ chối source sau khi thêm docstring | Giữ kiểm SHA của source Git đã chạy; cho phép khác biệt AST chỉ ở phần mô tả Python, config vẫn phải khớp | Hai bộ kiểm chạy qua; test đổi logic/default/decorator/config vẫn bị từ chối |
| Chưa có một lệnh đối chiếu toàn rubric và chạy hết các bộ kiểm offline | Thêm `audit-final-coursework.py`: pin snapshot nguồn, resolve ô merge, kiểm57 dòng/sáu cột nguồn, chín capsule và26verifier | Phát hiện được yêu cầu bị làm yếu, dòng mất/trùng, snapshot nguồn bị sửa và payload sai hash/thoát thư mục |
| Clone Windows từ chối snapshot vì hash ban đầu lấy từ CRLF của working copy | Cố định snapshot LF bằng `.gitattributes`, pin đúng blob Git gốc; không đổi nội dung workbook | Regression test so cả checkout và Git blob; audit lại từ clone sạch |
| Các dịch vụ systemd chạy tạm không tự trở lại sau VM STOP | Thêm `resume-demo-services.py`: phục hồi monitor, relay CNI của gateway, Alloy discovery, Jenkins và Vault; gia hạn JWT có scope cũ | Health của các dịch vụ trả200; giữ Jenkins home/ảnh container và Vault cluster ID/Raft; mở khóa thay vì init lại |
| MLflow khỏe trong Docker nhưng container đã phục hồi không publish cổng UI | CLI phục hồi thêm socket relay chỉ ở127.0.0.1:15000 tới IP private của container hiện có | Model6/run ID đọc qua API khớp; giữ container/image/state mount; PC mở được trang qua IAP |
| AppRole/Jenkins credential hết hạn theo phiên | Cờ `--renew-consumers` cấp secret-id mới cho hai role cũ và cập nhật đúng một Jenkins SecretText | Quyền giữ nguyên; secret-id có20lượt dùng và hết hạn trước phiên VM; giá trị thật chỉ ở runtime/Vault/Jenkins |
| Cache hết hạn và hướng dẫn cũ dùng cầu review tạm | Thêm `refresh-demo-feature.py` và `demo-feature-api.py`; tài liệu dùng port-forward/IAP chuẩn | Materialize200máy thật, immutable seed mới, cùng model6; chín HTTP case qua và score tính lại khớp |
| Chứng chỉ máy chủ cũ hết hạn30/10, trước ngày nộp | Thêm `renew-demo-certificates.py`, cấp lại chứng chỉ bằng khóa bảo vệ hiện có và giữ bản cũ để phục hồi | X509 thật theo đồng hồ thật; bốn route HTTPS200 và peer fingerprint khớp chứng chỉ mới; Vault giữ cluster ID, không init lại |

[Kịch bản tự chạy và trình bày](demo-walkthrough.md) là điểm bắt đầu mới. [Bàn giao cũ](final-handover.md) giữ lịch sử bằng chứng; các giờ hết hạn trong lượt cũ không phải trạng thái live hiện tại.

## Cách rà từng dòng

1. Đối chiếu nội dung và trọng số với [snapshot ô gốc](evidence/rubric-final/source.json), kể cả các ô merge. Snapshot được pin bằng SHA256, tránh sửa cả nguồn lẫn tracker rồi tự nhận đạt.
2. Đọc yêu cầu/deliverable và phạm vi đã đo của từng hàng trong [ma trận57dòng](final-audit-matrix.md) và [tracker gốc](final-rubric-tracker.md).
3. Kiểm byte/hash của các capsule, Git source thực chạy và phép tính độc lập: score model, PSI/MMD, số hàng, replay, metrics, trace graph, mutation provenance và các kết quả CI.
4. Kiểm live riêng sau khi bật lại cùng VM: mounts, dịch vụ, feature lease, credential và các HTTP case. Artifact lịch sử không chứng minh một endpoint còn Ready hôm nay.

Lệnh offline dùng Python3.12/3.13, Git và thư viện chuẩn; không khởi động VM hoặc tải dữ liệu lớn:

```powershell
$tag = Get-Date -Format 'yyyyMMdd-HHmmss'
python -S scripts/audit-final-coursework.py --output "outputs/audit-$tag"
```

Kết quả gồm26console gốc và `verification.json`. Chín capsule bổ sung có685payload được đối chiếu; riêng file ignore-policy của coverage đã được manifest cũ ghi rõ là không đưa vào repo. Việc loại file đó không loại report, test, source hoặc số đo nào. Bộ kiểm này không tự chấm điểm và không thay việc đọc nội dung ảnh/deliverable.

## Kết quả chạy thật sau restart28/10

[Capsule bổ sung](evidence/final-review-live/manifest.json) giữ24payload công khai:21file VM, hai file HTTP PC và một binding source. Bộ kiểm `verify-final-review.py` đối chiếu bốn operator với Git source đã chạy,96file runtime và10file CI; giữ ba receipt lỗi ban đầu nguyên trạng. Không chứa helper điều khiển máy, private key, mật khẩu hoặc token.

- Phục hồi năm health endpoint200, relay gateway, Alloy có hardening; Jenkins dùng lại home/image và Vault giữ cluster ID/Raft. Token có hạn trước STOP8giờ.
- Feast materialize200máy thật trong container1CPU/8GiB, exit0/khôngOOM; Redis trước0sau200key, seed mới bất biến. Cache có hạn một giờ thật.
- Chín request PC qua IAP cho kết quả200/409/404/422 đúng; lặp predict giống nhau, tính lại risk score khớp model6. Đây là smoke test, không thay load test gốc.
- Jenkins Vault build3SUCCESS: đăng nhập Airflow201/đọc DAG200, đọc chéo và token đã revoke đều403. AppRole gateway đọc KV2version2 và bốn route HTTPS200.
- Cấp lại chứng chỉ thật: bốn peer TLS đúng fingerprint của leaf mới, CA/SNI verification giữ nguyên; các khóa không đổi và Vault không init lại. Ngày hết hạn mới nêu bên dưới.

## Phạm vi cần trình bày đúng

| Tiêu chí/phần | Đã có | Giới hạn của kết luận |
|---|---|---|
| F19–F22 | Incremental200máy; hai DAG stream có graph UI gốc; native5.760cửa sổ/57.600reading qua hai store và replay; TTL có lý do | CI/Airflow dùng fixture3cửa sổ; fullcohort là lượt Docker riêng. Ảnh RUNNING của hai job chụp lần lượt, không nhận chúng chạy đồng thời |
| F23–F27 | Notebook/KFP cùng5bước; train qua hai executor JVM/Pod; MLflow model/data versions tự động | Hai executor trên cùng VM; không gọi là benchmark nhiều máy vật lý |
| F38–F43 | Bốn virtualhost `.test`, Basic auth/rate limit, HTTPS có CA/SNI và phép thử từ chối | Domain riêng của lab, CA riêng; ảnh browser qua HTTP loopback/IAP không phải chứng minh publicDNS hay browser-trusted HTTPS |
| F44–F45 | Terraform cloud VM/network/disks và Ansible roles có idempotency | Rubric cho phép cloud services; triển khai dùng Compute VM. Các service bổ sung có CLI/chart riêng, chưa gom tất cả vào Ansible |
| F54–F55 | Vault có hai consumer thật và policy/cross-denial; mesh có identity/mTLS allow/deny thật | Lab một operator/một VM; mesh ở namespace thử riêng; không khẳng định HA hoặc migration toàn doanh nghiệp |
| Nguồn lớn/ML | Nguồn offline102.447.847.856byte đã qua Spark Bronze/Silver; ML dùng3.132.184training rows từ nguồn60ngày | Không nhận Gold/features/training đã chạy toàn100GB. Hai quy mô có proof riêng |
| Máy chạy | Native Linux VM và clone kiểm trên PC | Chưa kiểm thực tế trên laptop; laptop có thể dùng CLI nhẹ và IAP tới VM |

Đây là các giới hạn được ghi rõ, không phải yêu cầu mới tự thêm vào rubric. Nếu giảng viên yêu cầu publicDNS, mọi service qua Ansible, mesh toàn production hoặc ảnh hai job cùng RUNNING, cần chốt riêng phạm vi đó; không tự đánh dấu đã làm những việc này.

## Việc vận hành còn phải thực hiện khi demo vào ngày khác

- Bật cùng VM trong phiên đã được duyệt; kiểm nguyên8giờ tự STOP và các đĩa.
- Phục hồi dịch vụ và credential có hạn bằng CLI; chỉ materialize khi cache/lease cũ đã hết hạn. Không sửa `prepared_wall_utc` để kéo dài demo.
- Kiểm hạn certificate lab trước buổi trình bày. Lượt cấp lại29/10 UTC đã cho server hạn đến **05/11/2026 00:18:40 UTC**, CA đến12/11; lần cấp sau dùng CLI trong hướng dẫn, giữ kiểm CA/hostname. Chứng chỉ cũ trong capsule lịch sử vẫn giữ byte gốc.
- Giữ bí mật nằm ngoài Git và bài nộp. Dữ liệu/runtime của VM không tự xuất hiện trên laptop khi clone.

Các lần lỗi đầu của công cụ resume được giữ trong lịch sử receipt: thiếu đường dẫn asset khi chạy từ `/tmp`, chờ Raft chưa đủ sau unseal, và lỗi trùng tên hàm với module `http`. Lượt hoàn tất chỉ được ghi PASS sau khi sửa và chạy lại; không sửa các receipt lỗi thành thành công.

Tham khảo API dùng cho cập nhật một credential đang có: [Jenkins CredentialsStore](https://javadoc.jenkins.io/plugin/credentials/com/cloudbees/plugins/credentials/CredentialsStore.html). Thao tác quản trị chạy qua endpoint private bằng tài khoản operator hiện có; secret không được nhúng vào script gửi đi hay lưu vào proof.
