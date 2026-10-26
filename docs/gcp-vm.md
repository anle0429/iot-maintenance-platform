# Chuẩn bị Google Cloud VM cho coursework

Tài liệu này ghi giai đoạn chuẩn bị ngày26/10/2026: đăng nhập CLI/ADC, kiểm project/billing/quota, validate/mock tests và lập plan trước khi tạo. **Sau đó đã chọn accelerated, apply và chạy benchmark thật; xem [trạng thái runtime mới nhất](gcp-runtime.md).** Credit trial còn lại chưa được xác minh độc lập qua Billing UI.

## So sánh trước khi tạo VM theo yêu cầu tăng tốc

Người dùng đã đồng ý tạo VM basic, đồng thời yêu cầu kiểm cách tăng tốc trước khi tạo. Đã bổ sung [ba profile](../infra/gcp/capacity-profiles.json), tính giá/lập plan, rồi người dùng chọn **accelerated và máy đó đã được tạo**. Các thông số basic ở phần dưới là lịch sử plan ban đầu; không sửa bằng chứng của plan đó.

| Profile | vCPU / RAM | Boot / scratch SSD balanced | Data HDD | 8h chạy +24h giữ đĩa, gồm30% | 120h liên tục để so giá, gồm30% |
|---|---|---|---|---:|---:|
| basic | 4 /16GiB | không; boot50GiB HDD | 500GiB | 2,39USD | 26,39USD |
| standard | 8 /32GiB | 50GiB +150GiB | 500GiB | 4,55USD | 51,14USD |
| **accelerated, đề xuất** | **8 /64GiB** | **50GiB +150GiB** | **500GiB** | **5,52USD** | **65,74USD** |

Accelerated là `e2-highmem-8`, đã đọc machine type thật8vCPU/65536MiB. Tổng SSD200GiB nằm trong quota250GiB, vCPU8 nằm trong quota toàn project12. Máy16vCPU vượt quota toàn project ở snapshot này, Free Trial không cho yêu cầu tăng quota. Dành lại4CPU quota giúp còn chỗ cho dịch vụ/VM khác khi thực sự cần; đây không phải tài nguyên đã đặt chỗ. Chưa xác minh credit còn lại, chưa bảo đảm capacity VM sẵn có.

Giá VM niêm yết Iowa: basic0,13402284USD/h, standard0,26804568USD/h, accelerated0,36159864USD/h; bảng trên còn gồm ba đĩa/IP theo từng profile, không gồm mạng/log/snapshot/thuế/tỷgiá. Session vẫn tự STOP sau8h/start; hàng120h chỉ so giá, không tự cho phép chạy liên tục120h. VM accelerated dừng nhưng ba đĩa còn giữ thì khoảng **1,315073USD/ngày** trước dự phòng. Credit300USD là ưu đãi dành cho tài khoản đủ điều kiện, không phải lời bảo đảm mọi phần coursework đều miễn phí.

Lợi ích dự kiến là RAM để chạy các service đỡ phải bật/tắt liên tục, SSD cho Docker/database/shuffle và nhiều CPU hơn cho Spark. I/O phụ thuộc cả loại/kích thước đĩa và vCPU; chưa đo speedup thật. Việc cần thực hiện khi triển khai:

1. Mount HDD cho nguồn/output bền vững; dùng SSD riêng cho Docker state và Spark scratch. Scratch150GiB vẫn phải đo đỉnh dung lượng, chưa lấy cumulative spill làm peak hoặc nhận đủ100GB.
2. Hiện script PC còn local[2]/heap1GiB/2CPU. Profile accelerated gợi ý local[6], heap20GiB, container28GiB, shuffle48; **chưa tự áp dụng vào runtime**, phải kiểm pilot và cùng workload trước/sau rồi điều chỉnh. Phần RAM còn lại dành cho OS/platform, tránh bật mọi job nặng cùng lúc.
3. Generator còn một tiến trình Python nên tăng số vCPU không tự làm sinh dữ liệu nhanh gấp tương ứng. Sau pilot đo throughput/đĩa, có thể cho sinh lớn chạy có log trong lúc triển khai các service khác; không ghép seed tùy tiện, bỏ kiểm đúng hoặc nhận ngoại suy là100GB. Session dài cần xem lại trước job dài để tránh bị STOP giữa chừng.
4. Dùng lại image/version/source và triển khai theo thứ tự phụ thuộc Airflow→DataHub→feature/model→serving/agent; cloud giảm nút thắt tài nguyên, code/kiểm chứng từng tiêu chí vẫn cần hoàn tất.

Sau thay đổi profile: **7 mock plan tests**, **7 quota checks**, plan accelerated **14 add/0 change/0 destroy**. [Verification](evidence/gcp-options/verification.json), [mock log](evidence/gcp-options/terraform-test.txt), [quota](evidence/gcp-options/quota-checks.json), [chi phí accelerated](evidence/gcp-options/accelerated.json). Raw plan/account values giữ ngoài Git. Muốn lập lại plan dùng `./scripts/prepare-gcp.ps1 -Action Plan -Profile accelerated -ProjectId YOUR_PROJECT_ID -RunName cloud-fast-practice`; máy mới mặc định basic; nếu có profile cục bộ thì giữ lựa chọn đó, không âm thầm đổi cấu hình.

## Vì sao thêm cloud vào dự án

Rubric M2-44 yêu cầu Terraform tạo GKE hoặc dịch vụ cloud; M2-45 yêu cầu Ansible cấu hình/deploy lên VM. Máy phát triển đã gặp giới hạn RAM và ngân sách đĩa cho offline100GB chưa đủ. VM là bước hạ tầng phục vụ chạy theo đợt; vẫn giữ code/test nhỏ trên PC/laptop. Airflow DP1–DP3/DataHub và 100GB còn là các mốc cần triển khai, không đổi lịch kết thúc tạm31/10.

## Phương án đã chuẩn bị

| Thành phần | Cấu hình trong plan |
|---|---|
| VM | Một `e2-standard-4`, 4 vCPU/16 GiB RAM, `us-central1-a` |
| OS | Ubuntu24.04 LTS amd64 family; cần ghi image version thực khi apply |
| Boot disk | 50 GiB `pd-standard`, xóa cùng VM |
| Data disk | 500 GiB `pd-standard`, tách khỏi boot, bảo vệ bằng `prevent_destroy` |
| Giới hạn phiên | GCE `max_run_duration=28800s`, hành động STOP; chưa kiểm thực việc tự dừng |
| Network | VPC/subnet riêng; một IPv4 ephemeral Standard để tải package/image |
| SSH | Chỉ ingress22 từ IAP `35.235.240.0/20`, OS Login; không mở HTTP/UI/database public |
| Workload identity | Service account riêng chưa được cấp quyền dữ liệu/project, không tạo key JSON |
| Runtime | Terraform1.16.6, Google provider8.8.1 có lockfile, Google CLI590.0.1 |

500GiB provisioned =536.870.912.000byte trước filesystem; vẫn phải đo free space, source/output/shuffle/log trên VM trước sinh lớn. Đĩa standard là HDD, cần đo I/O cho workload thực; dung lượng đủ theo ước tính không chứng minh tốc độ hoặc đã xử lý100GB. Không bật toàn bộ platform cùng lúc hoặc mặc định16GiB đủ cho KFP/KServe/LLM.

Plan ban đầu dùng500GiB pd-balanced bị loại sau khi đọc quota: `SSD_TOTAL_GB=250`, usage0. Chuyển sang pd-standard500GiB, cùng boot50GiB, phù hợp `DISKS_TOTAL_GB=2048`. Tại snapshot, E2_CPUS còn24, global CPUS_ALL_REGIONS còn12, IPv4 còn8 và instances còn24. Quota có thể thay đổi và không bảo đảm VM capacity sẵn có khi apply.

## Chi phí dự toán, chưa phải hóa đơn

[Rates snapshot](../infra/gcp/pricing-us-central1.json) dùng giá niêm yết USD Iowa, không trừ các ưu đãi miễn phí/Spot/CUD để tránh hạ thấp dự trù. [Công cụ tính](../infra/gcp/estimate.py) tách giờ chạy VM/IP khỏi giờ giữ đĩa:

| Kịch bản | VM chạy | Đĩa giữ | Tổng cơ sở | Cộng30% dự phòng |
|---|---:|---:|---:|---:|
| Phiên đầu | 8giờ | 24giờ | 1,835477USD | **2,386120USD** |
| Chạy liên tục5ngày, chỉ để so ngân sách | 120giờ | 120giờ | 20,299211USD | **26,388974USD** |
| VM dừng, chỉ giữ hai đĩa thêm1ngày | 0giờ | 24giờ | **0,723294USD** | 0,940282USD |

Chưa gồm truyền dữ liệu ra Internet, log vượt hạn mức, snapshot, dịch vụ khác, thuế và tỷ giá. Dự phòng30% không phải giới hạn chi tiêu. Session mặc định dừng sau8giờ mỗi lần khởi động, không thể chạy liên tục120giờ theo cấu hình hiện tại; hàng thứ hai chỉ là phép so chi phí và sẽ cần xem lại cấu hình nếu đổi thời lượng. Mức3,67USD trong dự toán trước dùng pd-balanced đã được thay bằng phương án standard mới.

Free Trial dành cho tài khoản đủ điều kiện có300USD/90ngày và hạn chế GPU/tăng quota. Chưa nâng cấp Paid billing. Chỉ tạo tài nguyên sau khi người dùng duyệt plan/cấu hình/chi phí; theo dõi Credit/Billing trong Console. Cảnh báo ngân sách dạng alerts-only không tự chặn chi tiêu. STOP giữ dữ liệu và dừng compute, **đĩa vẫn tính phí**. Data disk có prevent_destroy nên không chạy destroy mù để dọn chi phí; phải xem dữ liệu cần giữ và duyệt việc xóa riêng.

## Code và cách kiểm tra

[Module Terraform](../infra/gcp/vm/main.tf) quản lý13 resource actions:4 API registrations, VPC/subnet/firewall, service account, IAM cho người vận hành, data disk và VM. Project đã có sẵn, không tạo project/billing mới. IAM chỉ gán cho principal người dùng cấu hình cục bộ; không cấp allUsers/khóa tài khoản dịch vụ.

Từ repo root PowerShell7:

```powershell
./scripts/prepare-gcp.ps1 -Action Validate -RunName cloud-local-practice
./scripts/prepare-gcp.ps1 -Action Inspect -ProjectId YOUR_PROJECT_ID -RunName cloud-inspect-practice
./scripts/prepare-gcp.ps1 -Action Plan -ProjectId YOUR_PROJECT_ID -RunName cloud-plan-practice
```

[Script](../scripts/prepare-gcp.ps1) không có chế độ apply. Validate chỉ init/fmt/validate/mock tests và tính giá cục bộ. Inspect chỉ đọc project/billing/API/quota/machine type. Nếu Compute API chưa bật thì dừng, không tự bật trong script. Plan kiểm quota trước, dùng ADC để ghi một saved plan; không gọi apply. Bật API trong lần chuẩn bị thực tế đã được ghi riêng ở log cục bộ.

Windows dùng Python Microsoft Store có thể chuyển ADC sang AppData LocalCache; helper tìm đúng tệp và đặt GOOGLE_APPLICATION_CREDENTIALS trong tiến trình, khôi phục biến môi trường sau khi chạy. Không xuất token/refresh token vào terminal và không yêu cầu tải service-account key. `terraform.tfvars.json` chứa project/operator thật chỉ nằm cục bộ, ngoài Git.

Mock tests kiểm footprint, IAP-only SSH, OS Login/Secure Boot, ephemeral IP và STOP8h; bốn ca khác từ chối VM lớn hơn, allUsers, thời gian24h và đĩa1000GiB. Đây là kiểm hợp đồng plan, không chứng minh VM/OS Login/auto-stop đã chạy thật. [Verification/sanitized plan](evidence/gcp-prepare/verification.json), [mock log](evidence/gcp-prepare/terraform-test.txt), [quota snapshot](evidence/gcp-prepare/quota-checks.json) và [estimate](evidence/gcp-prepare/cost-estimate.json). Raw cloud plan/JSON và danh tính tài khoản chỉ giữ trong outputs, không đưa vào Git.

## Sau khi plan được duyệt

Apply đúng saved plan đã xem lại, kiểm VM/disk/IAP/OS thực. Sau đó dùng Ansible qua môi trường điều khiển Linux để mount đĩa mới an toàn, cài Docker và chạy lại pilot; không mount/format đĩa đang chứa dữ liệu một cách tự động. Đo RAM/đĩa/I/O rồi triển khai Airflow/validation/DataHub và quyết định lần chạy100GB. Lưu evidence máy cloud, source bytes, versions và code hashes thực; local plan không thay bằng chứng deploy của rubric.

## Tài liệu chính thức

- [Google provider8.8.1 compute instance](https://github.com/hashicorp/terraform-provider-google/blob/v8.8.1/website/docs/r/compute_instance.html.markdown), [Terraform mock provider](https://developer.hashicorp.com/terraform/language/tests/mocking).
- [GCE VM runtime limit](https://docs.cloud.google.com/compute/docs/instances/limit-vm-runtime), [IAP TCP forwarding](https://docs.cloud.google.com/iap/docs/using-tcp-forwarding), [OS Login](https://docs.cloud.google.com/compute/docs/oslogin/set-up-oslogin).
- [VM pricing](https://cloud.google.com/products/compute/pricing/general-purpose), [disk pricing](https://cloud.google.com/compute/disks-image-pricing), [IPv4 pricing](https://cloud.google.com/vpc/network-pricing#ipaddress).
- [Free Trial/Free Tier](https://docs.cloud.google.com/free/docs/free-cloud-features), [budgets và alerts](https://docs.cloud.google.com/billing/docs/how-to/budgets).
