# Protocol đánh giá ML: medium-60d-v1

[Cấu hình đã chốt](../ml/protocols/medium-60d-v1.json) được tạo trước lần huấn luyện/đánh giá mới. Đây là protocol cho mốc ML kế tiếp, chưa phải kết quả mô hình. Không sửa các kết quả ngày2 có test0positive; giữ chúng như bằng chứng giới hạn của lịch sử48giờ trước.

Nguồn DE trung gian đã được chọn và kiểm trước khi có metric ML mới: `scale-intermediate-26`,200máy×60ngày liên tục,5.587.984.353B offline; manifest SHA `6fb9980bdd739bb3869a5bcb1d2669ee7dd326fcf0f2ba0f2fb6a50f747ba8c5`. Mốc nguồn102,45GB dùng để kiểm quy mô DE riêng; không gán ngược nó thành tập huấn luyện của protocol này. Các timestamp sau là **thời gian mô phỏng**, có thể nằm sau ngày chạy thực.

| Tập | Cửa sổ event time UTC | Điều kiện bổ sung |
|---|---|---|
| Train | [01/10/2026,12/11/2026) | `label_available_ts < 12/11/2026 00:00:00Z` |
| Validation | [12/11/2026,21/11/2026) | `label_available_ts < 21/11/2026 00:00:00Z` |
| Test | [21/11/2026,30/11/2026) | Nhãn eligible và đủ horizon trước observation end |

Snapshot mỗi5phút; feature chỉ dùng readings trong `(t−10phút,t]` đã tới tại `t`, và failure history24h đã được ghi nhận tại `t`. Có8feature trong allowlist; không lấy trạng thái vật lý thật, nhãn hoặc thông tin sự cố tương lai làm input. Trạng thái vật lý của simulator chỉ dùng xác định cohort/nhãn máy đang hoạt động; trạng thái quan sát được khi serving phải dùng dữ liệu đã tới, không gọi truth generator.

Nhãn1 nếu failure bắt đầu trong `(t,t+1h]`. Máy dừng, có bảo trì bắt đầu trong horizon, hoặc `label_end_ts >= observation_end` thì nhãn unknown, không điền0. Availability của nhãn là horizon end cộng30giây; train/validation phải purge những dòng chạm ranh giới như bảng trên. Các dòng không có readings đã tới hoặc có feature không hữu hạn được loại theo quy tắc cố định và phải báo số lượng, không nhìn nhãn để chọn dòng thuận lợi.

Baseline giữ Spark MLlib logistic regression `maxIter=40, regParam=0.1`, StandardScaler chỉ fit train, class weights chỉ tính từ train. Threshold chỉ chọn trên validation từ0,1 đến0,9 theo F1, rồi precision, rồi threshold cao hơn khi hòa. Dùng training prevalence làm constant comparator. Test chỉ mở để đánh giá sau khi đã chốt model/threshold; không dùng test chọn feature, seed, cut time hoặc tham số.

Trước đánh giá, validation và test cần ít nhất20positive rows,20negative rows và5failure episodes khác nhau. Nhiều snapshot trước cùng một failure không phải nhiều sự cố độc lập. Nếu không đạt gate thì báo chưa đủ dữ liệu/không ước lượng được; không dời các cut time của version này. Bất kỳ thí nghiệm mới nào cần protocol version mới, lý do rõ và đánh giá độc lập phù hợp; không âm thầm chọn lại sau khi xem test.

Mốc triển khai kế tiếp phải kiểm feature/label bằng dữ liệu biết đáp án, arrival-time và các ranh giới này trước khi fit. Cần đo đủ số dòng/classes/purge/exclusions, dùng PR-AUC có chỉ rõ implementation, confusion matrix, precision/recall/F1 ở threshold đã khóa, và truy được source → protocol → feature → training run → model → prediction. Chưa có Feast/KFP/MLflow/model mới từ việc chỉ tạo protocol; các tiêu chí đó vẫn cần chạy thật.

**Cập nhật kiểm chứng27/10:** [Chuẩn bị feature60ngày](medium-feature-dataset.md) đã hoàn tất3.456.000snapshots/3.132.184eligible, có multisetreadback, arrival/horizon/purge và SHAinput/output. Validation có19.828positive/1.677episodes; test19.957positive/1.687episodes, đều qua gate đã khai báo. ProtocolJSON/SHA không đổi, chưa fit hoặc xem metric mô hình mới; kết quả cũ0positive vẫn giữ nguyên. Tiếp theo là Feast và pipeline huấn luyện, không dùng class support làm bằng chứng chất lượng ML.
