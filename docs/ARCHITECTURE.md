# Kiến trúc ShopPilot AI

## Luồng tổng thể

```mermaid
flowchart LR
    U[Khách hàng] --> C[Website / Messenger]
    C --> AD[Channel adapters]
    AD --> IB[Unified inbox]
    IB --> CP[Human reply copilot]
    IB --> API[FastAPI]
    API --> TR[Tenant resolver]
    TR --> AG[Sales agent loop]
    AG --> LLM[Groq LLM]
    AG --> GD[Confirmation guardrail]
    AG --> TL[Tool registry]
    TL --> P[(Product catalog)]
    TL --> I[(Inventory)]
    TL --> O[(Draft orders)]
    TL --> K[Shop policies]
    TL --> H[Human handoff]
    TL --> T[(Audit trace)]
    GD --> O
```

## Ranh giới trách nhiệm

| Thành phần | Trách nhiệm |
|---|---|
| `agent.py` | Quản lý vòng lặp model → tool → observation → trả lời |
| `tools.py` | Thực thi nghiệp vụ có kiểm soát và ghi trace |
| `repository.py` | Đọc/ghi dữ liệu, luôn giới hạn theo `shop_id` |
| `database.py` | Schema SQLite, transaction và foreign key |
| `main.py` | HTTP API, validation và phục vụ giao diện |
| `channels/` | Chuẩn hóa webhook từng nền tảng và gửi phản hồi |
| `inbox.py` | Chống sự kiện trùng, lưu hội thoại, điều phối AI/người thật |
| `copilot.py` | Tóm tắt hội thoại và soạn nháp có căn cứ để nhân viên duyệt |
| `static/` | Console chat, catalog, metrics và live trace |

## Luồng omnichannel

1. Adapter xác thực webhook của nền tảng và chuyển dữ liệu về `InboundMessage` chung.
2. `channel_events` dùng ID từ nền tảng để không trả lời hai lần khi webhook được gửi lại.
3. `channel_conversations` ánh xạ hội thoại bên ngoài vào hội thoại nội bộ của agent.
4. Khi `bot_enabled=true`, agent xử lý rồi adapter gửi kết quả về đúng kênh.
5. Khi nhân viên tắt AI, tin mới vẫn được lưu nhưng không tự trả lời; toàn bộ ngữ cảnh được giữ để nhân viên tiếp quản.
6. Repository tính trạng thái cần chú ý từ handoff, hướng tin nhắn cuối, tín hiệu mua hàng và thời gian chờ. Ca chờ quá năm phút được đánh dấu vi phạm SLA.
7. Nhân viên có thể nhận xử lý, trả lời, hoàn tất hoặc mở lại hội thoại. Tin nhắn mới tự mở lại hội thoại đã hoàn tất.
8. Website trả lời trực tiếp qua HTTP. Messenger được tiếp nhận nhanh và xử lý trong background task.

Priority là quy tắc minh bạch trong Python, không phải điểm số bí mật từ LLM. Vì vậy đội vận hành có thể giải thích tại sao một khách được đưa lên đầu hàng chờ và thay đổi ngưỡng SLA theo nhu cầu.

## Human reply copilot

Copilot đọc tối đa 16 tin nhắn gần nhất cùng catalog và chính sách của đúng shop. Groq trả về structured output gồm `summary`, `suggested_reply` và `risk_flags`; nếu API lỗi, một fallback xác định vẫn tạo bản nháp an toàn. Kết quả chỉ xuất hiện trong console nội bộ và không đi qua channel adapter.

Nhân viên phải bấm **Chèn vào ô trả lời**, có thể sửa nội dung rồi mới bấm **Gửi**. Mỗi bản nháp được lưu với trạng thái `generated` hoặc `used` để sau này đo tỷ lệ chấp nhận. Cơ chế này giữ con người ở điểm quyết định cuối cùng và tránh để AI tự gửi lời hứa về giá, phí giao hoặc hoàn tiền.

Background task trong tiến trình phù hợp cho bản demo. Bản production cần hàng đợi bền vững như Redis/Celery để không mất sự kiện khi máy chủ khởi động lại.

## Xóa dữ liệu Meta

1. Meta gửi `signed_request` tới `/api/meta/data-deletion` khi người dùng yêu cầu xóa.
2. Adapter giải mã Base64URL và kiểm tra HMAC-SHA256 bằng App Secret trước khi tin payload.
3. Repository xóa hội thoại kênh, tin nhắn, trace, đơn nháp, handoff và event có đúng Meta user ID.
4. Dữ liệu của khách khác không bị ảnh hưởng.
5. Hệ thống chỉ giữ biên nhận ẩn danh gồm hash người dùng, mã xác nhận, thời gian và số bản ghi đã xóa.
6. Người dùng theo dõi kết quả bằng `/data-deletion?code=...`; mã người dùng Meta không xuất hiện trong URL.

## Multi-tenant

Một bản triển khai phục vụ nhiều shop. Mỗi `conversation`, `product` và `order` đều gắn với một `shop_id`. API nhận `slug`, giải ra shop trước rồi mới tạo tool context. Tool chỉ truy vấn catalog của shop trong context, nên Mint Fashion không thể tìm thấy sản phẩm Nova Tech.

## Agent loop

1. Hybrid router bắt các ý định cần tính xác định: xác nhận/hủy đơn, vận chuyển, ý định mua rõ ràng, chuyển người và prompt injection.
2. Các yêu cầu mở nhận lịch sử hội thoại và system prompt của shop.
3. Model quyết định trả lời hay yêu cầu gọi tool.
4. Tool registry kiểm tra tên tool, tham số và tenant.
5. Tool thực thi, lưu arguments, result, latency và success.
6. Observation được trả lại cho model.
7. Lặp tối đa năm vòng rồi trả lời người dùng.
8. Nếu Groq lỗi, deterministic fallback vẫn xử lý các luồng cốt lõi.

## Write-action guardrail

`prepare_draft_order` chỉ tạo trạng thái `awaiting_confirmation`. Tồn kho chưa bị thay đổi. Chỉ khi tin nhắn tiếp theo chứa xác nhận rõ ràng, workflow xác định mới gọi `confirm_pending_order`, kiểm tra tồn kho lần cuối rồi mới trừ hàng.

Model không có tool `confirm_order`, do đó model không thể tự hoàn tất đơn hàng.

## Quan sát và đánh giá

Mỗi tool call lưu:

- Conversation ID.
- Tool name.
- Arguments.
- Result.
- Thời gian xử lý.
- Trạng thái thành công/thất bại.

`scripts/evaluate.py` chạy bộ scenario độc lập với unit test. Unit test xác nhận code hoạt động; evaluation xác nhận agent chọn đúng tool, đúng tenant, đúng sản phẩm và đúng trạng thái nghiệp vụ.
