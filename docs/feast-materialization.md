# Airflow incremental materialization và DataHub

Cập nhật28/10: [DataHub đã phục hồi sau STOP/START](datahub-restore.md), cùng image/volume, hai dataset/producing job/sáu assertion cũ còn được index. Không chạy lại materialization hoặc publication chỉ để phục hồi UI.

Ngày27/10 đã có [CI materialization riêng](ci-materialization.md): Jenkins test/build/auto-deploy và8task initial/incremental/replay/metadata chạy thật trên namespace CI tách riêng. Những receipt lịch sử trong tài liệu này vẫn giữ nguyên; CI không ghi ngược checkpoint/namespace cũ.

[DAG thực chạy](../airflow/dags/iot_feast_materialization.py) đưa 8 feature của 200máy từ offline Feast qua Redis, kiểm lại toàn bộ vector rồi mới gửi metadata. Nguồn là bản feature200máy×60ngày đã kiểm, giữ nguyên dataset/registry huấn luyện; registry online của DAG dùng project riêng `iot_materialization`.

`register_bound_source → materialize_incremental → validate_online → publish_to_datahub`. Airflow3.1.7/LocalExecutor, manual runs, max_active_runs1 và pool `feast_jobs`1slot. [Task launcher](../feature_store/airflow_tasks.py) dùng SDK Feast0.67.0 riêng1CPU/8GiB, publisher DataHub1.6.0 riêng1CPU/2GiB. [18 container thực tế](evidence/feast-materialization/capture/native-containers.json) xác nhận image ID, giới hạn và exit code. Không chạy Spark mới để materialize bộ feature đã có.

## Kết quả đã đọc lại

Cutoff dưới đây là UTC mô phỏng; Airflow UI ghi giờ America/Chicago, API ghi UTC thật. Đây là backfill hữu hạn, chưa phải luồng dữ liệu cảm biến đang cập nhật theo wall clock.

| Run | Cutoff | Trạng thái và kết quả |
|---|---|---|
| `feast-bootstrap-27` | 29/11/2026 23:00 | Native initial/validate200máy thành công; publish thất bại do enum DataHub sai, giữ nguyên report và lịch sử lỗi |
| `feast-bootstrap-recovery-27` | Cùng cutoff | Bốn task success; reuse checkpoint đã kiểm, mode`checkpoint_replay`, không reset hoặc nạp lại thời gian cũ |
| `feast-incremental-27` | 30/11/2026 00:00 | Bốn task success, **200/200 vector thay đổi**; native`materialize_incremental`, mọi giá trị khớp latest offline row tại cutoff |
| `feast-backward-27` | 29/11/2026 23:00 | Task materialize failed đúng lý do lùi checkpoint; validate/publish`upstream_failed`; SHA checkpoint không đổi |
| `feast-replay-27` | 30/11/2026 00:00 | Bốn task success, mode`checkpoint_replay`, **0 vector thay đổi**, không ghi thêm native materialization interval |

[Verification tổng](evidence/feast-materialization/controller/verification.json), [incremental report](evidence/feast-materialization/runs/feast-incremental-27/materialization.json), [API scheduler states](evidence/feast-materialization/runs/feast-incremental-27-airflow.json), [negative states](evidence/feast-materialization/runs/feast-backward-27-airflow.json), [replay](evidence/feast-materialization/runs/feast-replay-27/materialization.json). Controller wall duration gồm polling/index readback, khác duration DAG. Lượt incremental UI là38,391s; không coi là benchmark hoặc SLA.

![Bốn task incremental thành công](evidence/feast-materialization/screenshots/airflow-incremental-success.jpg)

![Yêu cầu lùi thời gian bị chặn](evidence/feast-materialization/screenshots/airflow-backward-blocked.jpg)

## Quy tắc an toàn dữ liệu

[Materializer](../feature_store/materialize.py) kiểm marker/SHA protocol, batch, compaction và file offline trước khi ghi. Kho nguồn là file bất biến; binding gắn project/source/8field/protocol. Chỉ nhận cutoff UTC trong horizon60ngày đã kiểm và không cho lùi checkpoint. Lần đầu nạp khoảng1giờ tới cutoff; lần sau native Feast tiếp từ interval đã ghi.

Một write lock bảo vệ registry/checkpoint. Trước native write có `_MATERIALIZING.json`; lỗi giữa chừng giữ marker và từ chối tự resume để người chạy điều tra. Report đã sealed cùng cutoff/source mới có thể tái sử dụng. Checkpoint/report/SHA được ghi nguyên tử; validator kiểm sealed report, source binding, requested cutoff, durable checkpoint, không có writer dở dang và cả200vector online khớp offline. So sánh số thực online dùng `allclose(rtol=atol=1e-12)` như native batch probe; [full training dataset](feast-feature-store.md) có exact multiset proof riêng.

Latest offline vector phải cách cutoff mô phỏng không quá600giây; ở cutoff cuối, feature mới nhất23:55 nên age300giây. `created` vẫn là build audit clock, không dùng nó thay arrival availability. Replay giữ nguyên vector và không refresh key; sau Redis TTL3600giây, replay có thể fail vì thiếu online values. Không giả đó là dữ liệu mới: cần nguồn/phiên bản mới hoặc thao tác backfill có chủ đích cùng clock policy trong bước streaming/serving tiếp theo.

## DataHub và validation

[Publisher](../feature_store/metadata.py) đọc validation/report đã bound, tạo source feature trung gian riêng với pilot48giờ, bảng compactParquet, onlineRedis và Airflow flow/job. [Indexed GraphQL readback](evidence/feast-materialization/runs/feast-incremental-27-datahub.json) xác nhận compact→online, producing job và đủ6assertion results theo run ID; không chỉ nhận HTTP acknowledgement.

![Lineage Parquet qua Airflow tới Redis](evidence/feast-materialization/screenshots/datahub-offline-online-lineage.jpg)

[Schema online](evidence/feast-materialization/screenshots/datahub-online-schema.jpg) có entity+8feature/200máy. [Sáu checks passing](evidence/feast-materialization/screenshots/datahub-six-checks-passing.jpg) là external custom assertions do validator tính, DataHub lưu metadata/kết quả. Backward run bị chặn trước validate/publish nên không phát một kết quả PASS giả cho nó.

Lỗi publisher đầu do dùng `DATABASE` thay enum`KEY_VALUE_STORE` của SDK1.6.0. Đã đọc schema SDK thực và chỉ sửa publisher; initial materialization/checkpoint không bị xóa. [Error records thật](evidence/feast-materialization/capture/expected-and-repaired-errors.json) giữ cả lỗi đó và lỗi backward mong đợi. Một helperV2 lỗi syntax trước khi chạy code; unitV3 mới điều khiển lượt phục hồi, report directory giữ tên`feast-airflow-27-v2`; không coi helper lỗi là deploy thành công. Full100GB Spark code được đối chiếu không đổi trước/sau.

## Tự chạy và đọc kết quả

Clone Git không chứa source feature, registry, Redis hoặc secret learner. Trên VM đã có [Feast batch](feast-feature-store.md), Airflow và DataHub: DAG processor tự nhận file mới; không rebuild/restart toàn platform. Thư mục`outputs/feast-materialization` cầnUID10001ghi được. [CLI](../scripts/verify-feast-materialization.py) dùng secret local sẵn có, không in token/password:

```bash
# VM hiện tại đã có checkpoint30/11; chọn run mới, giữ cutoff để kiểm replay.
sudo python3 scripts/verify-feast-materialization.py trigger \
  --run my-feast-replay --end 2026-11-30T00:00:00Z
sudo python3 scripts/verify-feast-materialization.py snapshot --run my-feast-replay
sudo python3 scripts/verify-feast-materialization.py metadata --run my-feast-replay
```

Chờ scheduler state`success` và cả4task success trước metadata readback; index có thể trễ vài giây. Nếu key đã hết TTL, validator sẽ báo thiếu feature; giữ report để điều tra, không xóa checkpoint hay chỉnh marker để biến thànhPASS. Trên môi trường mới chưa có DAG state, bootstrap cutoff29/11 23:00 rồi incremental30/11 00:00 bằng hai run khác tên. Profile này cố định nguồn200máy; chưa có tham số cho nhiều nguồn hoặc concurrent writers.

Trình bày được: Airflow quyết định thứ tự/chặn lỗi; Feast thực hiện lookup và ghi online; validator so dữ liệu thật; DataHub giúp truy nguồn và xem lịch sử chất lượng. Tiếp theo là streaming push cả offline/online, KFP4steps, MLflow/KServe và clock/freshness khi serving. Chưa có model metric mới, streaming deployment hoặc feature100GB từ mốc này.
