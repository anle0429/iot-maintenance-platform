# Metrics và trace cho ba API

**Bản chuẩn bị28/10/2026, chưa có dashboard/trace native để nhận F46/F49 hoàn tất.** Wrapper [services/observed.py](../services/observed.py) bọc các factory feature/drift/predictor hiện có; không sửa logic feature/model/drift.27test mới ban đầu PASS; sau bổ sung kiểm suffix, golden feature và exporter lỗi, tổng30test telemetry nằm trong276test PC PASS/2POSIXskip. Wrapper74statements/16branches100%,0excluded trên Python3.13.14/Windows. Mutation và rollout native còn phải chạy; coverage không thay cho chúng.

Lượt Linux đầu source`ff26518` đã278test PASS/không skip/32,28giây, cùng coverage74/16 100%. Mutation dừng ở bước `also_copy` vì mutmut3.8 chưa tạo `mutants/tests/api` trước khi chép từng file. Cấu hình sửa sang chép cả thư mục test API; giữ nguyên source wrapper, pin dependency, phạm vi một module và mọi ngưỡng kiểm. Lượt lỗi được giữ riêng, không có score hợp lệ từ nó.

Mỗi container chạy một worker, có registry riêng ở `/telemetry/metrics`. HTTP histogram cung cấp tổng số request, phân bố status, duration và tốc độ khi dùng `rate(..._count[1m])`. Healthcheck và scrape không tính vào business request. Bản pin ASGI0.66b1 dùng nhãn `http_target` chứa **route template** cho histogram cũ; yêu cầu tới30mã máy khác nhau vẫn vào cùng series,404 không tạo series cho từng URL lạ. Provider View chỉ giữ method/status/template. Service name cố định theo API.

Trace dùng OpenTelemetry SDK1.45.1 và FastAPI/urllib instrumentation0.66b1. Kiểm HTTP thật trên PC đã thấy span client từ `asyncio.to_thread` có cùng trace với server và đúng parent/header W3C. [Exporter projection](../services/observed.py) giữ định danh/time/parent/kind và method/status/template, bỏ URL/query/header/exception events, links và status description trước khi gửi OTLP. Không lưu payload feature hoặc Authorization. Chưa claim đã thấy chặng Feast SDK hoặc Redis vì chúng chưa được instrument ở đây.

[API chính thức của Prometheus reader](https://opentelemetry-python.readthedocs.io/en/latest/exporter/prometheus/prometheus.html) cho phép registry riêng và provider View giới hạn attributes. [OTLP HTTP exporter](https://opentelemetry-python.readthedocs.io/en/latest/exporter/otlp/otlp.html) nhận endpoint/timeout; dự án chỉ nhận receiver Jaeger đã khai báo trong cluster, timeout2giây. Span/batch dùng queue hữu hạn mặc định SDK; không bảo đảm không mất trace khi backend lỗi hoặc quá tải.

[Jaeger2.22.0](https://github.com/jaegertracing/jaeger/releases/tag/v2.22.0) có release06/10/2026; [asset declaration](../infra/observability/jaeger-assets.json) pin SHA256/byte. [Manifest private](../infra/observability/jaeger.yaml) chuẩn bị receiver/query1replica,512MiB, memory tối đa2.000trace, không public service. Cấu hình theo [tài liệu Jaeger2](https://www.jaegertracing.io/docs/2.22/deployment/configuration/); dữ liệu memory mất khi restart. YAML hiện chưa chứng minh đã deploy.

## Cách kiểm bản chuẩn bị

Trong môi trường API riêng đã cài các pin hiện có và [overlay dependencies](../infra/observability/requirements.lock):

```powershell
python -m pytest tests/api/test_observed.py -q --cov=services.observed --cov-branch
```

[Runner native](../scripts/test-api-observability.py) sẽ kiểm source seal, sao chép sang scratch mới, chạy regression cả ba API không skip, rồi mutation **chỉ module wrapper mới** với coverage>90% cả statement/branch và mutation>80%, không tính timeout/skip/chưa xong. Cấu hình7module của servingCI8 và kết quả cũ giữ riêng; không dùng score cũ để chứng minh wrapper này. Runner không deploy hoặc sửa nguồn/model/lease.

Bước tiếp theo là kiểm Linux/mutation trước, build image overlay có kiểm parent image, rollout riêng trên VM, đối chiếu số request bằng ledger workload độc lập, đọc cùng metrics qua Prometheus/Grafana và trace qua Jaeger. Chỉ chuyển trạng thái rubric khi có readback/ảnh thật.
