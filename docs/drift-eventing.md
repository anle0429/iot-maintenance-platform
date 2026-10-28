# Drift API qua Knative Eventing và KServe

Mục F35 cần CI/CD drift API với Knative Eventing kết hợp KServe. Phần này đang triển khai; chưa có application delivery hoặc Jenkins SUCCESS cho F35.

Chọn [Knative Eventing1.24.0](https://github.com/knative/eventing/releases/tag/knative-v1.24.0), phát hành27/10/2026; [lịch hỗ trợ chính thức](https://github.com/knative/community/blob/main/mechanics/RELEASE-SCHEDULE.md) yêu cầu Kubernetes≥1.35, phù hợp cluster1.35.9 hiện có. [Asset declaration](../infra/eventing/assets.json) pin byte/SHA256 theo GitHub release API và official checksums. Không dùng lịch release để suy rằng artifact đã tồn tại: release/API đã đọc trực tiếp trước khi chọn.

[Installer](../scripts/install-eventing.py) dành cho VM hiện có, yêu cầu còn90phút phiên/24GiB RAM/20GB trống mỗi data disk; từ chối namespace Eventing đã tồn tại. Các manifest official chỉ dùng serviceClusterIP; không tạo VM/đĩa/Internet ingress. Controller/channel/broker Ready chưa chứng minh luồng nghiệp vụ.

Theo [hướng dẫn cài YAML](https://knative.dev/docs/admin/install/eventing/install-eventing-with-yaml/), cần CRDs/core/InMemoryChannel/MTChannelBasedBroker. Broker memory phù hợp lab nhỏ, không nhận durability qua restart. Dự kiến gửi CloudEvent binary: attributes ở HTTP headers, JSON window ở body, theo [HTTP binding1.0.2](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/bindings/http-protocol-binding.md). Giữ event time mô phỏng tách khỏi thời điểm truyền thật; phải kiểm nhận/route/dedup bằng native counters/ledger trước khi xác nhận F35.
