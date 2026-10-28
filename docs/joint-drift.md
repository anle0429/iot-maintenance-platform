# Phát hiện thay đổi quan hệ giữa cảm biến bằng MMD

Cập nhật28/10: [cohort thực trên Linux](joint-cohort.md) đã có train64/capture64, lineage,3directoracles/597permutations và replay. Vẫn là offline diagnostic, chưa API/autoretrain. Người dùng xác nhận28/10 chưa học MMD.

[Drift API hiện có](drift-detection.md) dùng PSI để kiểm từng feature riêng. Khi phân bố nhiệt độ và phân bố độ rung đều giữ nguyên, PSI có thể bằng0 dù quan hệ nhiệt–rung đã đổi. [JointDriftAudit](../ml/joint_drift.py) bổ sung một phép kiểm chung tám feature bằng Gaussian MMD. Đây là **audit offline nhỏ, chưa nối vào API/Airflow hoặc kích hoạt retraining**.

Ý tưởng này được chọn cho F58 sau xác nhận28/10 chưa học MMD. Hai lịch lớp đã có drift, thống kê và kernel; không thấy tênMMD. [Đối chiếu và xác nhận của người dùng](novel-ideas.md) tách kỹ thuật MMD cụ thể khỏi drift/kernel nói chung.

## Phương pháp và lựa chọn trước khi đo

MMD so sánh phân bố chung qua độ giống nhau giữa các vector. Dùng `k(x,y)=exp(-Σ((x_j-y_j)/s_j)^2/(2σ²))`; statistic là trung bình kernel trong reference cộng trung bình trong current, trừ hai lần trung bình giữa hai nhóm. Bản này dùng estimator biased, có cả đường chéo. Tham khảo công thức MMD trong [Gretton và cộng sự, JMLR2012, mục2.2](https://www.jmlr.org/papers/volume13/gretton12a/gretton12a.pdf); mã được viết riêng, không sao chép implementation của thư viện khác.

[Policy](../ml/protocols/joint-drift-v1.json) đã commit **`1791ffa61388d42ff5c8cfe87a12d8bb75b39240`** trước khi chạy phép thử, SHA`accaee091ab2d88002646b2e8eb64414f9fbebfcf6e991979f8bdb15e481740c`. Nó cố định engineering scales, sigma1,199permutations, seed1127, tie tolerance1e-12 và diagnostic tail threshold0,05. Scales là đơn vị vận hành khai báo trước; không fit scaler/bandwidth theo nhóm đang so sánh và chưa được chứng minh tối ưu cho nhà máy thật. Code từ chối policy bị sửa.

Module giữ nhóm reference32–128hàng và current32–128snapshot ID duy nhất; không tự lấy mẫu. Request dùng cùng contract event/availability time và quy tắc duplicate/conflict của drift API. Cùng ID và cùng payload được gộp; ID trùng khác nội dung, feature chứa label, NaN/Inf, snapshot chưa sẵn sàng trước cuối cửa sổ bị chặn. Chuẩn hóa thứ tự vector trước resampling giúp replay không đổi kết quả. Đổi thứ tự reference vẫn làm đổi artifact/reference ID theo contract hiện có.

Reference1024hàng của PSI native **không tự trở thành reference MMD**: phải định nghĩa một cohort nhỏ riêng từ train và gắn lineage trước khi xem kết quả. Cửa sổ vượt128unique bị từ chối trước cấp phát Gram matrix, không âm thầm bỏ bớt quan sát.

## Bằng chứng chạy trên PC

Kernel/CLI/tests tại source **`07a36edb67e222c415f8f985bd0e648769cfe05f`**, Windows/THIENAN/Python3.13.14. [Verification](evidence/joint-drift/verification.json) và [manifest](evidence/joint-drift/manifest.json) giữ code hash, đầu vào, report và console của từng lệnh CLI; tất cả đầu vào không đổi sau chạy. Dữ liệu dưới đây là **controlled fixtures**, không phải telemetry từ VM đang hoạt động. Sáu case giữ nguyên policy đã khai báo.

| Ca thử | Hàng mỗi nhóm | PSI lớn nhất | MMD² | Tail fraction | Tín hiệu MMD |
|---|---:|---:|---:|---:|---|
| Cùng dữ liệu | 64 | 0 | 0 | 1 | Không |
| Đảo quan hệ nhiệt–rung | 64 | 0 | 0,163369 | 0,005 | Có |
| Dịch nhiệt/rung rõ rệt | 64 | 3,939966 | 1,260576 | 0,005 | Có |
| Các vector hằng giống nhau | 32 | 0 | 0 | 1 | Không |
| Quan hệ phi tuyến đổi, covariance vẫn0 | 96 | 0 | 0,068137 | 0,005 | Có |
| Nhóm bằng giới hạn đã khai báo | 128 | 0 | 0 | 1 | Không |

Ca đảo quan hệ giữ **đúng multiset của cả8cột**, nên mọi PSI thực sự bằng0. Ca phi tuyến bổ sung giữ thêm covariance nhiệt–rung bằng0 ở cả reference/current; chỉ kiểm covariance tuyến tính cũng không thấy thay đổi này. Mỗi vòng tròn phía dưới ghi số hàng tại đúng tọa độ; không jitter hoặc thêm dữ liệu.

![Controlled fixtures: giữ phân bố từng cột nhưng đổi quan hệ giữa các cảm biến](evidence/joint-drift/controlled-association.png)

[19tests PASS](evidence/joint-drift/unit-tests.txt), [coverage module56statements/14branches100%,0excluded](evidence/joint-drift/coverage.json). Có scalar kernel oracle, unequal-size direct sums, boundary, replay và Hypothesis symmetry. Ca phi tuyến là phép bổ sung sau lần triển khai đầu, trước khi chạy ca này; threshold/policy không được đổi theo kết quả.

[Verifier độc lập](../scripts/verify-joint-drift.py), source **`64d084e5a535020bc6bcf17e7d59182e01294bb9`**, tính lại các tổng AA/BB/AB trực tiếp, không import estimator hay dùng công thức cache row sums. [Kết quả](evidence/joint-drift/independent-oracle.json) khớp cả6observedstatistics và **1.194permutation statistics**, maxdelta≤6,67e-16; cùng exceedance counts/tail fractions. So sánh này kiểm tính đúng phép tính, không chứng minh error rate thống kê.

[Đo tài nguyên](evidence/joint-drift/maximum-profile.json): tại128+128hàng, Gram float64 đúng**524.288byte**; Python tracemalloc peak lúc compare**728.744byte**, sau bước nạp/validate reference/request. Đây chưa là RSS toàn process. CLI case lớn nhất chạy~0,678giây trên PC; lần có tracing~3,553giây. Không dùng các số này làm SLA, so PC với VM hoặc nhận đã benchmark trên100GB.

## Tự chạy từ clone

Dùng môi trường API/test đã khóa của dự án; kernel chỉ thêm stdlib, không thêm package vào image hay dịch vụ mới. Chọn output chưa tồn tại:

```bash
python -m pytest tests/test_joint_drift.py -q --cov=ml.joint_drift --cov-branch
python scripts/audit-joint-drift.py --reference docs/evidence/joint-drift/association/reference.json --window docs/evidence/joint-drift/association/window.json --output outputs/joint-association-new.json
python scripts/verify-joint-drift.py --evidence docs/evidence/joint-drift --output outputs/joint-independent-new.json
```

CLI kiểm mỗi input≤1MiB, ghi hash bytes đầu vào và giữ file output cũ. Các thư mục `unchanged`, `marginal-offset`, `constant`, `nonlinear`, `maximum` cũng có đủ reference/window/report để chạy lại cùng lệnh. Hình được vẽ bằng Matplotlib3.10.8 trong môi trường vẽ riêng; không cần package này để chạy audit.

## Cách diễn giải và phần chưa đạt

Tail fraction dùng `(1+exceedances)/200`, tính cả ties;0,005 là độ phân giải nhỏ nhất của199lượt, **không phải xác suất99,5% đã drift**. Response chủ động giữ `p_value=null`, `exchangeability_verified=false`. Telemetry lặp theo máy, cửa sổ chồng lấn và reference được dùng lại không tự thỏa giả định exchangeability. Các control giống hệt nhau cũng không thay được thí nghiệm đo false-positive trên hai mẫu độc lập.

Tín hiệu ở đây phục vụ xem xét thủ công. Nó không chứng minh accuracy giảm, concept drift, nguyên nhân sự cố hay cho phép retraining tự động. Scales/sigma/cohort có thể làm giảm độ nhạy; repeated monitoring/multiple windows và autocorrelation vẫn cần thiết kế calibration/resampling thích hợp trước một cảnh báo vận hành có bảo đảm thống kê.

Bổ sung28/10: [native cohort có lineage](joint-cohort.md) đã kiểm train64/capture64 trên Linux, và người dùng xác nhận chưa học MMD. Chưa tích hợp endpoint/autoretrain; các kết quả controlled PC bên trên giữ scope riêng.
