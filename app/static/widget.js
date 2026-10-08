(() => {
  const script = document.currentScript;
  const shop = script?.dataset.shop || "mint-fashion";
  const base = new URL(script.src).origin;
  const color = script?.dataset.color || "#18b99f";
  const frame = document.createElement("iframe");
  frame.title = "ShopPilot AI tư vấn bán hàng";
  frame.src = `${base}/static/widget.html?shop=${encodeURIComponent(shop)}&color=${encodeURIComponent(color)}`;
  frame.allow = "clipboard-write";
  Object.assign(frame.style, {
    position: "fixed", right: "22px", bottom: "92px", width: "min(390px, calc(100vw - 28px))",
    height: "min(640px, calc(100vh - 120px))", border: "0", borderRadius: "20px",
    boxShadow: "0 24px 80px rgba(0,0,0,.28)", zIndex: "2147483646", display: "none",
  });
  const button = document.createElement("button");
  button.type = "button";
  button.setAttribute("aria-label", "Mở tư vấn trực tuyến");
  button.innerHTML = '<svg viewBox="0 0 24 24" width="25" height="25" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z"/></svg>';
  Object.assign(button.style, {
    position: "fixed", right: "22px", bottom: "22px", width: "58px", height: "58px",
    border: "0", borderRadius: "18px", background: color, color: "#06201b", cursor: "pointer",
    boxShadow: `0 14px 35px ${color}55`, zIndex: "2147483647", display: "grid", placeItems: "center",
  });
  let open = false;
  button.addEventListener("click", () => {
    open = !open;
    frame.style.display = open ? "block" : "none";
    button.setAttribute("aria-expanded", String(open));
  });
  window.addEventListener("message", event => {
    if (event.origin === base && event.data === "shoppilot:close") {
      open = false;
      frame.style.display = "none";
    }
  });
  document.body.append(frame, button);
})();
