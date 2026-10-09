# Kết nối Facebook Page với ShopPilot AI

ShopPilot nhận tin nhắn Page qua webhook Messenger Platform và gửi câu trả lời bằng Send API. Không ghi access token vào source code hoặc commit lên Git.

## 1. Chuẩn bị URL HTTPS công khai

Deploy ứng dụng hoặc dùng tunnel HTTPS trong lúc phát triển. Callback URL cần có dạng:

```text
https://YOUR-DOMAIN/api/webhooks/meta
```

Endpoint `GET` dùng để Meta xác minh webhook. Endpoint `POST` kiểm tra chữ ký `X-Hub-Signature-256` trước khi xử lý dữ liệu.

## 2. Tạo và cấu hình Meta app

1. Tạo app phù hợp cho doanh nghiệp tại Meta for Developers.
2. Thêm Messenger và Facebook Login for Business.
3. Trong phần Webhooks, thêm callback URL ở trên và đăng ký trường `messages`.
4. Thêm `{PUBLIC_BASE_URL}/api/integrations/meta/callback` vào Valid OAuth Redirect URIs.
5. Verify token do bạn tự đặt phải giống `META_VERIFY_TOKEN` trong `.env`.
6. Với khách hàng ngoài vai trò test của app, hoàn thành các yêu cầu App Review/Advanced Access của Meta cho quyền liên quan, gồm `pages_messaging` và quyền quản lý webhook Page.

## 3. Điền cấu hình cục bộ

Sao chép các biến sau vào `.env` rồi điền giá trị trong Meta Dashboard:

```dotenv
PUBLIC_BASE_URL=https://YOUR-DOMAIN
META_SHOP_SLUG=mint-fashion
META_PAGE_ID=
META_APP_ID=
META_APP_SECRET=
META_PAGE_ACCESS_TOKEN=
META_VERIFY_TOKEN=
META_GRAPH_API_VERSION=v26.0
```

`META_VERIFY_TOKEN` nên là chuỗi ngẫu nhiên dài. `TOKEN_ENCRYPTION_KEY` là khóa dùng để mã hóa Page token trong cơ sở dữ liệu. Không gửi các khóa hoặc access token qua chat, ảnh chụp màn hình hay issue GitHub.

Luồng khuyến nghị là cấu hình `META_APP_ID`, `META_APP_SECRET`, `META_VERIFY_TOKEN` và `TOKEN_ENCRYPTION_KEY`, sau đó để chủ shop bấm **Kết nối Facebook Page** trong màn hình Integrations. `META_PAGE_ID`, `META_PAGE_ACCESS_TOKEN` và `META_SHOP_SLUG` chỉ còn là chế độ tương thích cho một Page cấu hình bằng biến môi trường.

## 4. Kiểm tra

1. Khởi động lại ShopPilot sau khi sửa `.env`.
2. Mở `/api/health`; `channels.messenger` phải là `true`.
3. Nhắn Page từ một tài khoản được phép thử app.
4. Mở Unified Inbox và kiểm tra tin đến, phản hồi AI và công tắc tiếp quản.
5. Gửi cùng một webhook hai lần để xác nhận hệ thống không tạo hai phản hồi.

## 5. URL phục vụ App Review

Sau khi deploy, mở `GET /api/integrations/meta/status` để lấy đúng các URL công khai mà
không làm lộ token:

- Privacy Policy: `/privacy`
- Terms of Service: `/terms`
- User Data Deletion Instructions: `/data-deletion`
- Data Deletion Callback: `/api/meta/data-deletion`

Callback xóa dữ liệu xác minh `signed_request` bằng App Secret, xóa hội thoại của Meta user
và trả `confirmation_code` cùng URL theo dõi. Không dùng URL tunnel ngắn hạn khi nộp review.

## Giới hạn của pilot

- OAuth onboarding, token mã hóa và ánh xạ Page theo tenant đã hỗ trợ nhiều shop; mỗi Page chỉ thuộc một workspace tại một thời điểm.
- Messenger chỉ cho phép gửi phản hồi theo chính sách và cửa sổ nhắn tin của Meta.
- Webhook đã có shared rate limit, durable queue, retry/backoff và dead-letter state. Khi Page token hết hạn hoặc bị thu hồi, owner kết nối lại Page qua OAuth; job lỗi được giữ để điều tra thay vì mất sự kiện.
- Quyền truy cập và quy trình xét duyệt có thể thay đổi; đối chiếu lại tài liệu Meta trước khi đưa lên production.
