# Gateway riêng: metrics, logs, trace và feature API

Ngày28/10/2026 trên cùng VM8CPU/64GiB, **F5 NGINX Ingress Controller5.7.0** đã chạy thật trên K3s1.35.9, một Pod/1CPU/512MiB, image khóa digest. [Bộ kiểm](../scripts/verify-private-gateway.py) kiểm độc lập86payload/4JPEG,77payload native và source Git. [Kết quả gốc](evidence/private-gateway-linux/verification-final/verification.json) lúc19:47:57UTC có4tuyến,8lần401,4redirect308,2caTLS bị từ chối, burst32yêu cầu: **6HTTP200/26HTTP429**, sau2giây phục hồi200. Đây là phép thử enforcement nhỏ, chưa phải benchmark công suất.

## Một cửa vào, bốn dịch vụ

| Host nội bộ | Backend | Bằng chứng |
|---|---|---|
|`metrics.iot.test`|Grafana, dashboardAPI observability|[Ảnh](evidence/private-gateway-linux/screenshots/grafana-gateway.jpg), JSON dashboard đúngUID|
|`logs.iot.test`|Grafana/Loki, dashboard và datasource proxy|[Ảnh](evidence/private-gateway-linux/screenshots/logs-gateway.jpg), [Loki labels thật](evidence/private-gateway-linux/verification-final/logs-native-query.json)|
|`traces.iot.test`|Jaeger collector/query hiện có|[Ảnh trace200](evidence/private-gateway-linux/screenshots/jaeger-gateway.jpg), [trace gốc](evidence/private-gateway-linux/ui-supplement/trace-713708c.json)|
|`features.iot.test`|Feature API→Feast, tám rolling features|[Ảnh API](evidence/private-gateway-linux/screenshots/features-gateway.jpg), [response qua gateway](evidence/private-gateway-linux/verification-final/feature-http.json)|

![Grafana thật qua gateway](evidence/private-gateway-linux/screenshots/grafana-gateway.jpg)

![Jaeger thật qua gateway](evidence/private-gateway-linux/screenshots/jaeger-gateway.jpg)

Service gateway là **ClusterIP**, không LoadBalancer/NodePort/externalIP/hostNetwork. Grafana vẫn nghe localhost13001. Một relay chuẩn `systemd-socket-proxyd` nối CNI bridge13002 với localhost13001; gateway dùng EndpointSlice riêng cho metrics/logs. Jaeger và API dùng Service/EndpointSlice hiện có, tự theo Pod trong namespace của mình. Role cho controller đọc cấu hình/Secrets tại đúng3namespace; chỉ namespace/IngressClass có quyền đọc ở cluster. [Can-i6ca](evidence/private-gateway-linux/final/rbac-checks.json) chứng minh không đọc Secrets namespace serving, không tạo Deployment ởdefault, không xóa Secrets feature. Đây chưa phải ngăn mọi Pod nội bộ gọi thẳng backend.

## Authentication, rate limit và HTTPS

Bốn Ingress yêu cầu Basic authentication. API giới hạn **5request/giây theo địa chỉ nguồn, burst5/nodelay**, trả429 khi vượt. Một replica/controller nên chưa có bài toán chia quota qua nhiều replica. Sau xác thực, runtime NGINX xóa Authorization trước khi proxy xuống backend; [config đọc từ Pod](evidence/private-gateway-linux/verification-final/nginx-config.txt) có đủ4directive rỗng. Username/password ngẫu nhiên, htpasswd, TLS key và CA key chỉ ở private runtime/Secrets, không trong Git hoặc ảnh.

Client native dùng CA được chỉ rõ và kiểm hostname/SNI, thương lượng **TLS1.3**; chỉTLS1.2/1.3 được bật. [Ca âm](evidence/private-gateway-linux/verification-final/tls-negative.json): không tin CA bị client từ chối `CERTIFICATE_VERIFY_FAILED`; SNI sai bị server từ chối `TLSV1_UNRECOGNIZED_NAME`. Ca thứ hai không phải một lỗi hostname do client phát ra. Bốn host có trong SAN của certificate gốc.

**Phạm vi F43 là virtualhost HTTPS của lab riêng**: client chỉ rõ host→ClusterIP, chưa đăng ký public DNS/domain hoặc chứng chỉ public/browser trust. Không sửa trust store hay vượt cảnh báo trình duyệt. Ảnh giao diện lấy qua cầu review localhost/IAP: browser→loopbackHTTP→gatewayHTTPS có CA+SNI+Basic auth. [Receipt cầu review](evidence/private-gateway-linux/review-encoding/receipt.json) ghi đúng luồng này; ảnh không chứng minh thanh địa chỉ browser đang dùng HTTPS. Cầu chỉ phục vụ review, tự hết sau2giờ. Certificate server lab được cấp2ngày, CA3ngày: cần cấp lại trước khi demo sau hạn đó.

## Dữ liệu mới và lỗi đã giữ

API seed cũ đã hết lease, probe đầu trả502 vì không có backend Ready; [probe](evidence/private-gateway-linux/probe/responses.json) giữ nguyên. Đã materialize200máy bằng Feast thật, tạo ConfigMap bất biến mới `iot-feature-gateway-28`, giữ image `observe-jenkins-2` và model6. [Refresh19:44:39UTC](evidence/private-gateway-linux/feature-refresh/verification.json):195prediction khớp logistic độc lập/sai lệch0,5máy dừng409,200key TTL tối thiểu3.581.839ms. Lease1giờ từ19:44:22UTC, không sửa timestamp/TTL. Verifier tính lại toàn bộ195prediction từ model/HTTP gốc; một feature response qua gateway khớp backend trực tiếp. Đồng hồ nghiệp vụ vẫn là simulation29/11, không nhận là máy thật hiện tại.

Annotation đầu để xóa Authorization đã dùng dấu nháy không hợp lệ. Controller từ chối cả4Ingress; [logRejected](evidence/private-gateway-linux/header-diagnosis/controller.log) và manifests gốc giữ nguyên. Đọc parser đúng5.7.0 rồi dùng `Authorization:` để controller sinh giá trị rỗng; không bật snippets. Một phép kiểm tiếp theo dừng do so URL chuỗi quá chặt: NGINX trả đúng308 nhưng có`:443`; report gốc giữ trong `verification-fixed/`. Bản cuối kiểm scheme/host/port/path đúng nghĩa, không sửa response cũ.

Log UI có log host đang cập nhật. Các ô application log hiện trống vì Alloy còn theo file Pod lịch sử sau rollout; chưa nhận là auto-discovery Pod mới. Trace trong ảnh là GET feature200/một span; bằng chứng trace dự đoán3span trước đó ở [observability](api-observability.md), không gộp hai scope.

## Tự kiểm và chạy lại

Trên PC/laptop clone đầy đủ lịch sử Git, không cần VM hoặc dependency:

```powershell
python -S scripts/verify-private-gateway.py
python -m unittest tests.test_gateway_contract -v
```

[Renderer](../infra/gateway/resources.py), [asset pins](../infra/gateway/assets.json), [deployer](../scripts/deploy-private-gateway.py), [native checker](../scripts/check-private-gateway.py). Source ban đầu7687; bản runtime sửa annotationd021; checker cuối**e5ffd51**/5Gitfiles. Hash mọi bản có ở [sources](evidence/private-gateway-linux/sources/gateway-source-v4.json), không nhận source mới đã được chạy trong các attempt cũ.

Trên VM đã chuẩn bị backend/active feature, triển khai lần đầu với output/private path mới và phiên còn ít nhất30phút:

```bash
sudo python3 scripts/deploy-private-gateway.py --runtime-root "$PWD" --output "$PWD/outputs/gateway-demo" --private "$PWD/data/runtime/gateway-demo-private"
```

Deployer từ chối namespace gateway đã có, tránh ghi đè deployment/keys. Không chạy lại lệnh trên instance hiện tại để làm mới feature. Kiểm lại instance đang chạy bằng native checker với CA, credentials và feature-state đúng; output phải là thư mục mới. Mật khẩu chỉ đọc ở file private trên VM, không paste vào chat/lệnh có lịch sử. Khi API hết lease cần prepare/materialize context mới thật, như [quy tắc feature demo](active-feature-refresh.md).

Chọn controller F5 đang được duy trì theo [release5.7.0](https://github.com/nginx/kubernetes-ingress/releases/tag/v5.7.0); community ingress-nginx đã nghỉ bảo trì theo [thông báo Kubernetes](https://kubernetes.io/blog/2026/01/29/ingress-nginx-statement/). Cấu hình theo [annotations chính thức](https://docs.nginx.com/nginx-ingress-controller/configuration/ingress-resources/advanced-configuration-with-annotations/) và [parser đúng5.7.0](https://github.com/nginx/kubernetes-ingress/blob/v5.7.0/internal/configs/version1/header_parsing_helper.go).
