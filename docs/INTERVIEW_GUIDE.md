# Hướng dẫn trình bày dự án

## Bài nói 90 giây

“Em xây ShopPilot AI, một nền tảng sales agent đa cửa hàng cho shop online. Khác chatbot FAQ, agent có thể tìm sản phẩm, kiểm tra tồn kho, tra cứu chính sách, tính vận chuyển và tạo đơn nháp bằng tool calling. Em tách dữ liệu theo shop_id để nhiều shop dùng chung hệ thống mà không lẫn dữ liệu.

Với hành động làm thay đổi dữ liệu, em không cho LLM tự xác nhận. Agent chỉ tạo draft; workflow Python yêu cầu khách xác nhận ở lượt sau, kiểm tra lại tồn kho rồi mới trừ hàng. Trường hợp khiếu nại hoặc thiếu căn cứ được chuyển nhân viên cùng toàn bộ ngữ cảnh.

Hệ thống dùng FastAPI, PostgreSQL/SQLite, Groq và giao diện web responsive. Unified inbox nhận diện tín hiệu mua, đưa ca cần người lên đầu và cảnh báo SLA năm phút. Khi nhân viên tiếp quản, Copilot tóm tắt và soạn sẵn câu trả lời nhưng không tự gửi. Chủ shop kết nối Page bằng Meta OAuth nên không chia sẻ mật khẩu; Page token được mã hóa và ánh xạ theo tenant. Em thêm durable queue, quản lý team, vận hành đơn hàng, Stripe billing, account recovery, shared rate limit, production metrics, human feedback, 65 automated tests, 13 browser E2E checks và 16 evaluation scenarios để đo tool routing, tenant isolation và safety workflow.”

## Câu hỏi thường gặp

### Tại sao không làm chatbot đơn giản?

Chatbot chỉ tạo ngôn ngữ. Bài toán bán hàng cần dữ liệu thay đổi liên tục và hành động có hậu quả, nên phải dùng tool, state và guardrail trong code.

### Nếu LLM bịa giá thì sao?

System prompt cấm suy đoán, nhưng lớp bảo vệ chính là giá và tồn kho chỉ đến từ tool. Trace lưu lại nguồn. Write action không do model trực tiếp thực hiện.

### Multi-tenant được bảo vệ thế nào?

API resolve shop trước khi tạo `ShopTools`. Mọi truy vấn product đều nhận `shop_id`. Test tenant isolation chứng minh truy vấn ở shop mỹ phẩm không trả sản phẩm điện tử.

### Tại sao cần deterministic fallback?

LLM API có thể lỗi hoặc hết quota. Fallback giữ các luồng cốt lõi hoạt động và tạo baseline để so với model online.

### Hệ thống chống bỏ sót khách như thế nào?

Khi AI handoff hoặc nhân viên tắt bot, hội thoại chuyển sang `waiting`. Repository kết hợp trạng thái này với tín hiệu mua hàng và thời gian chờ để xếp ưu tiên. Ca quá năm phút vi phạm SLA được đưa lên đầu; nhân viên có thể nhận xử lý rồi đánh dấu hoàn tất. Quy tắc này nằm trong code nên giải thích và kiểm thử được.

### Copilot khác chatbot tự trả lời như thế nào?

Chatbot gửi câu trả lời thẳng cho khách. Copilot chỉ tạo tóm tắt, cảnh báo rủi ro và bản nháp trong màn hình nhân viên. Nhân viên kiểm tra, sửa nếu cần rồi chủ động gửi. Vì vậy shop tăng tốc độ phản hồi mà vẫn giữ người chịu trách nhiệm cho các ca chốt đơn hoặc khiếu nại.

### 100% evaluation có nghĩa hệ thống hoàn hảo không?

Không. Kết quả 16/16 chỉ đúng trên tập scenario đã định nghĩa. Hệ thống có online eval, human feedback và production metrics để tiếp tục đo trên dữ liệu vận hành; không suy rộng baseline thành độ chính xác tuyệt đối.

### Hạn chế hiện tại là gì?

Các connector shipping và commerce dùng webhook ký HMAC nên có thể nối GHN, Haravan, Sapo hoặc middleware riêng, nhưng deployment chỉ gọi provider nào đã được chủ hệ thống cấp URL/credential. Meta Advanced Access vẫn phụ thuộc quy trình xét duyệt bên ngoài của Meta.

## Dòng CV đề xuất

> Built ShopPilot AI, a multi-tenant commerce agent using FastAPI, Groq and tool calling; implemented a revenue-prioritized omnichannel inbox, human reply copilot, catalog-grounded recommendations, auditable traces, and confirmation-gated order workflows, validated with automated tests and scenario-based evaluation.
