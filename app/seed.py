from __future__ import annotations

from app.database import db_session, json_dumps, utc_now


DEMO_SHOPS = [
    {
        "slug": "mint-fashion",
        "name": "MisterBox Men",
        "category": "Thời trang",
        "tagline": "Thời trang nam gọn gàng cho nhịp sống hiện đại",
        "primary_color": "#24d5b5",
        "accent_color": "#7557ff",
        "voice": "Thân thiện, tinh tế, xưng em và gọi khách là anh/chị. Trả lời ngắn gọn.",
        "policy_text": (
            "Đổi size miễn phí trong 7 ngày nếu sản phẩm còn nguyên tem. "
            "Không đổi sản phẩm đã qua sử dụng. Miễn phí giao hàng cho đơn từ 500.000đ tại TP.HCM. "
            "Đơn dưới 500.000đ có phí giao hàng 30.000đ nội thành và 45.000đ ngoại tỉnh. "
            "Thời gian giao nội thành 1-2 ngày, ngoại tỉnh 3-5 ngày. "
            "Mọi đơn hàng phải được khách xác nhận trước khi đặt."
        ),
        "products": [
            {
                "sku": "MF-SM01",
                "name": "Sơ mi Linen Trắng",
                "category": "Áo sơ mi",
                "description": "Sơ mi linen dáng relaxed, thoáng nhẹ, phù hợp đi làm và đi chơi.",
                "price": 429000,
                "stock": 28,
                "attributes": {"màu": ["trắng", "be"], "size": ["S", "M", "L", "XL"], "chất liệu": "linen", "phom": "relaxed"},
            },
            {
                "sku": "MF-SM02",
                "name": "Sơ mi Oxford Xanh",
                "category": "Áo sơ mi",
                "description": "Sơ mi Oxford đứng phom, lịch sự, ít nhăn.",
                "price": 489000,
                "stock": 17,
                "attributes": {"màu": ["xanh nhạt", "xanh navy"], "size": ["M", "L", "XL"], "chất liệu": "cotton oxford", "phom": "regular"},
            },
            {
                "sku": "MF-TS03",
                "name": "Áo thun Essential Đen",
                "category": "Áo thun",
                "description": "Áo thun cotton compact 250gsm, cổ bền và không bai.",
                "price": 269000,
                "stock": 42,
                "attributes": {"màu": ["đen", "trắng", "xám"], "size": ["S", "M", "L", "XL"], "chất liệu": "cotton compact", "phom": "oversize"},
            },
            {
                "sku": "MF-QA04",
                "name": "Quần Âu SmartFit",
                "category": "Quần",
                "description": "Quần âu co giãn nhẹ, cạp ẩn, phù hợp môi trường công sở.",
                "price": 559000,
                "stock": 12,
                "attributes": {"màu": ["đen", "xám than"], "size": ["28", "29", "30", "31", "32"], "chất liệu": "poly-viscose", "phom": "slim"},
            },
            {
                "sku": "MF-JK05",
                "name": "Áo khoác Urban Shell",
                "category": "Áo khoác",
                "description": "Áo khoác nhẹ chống gió và mưa nhỏ, có túi khóa kéo.",
                "price": 749000,
                "stock": 0,
                "attributes": {"màu": ["đen", "rêu"], "size": ["M", "L"], "chất liệu": "polyester", "phom": "regular"},
            },
        ],
    },
    {
        "slug": "lumi-beauty",
        "name": "Lumi Beauty",
        "category": "Mỹ phẩm",
        "tagline": "Chăm da tối giản, minh bạch thành phần",
        "primary_color": "#ff8fad",
        "accent_color": "#8f67ff",
        "voice": "Nhẹ nhàng, rõ ràng. Không đưa ra chẩn đoán y khoa và luôn nhắc thử trên vùng da nhỏ.",
        "policy_text": (
            "Sản phẩm chưa mở nắp được đổi trong 7 ngày nếu giao sai hoặc có lỗi. "
            "Không nhận đổi sản phẩm đã mở nắp vì lý do vệ sinh. Miễn phí giao hàng từ 600.000đ. "
            "Tư vấn chỉ mang tính tham khảo, khách có bệnh lý da nên hỏi bác sĩ da liễu."
        ),
        "products": [
            {
                "sku": "LB-SR01",
                "name": "Serum Niacinamide 5%",
                "category": "Serum",
                "description": "Serum hỗ trợ kiểm soát dầu và củng cố hàng rào bảo vệ da.",
                "price": 329000,
                "stock": 31,
                "attributes": {"loại da": ["da dầu", "da hỗn hợp"], "thành phần": ["niacinamide 5%", "panthenol"], "dung tích": "30ml"},
            },
            {
                "sku": "LB-CL02",
                "name": "Gel rửa mặt Dịu Nhẹ pH 5.5",
                "category": "Làm sạch",
                "description": "Gel làm sạch không sulfate, hạn chế khô căng.",
                "price": 219000,
                "stock": 44,
                "attributes": {"loại da": ["mọi loại da", "da nhạy cảm"], "thành phần": ["betaine", "allantoin"], "dung tích": "150ml"},
            },
            {
                "sku": "LB-SP03",
                "name": "Kem chống nắng Daily Shield SPF50+",
                "category": "Chống nắng",
                "description": "Kem chống nắng phổ rộng, ráo nhanh, không nâng tông.",
                "price": 389000,
                "stock": 22,
                "attributes": {"loại da": ["da dầu", "da thường"], "chỉ số": "SPF50+ PA++++", "dung tích": "50ml"},
            },
        ],
    },
    {
        "slug": "nova-tech",
        "name": "Nova Tech",
        "category": "Điện tử",
        "tagline": "Thiết bị thông minh, thông số rõ ràng",
        "primary_color": "#48b6ff",
        "accent_color": "#7357ff",
        "voice": "Chính xác, chuyên nghiệp, không suy đoán thông số kỹ thuật.",
        "policy_text": (
            "Sản phẩm được đổi mới trong 7 ngày nếu có lỗi phần cứng được xác nhận. "
            "Bảo hành theo thời hạn ghi trên từng sản phẩm. Không bảo hành rơi vỡ hoặc vào nước. "
            "Miễn phí giao hàng toàn quốc cho đơn từ 1.000.000đ."
        ),
        "products": [
            {
                "sku": "NT-HP01",
                "name": "Tai nghe AirBeat Pro",
                "category": "Tai nghe",
                "description": "Tai nghe chống ồn chủ động, kết nối hai thiết bị.",
                "price": 1290000,
                "stock": 19,
                "attributes": {"pin": "30 giờ", "kết nối": "Bluetooth 5.3", "bảo hành": "12 tháng", "màu": ["đen", "trắng"]},
            },
            {
                "sku": "NT-KB02",
                "name": "Bàn phím cơ Nova 75",
                "category": "Bàn phím",
                "description": "Bàn phím cơ layout 75%, hot-swap, kết nối ba chế độ.",
                "price": 1590000,
                "stock": 14,
                "attributes": {"switch": ["linear", "tactile"], "kết nối": ["USB-C", "Bluetooth", "2.4GHz"], "bảo hành": "18 tháng"},
            },
            {
                "sku": "NT-CH03",
                "name": "Sạc nhanh GaN 65W",
                "category": "Phụ kiện",
                "description": "Củ sạc GaN ba cổng, hỗ trợ PD 3.0 và PPS.",
                "price": 690000,
                "stock": 36,
                "attributes": {"cổng": ["2x USB-C", "1x USB-A"], "công suất": "65W", "bảo hành": "12 tháng"},
            },
        ],
    },
]


def seed_demo_data() -> None:
    with db_session() as connection:
        connection.execute(
            """
            UPDATE shops
            SET name = ?, tagline = ?
            WHERE slug = ? AND name = ?
            """,
            (
                "MisterBox Men",
                "Thời trang nam gọn gàng cho nhịp sống hiện đại",
                "mint-fashion",
                "Mint Fashion",
            ),
        )
        existing = connection.execute("SELECT COUNT(*) AS count FROM shops").fetchone()["count"]
        if existing:
            return

        for shop in DEMO_SHOPS:
            cursor = connection.execute(
                """
                INSERT INTO shops
                    (slug, name, category, tagline, primary_color, accent_color,
                     policy_text, voice, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    shop["slug"],
                    shop["name"],
                    shop["category"],
                    shop["tagline"],
                    shop["primary_color"],
                    shop["accent_color"],
                    shop["policy_text"],
                    shop["voice"],
                    utc_now(),
                ),
            )
            shop_id = cursor.lastrowid
            for product in shop["products"]:
                connection.execute(
                    """
                    INSERT INTO products
                        (shop_id, sku, name, category, description, price, stock,
                         attributes_json, active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        shop_id,
                        product["sku"],
                        product["name"],
                        product["category"],
                        product["description"],
                        product["price"],
                        product["stock"],
                        json_dumps(product["attributes"]),
                    ),
                )
