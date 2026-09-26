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
2. Thêm sản phẩm Messenger và kết nối Facebook Page cần thử nghiệm.
3. Tạo Page access token cho Page đó.
4. Trong phần Webhooks, thêm callback URL ở trên và đăng ký trường `messages`.
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

`META_VERIFY_TOKEN` nên là chuỗi ngẫu nhiên dài. Không gửi `META_APP_SECRET` hoặc `META_PAGE_ACCESS_TOKEN` qua chat, ảnh chụp màn hình hay issue GitHub.

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

- Cấu hình hiện tại dành cho một Page gắn với `META_SHOP_SLUG`.
- Messenger chỉ cho phép gửi phản hồi theo chính sách và cửa sổ nhắn tin của Meta.
- Khi bán cho nhiều shop, cần OAuth onboarding, lưu token đã mã hóa, phân quyền nhân viên, xử lý token hết hạn và hàng đợi webhook bền vững.
- Quyền truy cập và quy trình xét duyệt có thể thay đổi; đối chiếu lại tài liệu Meta trước khi đưa lên production.
