# Thu log khi Pod thay đổi

Ngày28/10 trên VM Linux8CPU/64GiB, Alloy1.21.2 đã chuyển từ file chứa PodUID cố định sang `Kubernetes discovery → relabel → file match → CRI parser → Loki`. Giữ Loki3.7.9/store/PID và ba pipeline host trước đó. [Final readback](evidence/log-discovery-linux/final-readback/verification.json): **hai PodUID,40dòng**, khớp từng timestamp nanosecond/nội dung/UID; không tăng dòng sau restart collector. Restart thêm để harden cũng giữ đúng40dòng.

Token ngắn hạn mới dùng SA`iot-api-metrics`, chỉ đọc Pod hai namespace`iot-features`/`iot-serving`; API HTTPS kiểm CA thật. [Requests thật](evidence/log-discovery-linux/start/rbac.json): hai namespace200; namespace default/Secrets/Nodes403. Token hết23:10:59UTC, trước autoSTOP23:13:58; giá trị không vào Git. Chỉ lấy node`iot-cw-vm`, phaseRunning, container ứng dụng trong allowlist; không thu Redis, init hay release cũ. [Renderer](../ml/log_discovery.py), [scoped discovery](https://grafana.com/docs/alloy/latest/reference/components/discovery/discovery.kubernetes/).

![Đường dẫn sai được quan sát trong Alloy thật](evidence/log-discovery-linux/screenshots/before-path.jpg)

![Đường dẫn đúng namespace, Pod và UID sau sửa](evidence/log-discovery-linux/screenshots/correct-path.jpg)

Hai ảnh gốc từ UI Alloy qua IAP/loopback. `$1_$2_$3` bị diễn giải thành tên nhóm có dấu`_`, làm mất namespace/tên Pod. Sửa thành`${1}_${2}_${3}`; [before/after](evidence/log-discovery-linux/repair/verification.json) chứng minh chỉ thay biểu thức đó. [Semantics capture groups](https://grafana.com/docs/alloy/latest/reference/components/discovery/discovery.relabel/).

![Grafana thật có log host và ứng dụng, khoảng6giờ](evidence/log-discovery-linux/screenshots/grafana-discovered-logs.jpg)

Ảnh giữ WARN thực và restart AB có lease cũ đã hết; nó chứng minh quan sát log, không chứng minh mọi API healthy. Demo sau cần prepare feature/context mới thật, không sửa timestamp/TTL.

| Bước | Kết quả thật |
|---|---|
| Probe thiếu seccomp | Rollout timeout, không Pod/CRI. Admission restricted được đọc trong phiên; capsule giữ timeout/manifest/checkpoint, không có bản lưu riêng event ReplicaSet đó. |
| Bổ sung RuntimeDefault | Pod`f18643cc-84a3-44c0-9b87-f9da2560df0d` ghi20dòng, Loki rỗng do path sai |
| Sửa relabel | Giữ nguyên Pod/20dòng gốc, không phát lại; collector đọc được dữ liệu bị bỏ sót |
| Thay template sang batch second | Pod`0384b000-1660-4303-8392-f656eb46cbee` ghi20dòng khác; tổng40 |
| Restart, cùng positions/collection | PID collector đổi;40dòng giữ nguyên, Loki PID không đổi |
| Đọc API hiện tại | Query cuối có AB24/drift4/feature4/predictor4dòng; không nhận là tổng log toàn hệ thống |

Startup log cũ chỉ query được sau một [POST /flush xuống backing store](https://grafana.com/docs/loki/latest/reference/loki-http-api/#flush-in-memory-chunks-to-backing-store). Cấu hình thật có query_ingesters_within3h/query split1h; kết quả phù hợp giới hạn ingester cho log backfill cũ, không chứng minh mọi nguyên nhân bên trong query engine. Giữ cả [settings](evidence/log-discovery-linux/final-readback/query-settings.txt), query rỗng và query sau flush; không sửa timestamp hoặc restart Loki. Grafana UID/data source vẫn`iot-linux-logs`/`iot-logs-loki`; chọn collection`native-28-auto` và khoảng6giờ để thấy startup cũ. Service ít ghi log có thể không xuất hiện trong khoảng ngắn hơn.

Collector root đọc CRI root-owned,768MiB/0,5CPU/NoNewPrivileges. [Hardening](evidence/log-discovery-linux/hardening/unit.txt): ProtectSystem=strict, bỏ capabilities, private temp, chỉ positions/WAL riêng được ghi. Loki/store và state collector trước giữ nguyên; token thật chỉ ở runtime. Chưa claim exactly-once với crash bất kỳ, multi-node hoặc mọi dạng rotation.

```powershell
python -S scripts/verify-log-discovery.py
python -m unittest tests.test_log_discovery tests.test_log_monitoring -v
```

Verifier stdlib/Git:126payload1017096byte/122nativefile/native manifest/3JPEG,27Gitbindings, Counter bắt cả thiếu lẫn trùng. [Manifest](evidence/log-discovery-linux/manifest.json). Source initial`1c2c30d`/6file; checker`2572305`/7, seccomp`23fe11f`/7, path+resume`876c79f`/7. BinarySHA`8cc367523e0d2777683acf00d84c5556a18ff74f74c137a2f4a87605b8b86db4`. Ready20:48:30UTC/final21:02:34/hardened21:04:27.

Runtime`data/runtime/log-discovery-28`, unit`iot-alloy-discovery-28`; oldunit`iot-alloy-28-keep` đã dừng, state không xóa. **Các batch đã chạy xong: không chạy lại [checker](../scripts/check-log-discovery.py) để xem kết quả cũ.** Nó dành cho phép đo mới/output mới; resume chỉ nhận checkpoint cùng Pod/CRI chưa có second batch. Không bật hai collector đọc cùng collection/positions. Phiên VM mới cần token hợp lệ mới, không chỉnh TTL để giả còn hạn.
