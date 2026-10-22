# Hợp đồng dữ liệu nguồn, phiên bản 1

Chốt ngày 22/10/2026 cho mốc ngày 1. Đơn vị nghiệp vụ là máy bơm ly tâm mô phỏng. Mỗi dòng telemetry biểu diễn **một máy tại một thời điểm đo**; bản gửi/xuất lại giữ nguyên `event_id`. Chỉ số và thông số mô phỏng phục vụ coursework, chưa hiệu chuẩn theo máy công nghiệp thật.

## Phạm vi quan sát và lưu trữ

Mọi timestamp là ISO 8601 UTC với hậu tố `Z`, độ chính xác giây. Khoảng quan sát là `[start_ts, end_ts)`, mốc cuối không có reading. Cấu hình mặc định từ `2026-10-01T00:00:00Z` tới trước `2026-10-03T00:00:00Z`, 20 máy, một reading/phút kể cả khi dừng: 20 × 48 × 60 = 57.600 sự kiện duy nhất.

Nguồn cục bộ nằm ở `data/source/<run>/`, **tách khỏi Bronze** sẽ được dựng trong mốc sau. Định dạng JSONL UTF-8, mỗi dòng là một object, không nén, LF. `manifest.json` ghi cấu hình, phiên bản mô phỏng, số dòng, SHA-256 và byte đo từ tệp. Không sửa một source run đã tạo; lần mới dùng tên khác. Bộ sinh giữ dữ liệu trong RAM và giới hạn 100.000 sự kiện duy nhất để tránh vô tình chạy quy mô lớn.

| Tệp | Grain và khóa | Nội dung |
|---|---|---|
| `machines.jsonl` | Một snapshot/máy, PK `machine_id` | Metadata khởi đầu, chưa có nhiều phiên bản |
| `maintenance_events.jsonl` | Một khoảng bảo trì, PK `maintenance_id`, FK `machine_id` | Bảo trì phòng ngừa và khoảng dừng |
| `failure_events.jsonl` | Một sự cố bắt đầu, PK `failure_id`, FK `machine_id` | Sự cố do hao mòn và khoảng sửa chữa |
| `telemetry/schema_v1/*.jsonl` | Một bản xuất của reading, logical key `event_id` | Schema cũ, có duplicate có chủ đích |
| `telemetry/schema_v2/*.jsonl` | Như v1 | Thêm pressure ở mốc đổi firmware |
| `stream/telemetry_deliveries.jsonl` | Một lần gửi, PK `delivery_id` | Envelope chứa telemetry, lần gửi và thời điểm tới |

ID gồm fingerprint cấu hình vật lý, máy và bước thời gian. Thay seed hoặc cấu hình vật lý là một dataset khác; không ghép các lần mô phỏng khác cấu hình như một dòng lịch sử thực. Cấu hình truyền dữ liệu không thay đổi ID hoặc lịch sử sự cố. Phiên bản code vẫn phải được ghi cùng evidence; thay code có thể thay dữ liệu dù seed giữ nguyên.

## Telemetry

| Trường | Kiểu JSON | Quy tắc/ý nghĩa |
|---|---|---|
| `event_id` | string | ID reading ổn định; trùng có chủ đích được phép ở nguồn |
| `machine_id` | string | FK tới machines, ví dụ `P-001` |
| `event_ts` | string timestamp | Thời điểm thiết bị đo |
| `received_ts` | string timestamp | Lần đầu gateway có reading; không đổi khi retry hoặc xuất lại |
| `created_ts` | string timestamp | Thời điểm tạo bản xuất nguồn: bản gốc bằng received; bản xuất lại muộn hơn |
| `temperature_c` | number hữu hạn | °C, không âm trong mô phỏng này |
| `vibration_rms_mm_s` | number hữu hạn | RMS mm/s, không âm |
| `current_a` | number hữu hạn | Ampere, bằng 0 khi motor dừng |
| `rpm` | number hữu hạn | Vòng/phút, bằng 0 khi motor dừng |
| `operating_status` | string enum | `operating`, `maintenance`, `failed`; trạng thái hiện tại, không phải nhãn tương lai |
| `schema_version` | integer | 1 hoặc 2 |
| `pressure_bar` | number hữu hạn, chỉ v2 | Bar, không âm; bằng 0 khi dừng |

`event_ts <= received_ts <= created_ts`. Không chứa latent wear, failure timestamp tương lai hoặc nhãn ML. Các cột chỉ số cũ không null ở tập này; thiếu chỉ số/sensor drift chưa được mô phỏng. Không tự điền pressure bằng 0 cho v1: Silver sẽ union schema và biểu diễn là NULL, vì v1 chưa đo pressure.

Đổi sang v2 theo **event time** ở `start + floor(steps × schema_v2_fraction) × sample_seconds`. Mặc định là `2026-10-02T00:00:00Z`. Một v1 đến muộn sau mốc đổi firmware vẫn là v1. Phải đọc cả hai thư mục; không suy luận schema của toàn nguồn từ một tệp.

## Metadata và sự cố

`machines`: `machine_id`, `factory_id`, `machine_type`, `installed_at`, `rated_rpm`, `valid_from_ts`, `valid_to_ts`, `is_current`. Một snapshot ban đầu, `valid_from_ts = start`, `valid_to_ts = null`, `is_current = true`. Chưa chứng minh SCD2: ngày 2 cần thêm thay đổi metadata, xây `dim_machine`, kiểm khoảng không chồng và đúng một phiên bản hiện tại.

`maintenance_events`: `maintenance_id`, `machine_id`, `start_ts`, `end_ts` nullable, `maintenance_type = preventive`, `component = bearing`, `created_ts = start_ts`. Chu kỳ mặc định 18 giờ, lệch 5 phút theo số máy; dừng 20 phút. Bảo trì giảm latent wear còn 35%; nếu máy đang sửa chữa tại mốc bảo trì thì bỏ lượt đó. Chưa sinh bản ghi corrective maintenance riêng; sửa chữa nằm trong failure interval.

`failure_events`: `failure_id`, `machine_id`, `failure_start_ts`, `recovered_ts` nullable, `failure_type = bearing_wear`, `recorded_ts = failure_start_ts + 30 giây`. Latent wear vượt ngưỡng sẽ tạo sự cố, motor dừng 45 phút và wear được reset cho chu kỳ tiếp theo. Wear không được ghi vào telemetry. Khoảng dừng là `[start, end)`; reading ở đầu khoảng đã dừng, ở cuối khoảng hoạt động lại nếu không có sự kiện mới.

Thời điểm kết thúc bảo trì/phục hồi chỉ được ghi khi kết thúc **trước** cuối quan sát; nếu chưa quan sát được thì NULL. `end_ts`/`recovered_ts` là kết quả biết sau khi hoàn thành: không được coi là thông tin đã có ở `created_ts`/`recorded_ts`. Khi dựng feature, phải lọc cả thời điểm hoàn tất và thời điểm ghi nhận; nếu cần CDC thì bổ sung version/update timestamp trước khi dùng. Lịch sử sự cố tương lai chỉ dùng tạo nhãn.

## Mô phỏng và tỷ lệ lỗi

Tải là một dao động theo thời gian cộng nhiễu, có lúc cao trong vận hành bình thường. Wear tăng theo tải, tốc độ hao mòn riêng từng máy và nhiễu; temperature/vibration/current phụ thuộc tải và wear. Ngưỡng wear gây failure; không bốc thăm nhãn độc lập. Đây là mô hình cơ chế đơn giản, không phải bằng chứng mô hình ML có thể áp dụng ngoài thực tế.

| Hiện tượng | Định nghĩa mẫu số | Mặc định và cách tạo |
|---|---|---|
| Skew | Readings duy nhất, join machines lấy factory | `floor(machine_count × share + 0.5)` máy ở F-01; phần còn lại chia F-02/F-03. Cùng tần suất đo nên 14/20 máy cho 70% readings |
| Offline duplicate | Số bản **thêm** / readings duy nhất | Lấy đúng `floor(N × 0.02)` readings không hoàn lại; copy payload, tăng created 3.600 giây |
| Stream duplicate | Số lần retry thêm / readings duy nhất | Lấy `floor(N × 0.015)`; cùng payload, thêm delivery attempt 2 sau 2 giây |
| Late arrival | Readings duy nhất trễ / N | Lấy `floor(N × 0.05)`, delay nguyên 300–600 giây; còn lại 0–3 giây |

Ví dụ: 60 readings + 6 bản xuất thêm có tỷ lệ extras/unique = 10%, nhưng tỷ lệ bản thừa trên số dòng lưu = 6/66 = 9,09%. Báo cáo giữ cả hai mẫu số. Skew đo trên readings đã dedup; không dùng phân phối này để tuyên bố đã đo hiệu quả tối ưu Spark.

Các lựa chọn late/offline duplicate/stream retry dùng random stream riêng, có thể chồng lên cùng reading. Thay cấu hình vận chuyển không đổi lịch sử vật lý. Tái lập từng byte đã kiểm trong cùng CPython 3.13.14; không bảo đảm byte giống trên mọi phiên bản Python vì thuật toán phân phối có thể thay đổi.

## Envelope và chính sách xử lý dự kiến

Envelope có `delivery_id`, `attempt` (1 hoặc 2), `published_ts`, `event` (object telemetry). Tệp sắp theo `published_ts`, tie-break bằng delivery_id; không sắp theo event_ts. `published_ts = received_ts + (attempt - 1) × retry_delay_seconds`. Đây là fixture phát lại, chưa phải Kafka producer hay streaming runtime.

- Silver batch: giữ bản có `created_ts` lớn nhất cho cùng event_id; payload ngoài created phải giống nhau, xung đột phải báo lỗi/cách ly. Oracle Python hiện chỉ dùng kiểm nguồn; Spark sẽ triển khai quy tắc này.
- Flink dự kiến: dedup theo event_id; watermark 15 phút cho độ trễ hiện tại tối đa 10 phút. Readings tới trước watermark được cập nhật cửa sổ; ngoài watermark đưa sang luồng late để đối soát batch. TTL trạng thái dedup và hành vi replay sau TTL cần được chọn, kiểm chứng ở mốc streaming.
- Phát lại toàn tệp hai lần phải giữ nguyên số readings duy nhất. Hiện mới kiểm bằng oracle tập ID cục bộ, chưa chứng minh Flink hay sink idempotent.

## Nhãn một giờ và feature: quy tắc cho mốc ngày 2

Một snapshot ở t chỉ đủ điều kiện khi máy **operating tại t**. `failure_in_next_1h = 1` nếu sự cố bắt đầu trong `(t, t+1h]`; sự cố đúng t không phải tương lai và máy tại đó đã failed. Chỉ gán 0 khi biết đầy đủ khoảng tương lai và không có failure. Vì cuối quan sát là exclusive, loại các snapshot có `t+1h >= observation_end_exclusive_ts`; dùng event/record watermark đầy đủ khi tạo dataset thực tế.

Để tránh đoán kết quả sau can thiệp, loại mọi snapshot có bảo trì bắt đầu trong `(t, t+1h]`, kể cả khi khoảng đó cũng có failure. Snapshot đang dừng/sửa/bảo trì bị loại. NULL recovery không được đổi thành một thời điểm đã biết. Cần unit test nhãn với failure ở t, t+1h, t+1h+1s, cuối quan sát, bảo trì và máy dừng trước khi train. **Mốc ngày 1 chưa tạo feature/nhãn ML.**

Feature chỉ lấy `event_ts <= t` **và** `received_ts <= t`; bản đến sau t không được sửa feature lịch sử đã dùng tại t. `created_ts` của bản xuất offline mới hơn không làm measurement gốc mới có sẵn; phải giữ thông tin first arrival. Lần bảo trì hoàn tất chỉ dùng khi end/availability <= t; sự cố lịch sử chỉ dùng khi failure_start và recorded <= t. Feature có `event_timestamp` (thời điểm hiệu lực) và `created` (thời điểm tính/ghi), kèm availability nếu cần; không coi PIT join tự giải quyết arrival leakage.

Train/validation/test chia theo thời gian. Loại hàng train có cửa sổ nhãn chạm hoặc vượt đầu validation; tương tự validation/test, tức chừa ít nhất một giờ tại ranh giới. Kiểm riêng với snapshot biết đáp án; không chia ngẫu nhiên.

## Kiểm chứng hiện tại

`generator/quality.py` đo lại dữ liệu đã đọc từ đĩa: toàn bộ time grid, khóa/FK, schema/key/type, interval và trạng thái motor, tỷ lệ/lateness/skew, cardinality, envelope/retry và checksum từng tệp. Test cố tình làm sai timestamp/payload/thiếu reading/failure để kiểm validation báo lỗi. Kết quả thật và giới hạn xem [mốc ngày 1](day1-generator.md).

Nguồn kỹ thuật: [Python random 3.13](https://docs.python.org/3.13/library/random.html) mô tả instance riêng và giới hạn tái lập giữa phiên bản. Không tái sử dụng mã repo tham khảo.
