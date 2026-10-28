# Xác thực và phân quyền giữa dịch vụ bằng mesh

Istio cấp chứng thư cho danh tính Kubernetes ServiceAccount; Envoy xác thực kết nối mTLS rồi áp dụng quyền gọi API. Header do bên gọi tự gửi không thay thế được danh tính trong chứng thư. Kiểm chứng thực trên `iot-cw-vm` Linux, 8 vCPU/64 GiB, ngày28/10/2026; [kết quả gốc](evidence/private-mesh-linux/workloads-readback/verification.json).

| Ca chạy thật | Kết quả |
|---|---|
| `feature-caller`, POST đúng route model | HTTP200, risk0.10318092377929837; phép tính độc lập sai khác0 |
| `untrusted-caller`, cùng POST | HTTP403 trước ứng dụng |
| `untrusted-caller`, giả header X-Forwarded-Client-Cert | HTTP403; log vẫn ghi SPIFFE untrusted-caller |
| `feature-caller`, GET metadata ngoài quyền | HTTP403 |
| Client không sidecar, HTTP không mTLS | TCP reset; DNS khớp ClusterIP10.53.254.109; Envoy `filter_chain_not_found` |

Envoy đếm1allowed/3denied; ba403 không vào predictor. Bộ đếm dự đoán thành công của ứng dụng bằng1 và toàn bộ metrics sau các yêu cầu bị chặn giữ nguyên so với sau yêu cầu thành công. Lỗi plaintext ở tầng TLS không cộng vào ba lần HTTP RBAC denied. [Log danh tính thật](evidence/private-mesh-linux/workloads-readback/server-proxy-log.stdout), [counters](evidence/private-mesh-linux/workloads-readback/rbac-counters.stdout), [chứng thư công khai](evidence/private-mesh-linux/review/certs.json).

![Bộ đếm thực của Envoy](evidence/private-mesh-linux/screenshots/envoy-rbac.jpg)

![Chứng thư workload thực](evidence/private-mesh-linux/screenshots/envoy-certificate.jpg)

Hai ảnh gốc được lưu trực tiếp từ trình duyệt. Review bridge chỉ nghe loopback, chuyển ba GET allowlist `/stats`, `/certs`, `/logs` sang kết quả native Envoy qua Kubernetes exec/logs và IAP. Đây là trình xem đọc dữ liệu thật, không phải dashboard mesh hay HTTPS được trình duyệt tin cậy. Không export SDS Secret/private key, không mở public port. `/certs` chỉ chứa SAN, serial và thời hạn chứng thư công khai.

## Cấu hình thực và nguồn

- Istio1.31.3, revision`iot-1313`; minimal control plane trong`iot-mesh-system`. 32resource được render/kiểm trước khi cài; các Service là ClusterIP. Nhãn namespace cũ không đổi. [Operator](../infra/mesh/istio-operator.yaml), [asset pin](../infra/mesh/assets.json), [install receipt](evidence/private-mesh-linux/control-plane/ready.json).
- Namespace riêng`iot-mesh`: predictor clone + hai caller có native sidecar; một caller không injection. K3s1.35.9 đặt proxy trong`initContainers` với`restartPolicy: Always`, ready/started/running, không phải one-shot init đã hoàn tất. Image thực`proxyv2` digest`eb74eacea98d3fb1e239ea84016085155478586a554158676b9b67d960151084`; tag trong Podspec là1.31.3. [Pod gốc](evidence/private-mesh-linux/workloads-readback/pods-before.json).
- [PeerAuthentication/AuthorizationPolicy](../infra/mesh/policies.yaml): STRICT mTLS; chỉ principal`cluster.local/ns/iot-mesh/sa/feature-caller` được POST port8080 route`/v1/models/iot-pump-failure-v1:predict`. Sai principal hoặc thao tác khác bị chặn.
- Clone dùng image predictor thực`observe-jenkins-2`, portable model6/run`be3b27fa0960493d92b45be69b20690e`; giữ nguyên9field lineage. Pure predictor factory, không đưa trace riêng của ca mesh vào tập trace trước đó. Input P001 là vector thật được giữ lại từ [mốc gateway](private-gateway.md), có parent SHA; không nhận là truy vấn feature mới/current. Không sửa TTL hoặc promote model7.
- Control source`818271e`/7file; workload đầu`17e08d7`/8file; lần hai`bcd757c`/8file; checker đọc tiếp`56dbf0f`/8file. Tổng31Gitbindings, hash đối chiếu bằng verifier. [Source seals](evidence/private-mesh-linux/sources/resume.json). Control Ready20:30:28UTC; kiểm chứng hoàn tất20:38:17UTC.

Official [Istio release1.31.3](https://github.com/istio/istio/releases/tag/1.31.3), [PeerAuthentication](https://istio.io/latest/docs/reference/config/security/peer_authentication/), [AuthorizationPolicy](https://istio.io/latest/docs/reference/config/security/authorization-policy/) giải thích phiên bản và semantics sử dụng. Archive30393605byte/SHA`b32bba09bc6138d738fd19a09ef6d0fcdd5c5c013efeb72969663c24a2bddd47`; binarySHA`c46acad9975e694e503aad476ce8730db991cfd56232e88118d9440c38a5be05`. [Download receipt](evidence/private-mesh-linux/preflight/download.json).

## Tự kiểm tra trên clone và chạy lại

Chỉ cần Python3.11+ và lịch sử Git đầy đủ để kiểm tra bằng chứng, không cần VM hay dependency bên ngoài:

```powershell
python -S scripts/verify-private-mesh.py
python -m unittest tests.test_mesh_contract -v
```

Unit tests cần PyYAML trong môi trường phát triển; verifier chỉ dùng stdlib. Nó kiểm byte/hash, source Git, response và math từ portable model,5log identity, counters/certs, same PodUID, checkpoint, policy scope và serving/feature UID/spec trước sau.106payload/4103855byte gồm native manifest và2JPEG; native103file/3983052byte. Tar gốcSHA`d7a06363330c45f4097adfac5a324593f8832248e83c66faa61f57147cdaf8d9`. [Manifest](evidence/private-mesh-linux/manifest.json). Generated full install YAML không nằm trong capsule; summary/hash giữ lại. Không có credentials/private keys.

Trên VM mới đã có K3s, cached image/model6, Istio assets đã kiểm SHA và phiên được phép còn tối thiểu30phút, các entry point là `scripts/install-private-mesh.py` rồi `scripts/check-private-mesh.py`, xem `--help`. Chúng yêu cầu output mới và namespace chưa tồn tại. **Trên VM hiện tại đã chạy xong: không chạy lại installer/checker từ đầu**, vì đó là phép đo mới, không phải xem kết quả cũ. Xem kết quả lưu hoặc dùng kubectl đọc Pod/policy/logs. `--resume-from` chỉ dùng cho checkpoint chưa gửi request, khớp manifest/image/PodUID; không dùng để lặp lại ca đã đo.

## Lỗi đã gặp và giới hạn

1. Assert định dạng`istioctl version --short` quá chặt sau download SHA đúng; đọc version JSON để tiếp tục, không tải lại/cài hai lần.
2. Checker đầu tìm`model-pin.json` không tồn tại trong image; dừng trước tạo namespace. Giữ [stderr gốc](evidence/private-mesh-linux/workloads/baked-model.stderr), đổi sang loader lineage thật`PumpPredictor.pin`.
3. Lần hai tạo bốn Pod Ready nhưng guard chỉ tìm regular container; dừng trước gửi request. Bản56 nhận native sidecar và đọc tiếp checkpoint **không apply/restart**, cùng toàn bộ PodUID. Giữ cả [checkpoint](evidence/private-mesh-linux/workloads-v2/pods-before.json) và [resume receipt](evidence/private-mesh-linux/workloads-readback/resume.json).
4. Khi viết verifier offline, assert digest nhầm trường spec.image (tag) thay vì status.imageID (digest); sửa cách đọc hai trường, không thay bằng chứng native.

Chứng thư workload model-server hợp lệ20:32:59UTC28/10 đến20:34:59UTC29/10. Đây là bằng chứng cấp và sử dụng chứng thư đang hợp lệ, chưa chứng minh rotation24giờ. Không nhận HA, throughput tối đa, multi-node, hay migration production FeatureAPI→Predictor. Các caller là identity probes, không phải FeatureAPI sản phẩm. Serving/feature đang dùng giữ UID/spec; dynamic health có thể thay đổi khi feature lease hết hạn. Mốc này không thay thế việc chuẩn bị feature thật cho lần demo sau.

Khi trình bày: chỉ ra route/principal được phép, gọi ca đúng/sai/giả header/plaintext, đối chiếu SPIFFE trong log và bộ đếm ứng dụng. Giải thích rằng gateway xác thực người gọi từ ngoài; mesh bổ sung danh tính và quyền **giữa các dịch vụ**.
