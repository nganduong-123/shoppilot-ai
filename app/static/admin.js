const $ = selector => document.querySelector(selector);
const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g,
  char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
const pages = {shops:0,users:0}, pageSize = 50;

async function api(url, options = {}) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(url, {...options, signal:controller.signal,
      headers:{"Content-Type":"application/json"}});
    const body = await response.json();
    if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Dữ liệu không hợp lệ.");
    return body;
  } finally { clearTimeout(timer); }
}

function showError(error) { $("#admin-error").textContent = error.message; $("#admin-error").hidden = false; }
function emptyRow(columns) { return `<tr><td colspan="${columns}">Chưa có dữ liệu.</td></tr>`; }

async function mutate(button, url, body) {
  const reason = prompt("Ghi rõ lý do thao tác (ít nhất 5 ký tự):");
  if (reason === null) return;
  if (reason.trim().length < 5) { showError(new Error("Cần ghi lý do rõ ràng.")); return; }
  button.disabled = true;
  try {
    await api(url, {method:body.status ? "PATCH" : "POST",body:JSON.stringify({...body,reason})});
    await refresh();
  } catch (error) { showError(error); }
  finally { button.disabled = false; }
}

async function refresh() {
  $("#refresh-admin").disabled = true;
  $("#admin-error").hidden = true;
  try {
    const [summary, readiness, shops, users, jobs, audit] = await Promise.all([
      api("/api/admin/overview"),api("/api/admin/readiness"),api(`/api/admin/shops?limit=${pageSize}&offset=${pages.shops*pageSize}`),
      api(`/api/admin/users?limit=${pageSize}&offset=${pages.users*pageSize}`),
      api("/api/admin/jobs"),api("/api/admin/audit"),
    ]);
    const labels = {users:"Tài khoản",suspended:"Đang khóa",shops:"Shop",failed_jobs:"Job lỗi",ai_turns:"Lượt AI tháng này"};
    $("#overview").innerHTML = Object.entries(labels).map(([key,label]) =>
      `<article><span>${label}</span><strong>${Number(summary[key])}</strong></article>`).join("");
    $("#readiness").innerHTML = readiness.checks.map(check => `<article class="${check.ready ? "ready" : "pending"}"><span>${check.ready ? "✓" : "!"}</span><div><strong>${escapeHtml(check.label)}</strong><small>${escapeHtml(check.detail)}</small></div></article>`).join("");
    $("#admin-shops").innerHTML = shops.map(shop => `<tr><td>${escapeHtml(shop.name)}<small>${escapeHtml(shop.slug)}</small></td>
      <td>${shop.members}</td><td>${escapeHtml(shop.plan)} · ${escapeHtml(shop.billing_status)}</td>
      <td>${shop.trial_plan && new Date(shop.trial_expires_at) > new Date() ? `${escapeHtml(shop.trial_plan)}<small>${new Date(shop.trial_expires_at).toLocaleString("vi-VN")}</small>` : "Không có"}</td>
      <td><div class="trial-controls"><select aria-label="Gói dùng thử"><option value="pro">Pro</option><option value="business">Business</option></select>
      <input type="number" min="1" max="30" value="7" aria-label="Số ngày"><button data-trial="${shop.id}">Cấp dùng thử</button></div></td></tr>`).join("") || emptyRow(5);
    $("#admin-users").innerHTML = users.map(user => `<tr><td>${escapeHtml(user.display_name)}<small>${escapeHtml(user.email)}</small></td>
      <td>${escapeHtml(user.status)}</td><td>${user.platform_admin ? "Platform Admin" : "Người dùng"}</td>
      <td>${user.platform_admin ? "Được bảo vệ" : `<button data-user="${escapeHtml(user.id)}" data-status="${user.status === "active" ? "suspended" : "active"}">${user.status === "active" ? "Khóa" : "Mở khóa"}</button>`}</td></tr>`).join("") || emptyRow(4);
    $("#admin-jobs").innerHTML = jobs.map(job => `<tr><td>${escapeHtml(job.id)}</td><td>${escapeHtml(job.kind)}</td><td>${job.attempts}/${job.max_attempts}</td><td><button data-job="${escapeHtml(job.id)}">Thử lại</button></td></tr>`).join("") || emptyRow(4);
    $("#admin-audit").innerHTML = audit.map(item => `<tr><td>${new Date(item.created_at).toLocaleString("vi-VN")}</td><td>${escapeHtml(item.action)}</td><td>${escapeHtml(item.target_id)}</td><td>${escapeHtml(item.reason)}</td></tr>`).join("") || emptyRow(4);
    for (const kind of ["shops","users"]) {
      $(`#${kind}-page`).textContent = `Trang ${pages[kind]+1}`;
      $(`#${kind}-prev`).disabled = pages[kind] === 0;
      $(`#${kind}-next`).disabled = (kind === "shops" ? shops : users).length < pageSize;
    }
    document.querySelectorAll('[data-user]').forEach(button => button.onclick = () =>
      mutate(button,`/api/admin/users/${encodeURIComponent(button.dataset.user)}/status`,{status:button.dataset.status}));
    document.querySelectorAll('[data-job]').forEach(button => button.onclick = () =>
      mutate(button,`/api/admin/jobs/${encodeURIComponent(button.dataset.job)}/retry`,{}));
    document.querySelectorAll('[data-trial]').forEach(button => button.onclick = () =>
      mutate(button,`/api/admin/shops/${button.dataset.trial}/trial`,{
        plan:button.parentElement.querySelector('select').value,
        days:Number(button.parentElement.querySelector('input').value),
      }));
  } catch (error) { showError(error); }
  finally { $("#refresh-admin").disabled = false; }
}

async function init() {
  const session = await api("/api/auth/me");
  if (!session.authenticated) { location.href = "/login"; return; }
  if (!session.platform_admin) { showError(new Error("Tài khoản này không có quyền quản trị nền tảng.")); return; }
  $("#admin-account").textContent = session.user.display_name;
  $("#refresh-admin").onclick = refresh;
  for (const kind of ["shops","users"]) for (const [suffix,delta] of [["prev",-1],["next",1]]) {
    $(`#${kind}-${suffix}`).onclick = () => {pages[kind] += delta;refresh();};
  }
  await refresh();
}
init().catch(showError);
