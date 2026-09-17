# Hướng dẫn trình bày dự án

## Bài nói 90 giây

“Em xây ShopPilot AI, một nền tảng sales agent đa cửa hàng cho shop online. Khác chatbot FAQ, agent có thể tìm sản phẩm, kiểm tra tồn kho, tra cứu chính sách, tính vận chuyển và tạo đơn nháp bằng tool calling. Em tách dữ liệu theo shop_id để nhiều shop dùng chung hệ thống mà không lẫn dữ liệu.

Với hành động làm thay đổi dữ liệu, em không cho LLM tự xác nhận. Agent chỉ tạo draft; workflow Python yêu cầu khách xác nhận ở lượt sau, kiểm tra lại tồn kho rồi mới trừ hàng. Trường hợp khiếu nại hoặc thiếu căn cứ được chuyển nhân viên cùng toàn bộ ngữ cảnh.

Hệ thống dùng FastAPI, SQLite, Groq và giao diện web responsive. Em thêm audit trace, 11 unit/integration tests và 16 evaluation scenarios để đo tool routing, tenant isolation và safety workflow.”

## Câu hỏi thường gặp

### Tại sao không làm chatbot đơn giản?

Chatbot chỉ tạo ngôn ngữ. Bài toán bán hàng cần dữ liệu thay đổi liên tục và hành động có hậu quả, nên phải dùng tool, state và guardrail trong code.

### Nếu LLM bịa giá thì sao?

System prompt cấm suy đoán, nhưng lớp bảo vệ chính là giá và tồn kho chỉ đến từ tool. Trace lưu lại nguồn. Write action không do model trực tiếp thực hiện.

### Multi-tenant được bảo vệ thế nào?

API resolve shop trước khi tạo `ShopTools`. Mọi truy vấn product đều nhận `shop_id`. Test tenant isolation chứng minh truy vấn ở shop mỹ phẩm không trả sản phẩm điện tử.

### Tại sao cần deterministic fallback?

LLM API có thể lỗi hoặc hết quota. Fallback giữ các luồng cốt lõi hoạt động và tạo baseline để so với model online.

### 100% evaluation có nghĩa hệ thống hoàn hảo không?

Không. Kết quả chỉ đúng trên 16 scenario đã định nghĩa ở chế độ offline. Cần mở rộng dữ liệu, chạy online eval, human review và theo dõi production trước khi dùng thật.

### Hạn chế hiện tại là gì?

Catalog và shipping đang là dữ liệu mô phỏng; chưa kết nối Sapo, Haravan, KiotViet hay đơn vị vận chuyển. SQLite phù hợp demo, production nên dùng PostgreSQL, authentication, RBAC và mã hóa thông tin khách hàng.

## Dòng CV đề xuất

> Built ShopPilot AI, a multi-tenant commerce agent using FastAPI, Groq and tool calling; implemented catalog-grounded recommendations, human handoff, auditable traces, and confirmation-gated order workflows, validated with automated tests and scenario-based evaluation.
