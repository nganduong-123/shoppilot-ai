function canManageCatalog() {
  return !state.session?.authenticated || ["owner", "manager"].includes(state.shop?.role);
}

function renderCatalog() {
  const editable = canManageCatalog();
  $("#catalog-actions").hidden = !editable;
  $("#catalog-count").textContent = state.products.length;
  $("#catalog-grid").innerHTML = state.products.map(product => {
    const attrs = Object.entries(product.attributes).slice(0, 2)
      .map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(", ") : value}`).join(" · ");
    return `<article class="product-card">
      <div class="product-top"><span class="product-glyph">${initials(product.name)}</span>
      <span class="stock-pill ${product.stock ? "" : "out"}">${product.stock ? `Còn ${product.stock}` : "Hết hàng"}</span></div>
      <h3>${escapeHtml(product.name)}</h3><span class="sku">${escapeHtml(product.sku)} · ${escapeHtml(product.category)}</span>
      <p>${escapeHtml(product.description)}</p><div class="product-bottom"><span class="product-price">${formatMoney(product.price)}</span>
      <span class="attribute-list">${escapeHtml(attrs)}</span></div>
      ${editable ? `<div class="catalog-actions"><button type="button" data-edit-product="${product.id}">Sửa</button>
      <button type="button" data-archive-product="${product.id}">Ẩn khỏi bán hàng</button></div>` : ""}
    </article>`;
  }).join("") || '<p>Chưa có sản phẩm. Thêm sản phẩm hoặc nhập CSV để bắt đầu.</p>';
  $$('[data-edit-product]').forEach(button => button.addEventListener("click", () =>
    openProductEditor(Number(button.dataset.editProduct))));
  $$('[data-archive-product]').forEach(button => button.addEventListener("click", () =>
    archiveCatalogProduct(Number(button.dataset.archiveProduct), button)));
}

function bindCatalogEditor() {
  $("#add-product").addEventListener("click", () => openProductEditor());
  $("#cancel-product").addEventListener("click", () => $("#product-editor").close());
  $("#product-form").addEventListener("submit", saveCatalogProduct);
  $("#edit-shop").addEventListener("click", () => {
    const form = $("#shop-form");
    for (const key of ["name", "category", "tagline", "policy_text", "voice"]) {
      form.elements[key].value = state.shop[key] || "";
    }
    $("#shop-form-error").hidden = true;
    $("#shop-editor").showModal();
  });
  $("#cancel-shop").addEventListener("click", () => $("#shop-editor").close());
  $("#shop-form").addEventListener("submit", saveShopProfile);
}

async function saveShopProfile(event) {
  event.preventDefault();
  const form = event.currentTarget, button = $("#save-shop"), error = $("#shop-form-error");
  button.disabled = true;
  error.hidden = true;
  try {
    const body = {};
    for (const key of ["name", "category", "tagline", "policy_text", "voice"]) {
      body[key] = form.elements[key].value.trim();
    }
    const shop = await api(`/api/shops/${state.shop.slug}`, { method: "PATCH", body: JSON.stringify(body) });
    Object.assign(state.shop, shop);
    $("#shop-name").textContent = shop.name;
    $("#shop-category").textContent = shop.category;
    $("#shop-avatar").textContent = initials(shop.name);
    renderShopMenu();
    $("#shop-editor").close();
    toast("Đã cập nhật cấu hình và chính sách shop.");
  } catch (failure) { error.textContent = failure.message; error.hidden = false; }
  finally { button.disabled = false; }
}

function openProductEditor(id) {
  const form = $("#product-form"), product = state.products.find(item => item.id === id);
  form.reset();
  form.elements.product_id.value = product?.id || "";
  for (const key of ["sku", "name", "category", "price", "stock", "description"]) {
    form.elements[key].value = product?.[key] ?? (key === "price" || key === "stock" ? 0 : "");
  }
  form.elements.attributes.value = JSON.stringify(product?.attributes || {}, null, 2);
  $("#product-editor-title").textContent = product ? "Sửa sản phẩm" : "Thêm sản phẩm";
  $("#product-form-error").hidden = true;
  $("#product-editor").showModal();
}

async function saveCatalogProduct(event) {
  event.preventDefault();
  const form = event.currentTarget, error = $("#product-form-error"), button = $("#save-product");
  button.disabled = true;
  error.hidden = true;
  try {
    let attributes;
    try { attributes = JSON.parse(form.elements.attributes.value || "{}"); }
    catch { throw new Error('Thuộc tính phải là JSON hợp lệ, ví dụ {"size": ["S", "M"]}.'); }
    if (!attributes || Array.isArray(attributes) || typeof attributes !== "object") {
      throw new Error("Thuộc tính phải là một đối tượng JSON.");
    }
    const body = { attributes };
    for (const key of ["sku", "name", "category", "description"]) body[key] = form.elements[key].value.trim();
    for (const key of ["price", "stock"]) body[key] = Number(form.elements[key].value);
    const id = form.elements.product_id.value;
    await api(`/api/shops/${state.shop.slug}/products${id ? `/${id}` : ""}`, {
      method: id ? "PUT" : "POST", body: JSON.stringify(body),
    });
    $("#product-editor").close();
    await Promise.all([loadProducts(), loadMetrics()]);
    toast(id ? "Đã cập nhật sản phẩm." : "Đã thêm sản phẩm.");
  } catch (failure) {
    error.textContent = failure.message;
    error.hidden = false;
  } finally { button.disabled = false; }
}

async function archiveCatalogProduct(id, button) {
  if (!confirm("Ẩn sản phẩm khỏi bán hàng? Lịch sử đơn hàng vẫn được giữ nguyên.")) return;
  button.disabled = true;
  try {
    await api(`/api/shops/${state.shop.slug}/products/${id}`, { method: "DELETE" });
    await Promise.all([loadProducts(), loadMetrics()]);
    toast("Đã ẩn sản phẩm khỏi bán hàng.");
  } catch (error) { toast(error.message); button.disabled = false; }
}
