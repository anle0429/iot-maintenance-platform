# MMD trên cohort có nguồn gốc thực

Ngày28/10 đã chạy [audit MMD](joint-drift.md) trên VM Linux8CPU/64GiB với64hàng từ train reference và64máy từ capture gateway thật. [Protocol](../ml/protocols/joint-cohort-v1.json) commit`ed38564` trước kết quả: lấy64hàng đầu của reference1024 đã frozen;64máy operating đầu theo machine_id; thêm control đồng nhất và current quay vibration một vị trí. Không chọn lại theo alert, không đổi policy kernel/ngưỡng.

Reference gốc gắn model3/run`0f2fe09b6f974eadb858424a70edc7e6`/partitiontrain; capture phục vụ model6 dùng cùng dataVersion`5e78230e...`/protocol`e0bf...`. Giữ hai lineage riêng. Event time/availability current lấy từ feature_event_ts/as_of trong capture, chế độsimulation; đây **không phải fresh API request lúc chạy audit**. Control gán vector train vào cùng cấu trúc window chỉ là fixture đồng nhất, không nhận các timestamp đó là event train thật. Cohort có chủ đích không phải mẫu đại diện ngẫu nhiên.

| Case | MMD² | Tail fraction | PSI alert | MMD diagnostic |
|---|---:|---:|---|---|
| Reference so chính nó | 0 | 1 | Không | Không |
| Train64 so gateway64 | 0,2863526364463531 | 0,005 | Có | Có |
| Train64 so current quay vibration | 0,2570569718430441 | 0,005 | Có | Có |

Hai case sau đều có marginal drift; **không dùng chúng để nói MMD vượt PSI**. Giá trị bổ sung thuộc [controlled cases PSI0](joint-drift.md). Quay vòng giữ multiset từng cột current và không có điều kiện bắt buộc alert đã đặt trước. Không suy accuracy, sensitivity hay false-alarm rate từ bảng này.

Source native`876c79f`/13Gitinputs gồm code/protocol/parent captures. Image driftAPI có sẵn, nonroot10001/read-only/networknone/1CPU512MiB/no extra swap/no capabilities; exit0/noOOM, input trước sau không đổi. [Native receipt](evidence/joint-cohort-linux/native.json), [container](evidence/joint-cohort-linux/container.json). Một lượt wall1,702996s gồm startup Docker, không SLA/benchmark100GB. Gram128×128 float64/131072byte; replay đảo thứ tự+duplicate exact giữ assessment.

```powershell
python -S scripts/verify-joint-cohort.py
python scripts/audit-joint-cohort.py --reference docs/evidence/offline-drift-linux/reference.json --current docs/evidence/private-gateway-linux/feature-refresh/http-all-machines.json --output outputs/joint-cohort-new
```

Verifier chỉ stdlib/Git, không import estimator: direct AA/BB/AB và **597permutation statistics**, maxdelta4,44e-16; kiểm selection/lineage/timestamp/counterfactual/replay/limits. Audit cần dependencies API đã khóa và output mới.17payload223320byte/16nativefile/native manifest. [Manifest](evidence/joint-cohort-linux/manifest.json). Builder đầu trên PC dùng nhầm thuộc tính reference_id, dừng trước tạo window/report; sửa detector.reference_id và tên permutation_tail_fraction, không đổi policy. Lỗi được quan sát trong phiên, không có native receipt riêng; native chạy sau sửa thành công.

Exchangeability chưa chứng minh, p_value giữnull; tail fraction chỉ diagnostic. Chưa API/Airflow/auto-retrain/quality claim. Người dùng xác nhận ngày28/10 chưa học MMD; [xác nhận](evidence/course-scope/learner-confirmation.json) cùng proof controlled/native hỗ trợ F58 trong phạm vi diagnostic đã ghi.
