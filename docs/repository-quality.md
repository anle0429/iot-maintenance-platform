# Kiểm tra chất lượng repo và docstrings

Commit source cố định `b8f0ef81d9d5305ad63a9e3099435801499c5f0c` đã chạy toàn bộ `pytest tests` trên **PC Windows/Python3.13.14**: **732 tests đạt, 292 subtests đạt, 7 skipped**, không failure/error. Pytest wall67,21giây; JUnit ghi67,160giây. Header JUnit đếm1031 gồm739 testcase và292subtests; không nhận1031 test độc lập.

[Console gốc](evidence/repository-audit/frozen/pytest.stdout), [JUnit](evidence/repository-audit/frozen/junit.xml), [source trước](evidence/repository-audit/frozen/source-before.json)/[sau](evidence/repository-audit/frozen/source-after.json), [báo cáo](evidence/repository-audit/frozen/verification.json). 307file Python được bind Git và kiểm hash working tree không đổi trong cả lượt; khác newline Windows được ghi rõ. Đây là kiểm trên PC, không phải chạy lại coverage/mutation/load hoặc deployment Linux.

## Phạm vi sửa tài liệu trong code

Đã thêm419docstrings ở75file đang bảo trì. Trong đó418mô tả ở74file có executable AST trước/sau giống nhau sau khi bỏ riêng docstrings; một mô tả còn lại nằm trong verifier A/B đồng thời được sửa cách so source lịch sử. [Danh sách và hash](evidence/repository-audit/docstring-edits.json) phân biệt hai nhóm. `__doc__`, help text và số dòng source có thay đổi có chủ đích; không gọi đây là toàn bộ bytecode/metadata không đổi.

Audit tại commit trên có **307file/1637định nghĩa**, không thiếu mô tả đầu module/hàm/class, không có data/outputs/venv/pyc/state hoặc file điều khiển chat được track. Snapshot code cũ trong `docs/evidence/` được giữ nguyên để bảo toàn as-run hashes; chúng không bị sửa ngược cho đẹp báo cáo. Các module verifier thêm sau lượt frozen cũng được kiểm docstring khi chạy bộ kiểm bàn giao.

Verifier gateway/mesh/Vault và A/B vẫn kiểm đúng **byte Git của source thực chạy**. Khi đối chiếu mã hiện tại, chỉ cho phép khác docstrings Python; mọi AST thực thi khác vẫn bị từ chối, mọi config ngoài Python vẫn cần byte đúng. Ba tests biết đáp án kiểm việc cho phép mô tả nhưng chặn đổi logic/default/decorator, string không phải docstring và JSON. Đây là thay đổi ở công cụ audit, không gán source mới cho deployment cũ.

## Kết quả bổ sung và giới hạn

19lệnh kiểm đều exit0: audit, pytest, pip check, package freeze và15verifier evidence. Các verifier kiểm API, policy, IaC, logs, MMD, DataHub, gateway, mesh, Vault, hai job stream, eventing, serving, telemetry, scheduled drift và A/B. Report/console từng lệnh giữ trong [capsule47payload](evidence/repository-audit/manifest.json).

Bảy skip được giữ nguyên lý do: hai phép đổi timezone bằng `tzset` chỉ có POSIX; hai integration cần exporter/Prometheus/Grafana binary thật; một phép thử cần `promtool`; hai phép codec cần `orjson` tùy chọn. Không thay binary thật bằng fixture để biến skip thành PASS. Bằng chứng native tương ứng có tài liệu riêng; lượt PC này không nhận đã chạy chúng.

Lượt collection ban đầu thiếu `opentelemetry.sdk`, exit2. Đã bổ sung đúng [overlay đã khóa](../infra/observability/requirements.lock) vào venv riêng, không nâng dependency nghiệp vụ; giữ [console lỗi gốc](evidence/repository-audit/initial-collection/console.stdout). Lượt frozen sau đó pip check đạt và lưu [freeze thực tế](evidence/repository-audit/frozen/freeze.stdout).

## Tự kiểm từ clone

Không cần VM/package ngoài standard library để kiểm artifact:

```powershell
python -S scripts/verify-final-handover.py
```

Tạo môi trường riêng để chạy toàn tests theo đúng package freeze của lượt PC:

```powershell
python -m venv outputs/venv-check
./outputs/venv-check/Scripts/python.exe -m pip install -r docs/evidence/repository-audit/frozen/freeze.stdout
./outputs/venv-check/Scripts/python.exe -m pytest -q tests --hypothesis-seed=20261028
```

Audit source hiện tại, chọn output mới:

```powershell
python scripts/audit-repository.py --output outputs/my-repository-audit --edit-proof docs/evidence/repository-audit/docstring-edits.json
```

Package/runtime, dữ liệu và dịch vụ riêng từng máy; clone kiểm trên PC không chứng minh laptop đã chạy. [Pattern Adapter/DI/factory và ảnh source gốc](ml-class-design.md) giải thích thiết kế; [diagram](../README.md#system-diagram) dùng đơn vị có thể triển khai, không vẽ Feast SDK thành server.

## Kiểm bản phát hành có fullcohort

Clone PC từ GitHub main tại`5535bcffc013b1a9464133f69239cdb963183bb2` đã qua736 tests,297 subtests,7 skips trong môi trường Python riêng từ freeze đã công bố. Pip check, window/fullhandover/MMD/OPA và suite đều exit0; clone sạch trước/sau. [14 payload gốc](evidence/final-release-pc/manifest.json) giữ JUnit/console và receipt. Đây là lượt mới sau audit732/292, không thay số đo frozen cũ và không chứng minh đã chạy trên laptop. Tự kiểm cả hai lượt bằng `python -S scripts/verify-final-handover.py`.
