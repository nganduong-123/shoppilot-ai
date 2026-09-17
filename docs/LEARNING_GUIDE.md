# Học ShopPilot AI từ chính dự án

Tài liệu này giúp Dương Thị Ngân giải thích được hệ thống bằng kiến thức của mình, thay vì chỉ chạy một repository có sẵn.

## 1. Bài toán đang giải quyết

Nhân viên shop mất nhiều thời gian trả lời các câu lặp lại như giá, tồn kho, phí giao hàng và đổi trả. Chatbot FAQ chỉ sinh câu trả lời; ShopPilot còn đọc dữ liệu hiện tại, gọi công cụ và quản lý một quy trình nhiều bước.

Mục tiêu hợp lý là tự động hóa tuyến đầu và chuyển trường hợp khó cho nhân viên. Hệ thống không tuyên bố thay toàn bộ con người.

## 2. Vì sao đây là agent

Một chatbot thường thực hiện `câu hỏi → câu trả lời`. ShopPilot thực hiện:

```text
Yêu cầu → quyết định tool → lấy dữ liệu → quan sát kết quả
        → suy luận tiếp → hành động hoặc trả lời
```

Ví dụ “Tìm áo sơ mi trắng size M dưới 500k” cần gọi `search_products`. Model không được tự nhớ hay bịa catalog.

## 3. Tool calling

Tool là hàm Python có JSON Schema mô tả input. Model chỉ đề nghị gọi tool; code Python mới thực thi.

```text
Model: search_products(query="áo sơ mi trắng", size="M", max_price=500000)
Code: kiểm tra schema và shop_id, sau đó truy vấn database
Tool: trả về sản phẩm, giá và tồn kho
Model: diễn đạt kết quả cho khách
```

Điểm quan trọng: model quyết định, tool cung cấp bằng chứng.

## 4. State và hội thoại nhiều lượt

`conversations.context_json` lưu sản phẩm vừa xem và preferences như ngân sách, màu, size. `pending_action_json` lưu thao tác đang chờ xác nhận. Vì vậy “mẫu số 1” và “xác nhận” có nghĩa dựa trên lượt trước.

## 5. Multi-tenant

Multi-tenant nghĩa là một ứng dụng phục vụ nhiều doanh nghiệp. Logic agent dùng chung, nhưng catalog, policy, đơn hàng và hội thoại tách theo `shop_id`.

Lợi ích:

- Triển khai và cập nhật một hệ thống.
- Mỗi shop cấu hình giọng nói và chính sách riêng.
- Dữ liệu giữa các shop không bị trộn.

## 6. Human-in-the-loop

AI xử lý việc lặp lại; người thật xử lý khiếu nại, ngoại lệ, thương lượng và trường hợp thiếu căn cứ. `handoff_to_human` gửi lý do cùng lịch sử gần nhất, để khách không phải trình bày lại.

## 7. Confirmation guardrail

Đặt hàng là write action vì nó thay đổi dữ liệu. Quy trình có hai bước:

1. Tạo draft và cho khách xem tổng tiền.
2. Khách nhắn “Xác nhận”; code kiểm tra lại tồn kho rồi mới hoàn tất.

Quy tắc này nằm trong code chứ không chỉ nằm trong prompt. Vì vậy model có nói sai thì vẫn không tự hoàn tất đơn.

ShopPilot dùng **hybrid routing**: mua hàng, vận chuyển, chuyển người, xác nhận và prompt injection được xử lý bằng workflow xác định. LLM tập trung vào hiểu ngôn ngữ và tư vấn mở. Cách này giảm biến động của model ở các bước nghiệp vụ quan trọng.

## 8. Grounding, trace và evaluation

Grounding buộc câu trả lời dựa trên catalog, inventory và policy. Trace cho biết agent đã dùng bằng chứng nào. Evaluation dùng 16 tình huống để kiểm tra tool routing, sản phẩm, status và tenant isolation.

Ba khái niệm khác nhau:

- **Unit test:** hàm có chạy đúng không?
- **Evaluation:** agent có hành xử đúng trên tập tình huống không?
- **Monitoring:** khi chạy thật, hệ thống đang nhanh, rẻ và chính xác đến đâu?

## 9. Thứ tự đọc code

1. `app/main.py`: nhìn các API mà sản phẩm cung cấp.
2. `app/agent.py`: theo vòng lặp và các guardrail.
3. `app/tools.py`: hiểu từng hành động nghiệp vụ.
4. `app/repository.py`: xem dữ liệu được cô lập như thế nào.
5. `app/database.py`: đọc schema và quan hệ bảng.
6. `tests/`: xem những điều hệ thống cam kết.
7. `evals/scenarios.json`: xem định nghĩa một agent “tốt”.

## 10. Bài tập để biến thành kiến thức của mình

1. Tự thêm tool `get_order_status` và ba test.
2. Thêm thuộc tính chiều cao/cân nặng nhưng không để agent khẳng định size chắc chắn.
3. Thêm một shop mới bằng API và import CSV.
4. Tạo một scenario prompt injection mới.
5. Giải thích vì sao không cấp tool xác nhận đơn cho LLM.
