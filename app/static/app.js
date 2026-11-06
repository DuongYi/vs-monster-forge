// Xưởng Quái – giao diện. JS thuần, không cần build.
const $ = (sel, el = document) => el.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const IMG_RE = /\.(png|webp|jpe?g)$/i;

const S = {
  status: null, monsters: [], current: null, monster: null, templates: [], presets: {},
  jobs: [], activeIds: new Set(), openGen: new Set(), genForm: {}, uploading: {},
  openLogs: new Set(), specOpen: false, specDraft: null, justApproved: false,
};

async function api(method, path, body, raw = false) {
  const opts = { method, headers: { "X-Xuong": "1" } };
  if (body !== undefined) {
    if (raw) { opts.body = body; opts.headers["Content-Type"] = "application/octet-stream"; }
    else { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  }
  const r = await fetch(path, opts);
  let data = null;
  try { data = await r.json(); } catch { /* không có JSON */ }
  if (!r.ok) throw new Error(data?.error || `Máy chủ trả lỗi ${r.status}`);
  return data;
}

function toast(msg, kind = "bad") {
  let el = $("#toast");
  if (!el) { el = document.createElement("div"); el.id = "toast"; el.setAttribute("role", "alert"); document.body.append(el); }
  el.className = `toast ${kind}`;
  el.textContent = msg;
  const tray = $("#tray");
  el.style.bottom = `${(tray.classList.contains("has-jobs") ? tray.offsetHeight : 0) + 16}px`;
  el.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => { el.hidden = true; }, kind === "bad" ? 9000 : 4000);
}

const QA_LABEL = { pass: "Đạt", warn: "Cảnh báo", fail: "Lỗi", error: "Không xử lý được" };
const badge = (s) => `<span class="badge ${esc(s || "none")}">${esc(QA_LABEL[s] || "Chưa kiểm")}</span>`;
const CHECK_LABEL = {
  frame_count: "Số khung", empty_frame: "Khung trống", halo: "Viền màu nền ở mép", key_residue: "Sót màu nền bên trong",
  stray_alpha: "Điểm mờ rời rạc", pop: "Hình nhảy giữa hai khung", area_flicker: "Kích thước nhấp nháy",
  anchor_jitter_px: "Chân còn rung", loop_seam: "Mối nối vòng lặp", color_drift: "Lệch màu so với ảnh chuẩn",
  brightness_drift: "Lệch độ sáng", palette: "Ngoài bảng màu", duration: "Thời lượng lệch bản cũ",
  scale_spread: "Cỡ quái giữa các animation", atlas_size: "Kích thước atlas", input: "Không đọc được nguồn",
};
const checkLabel = (c) => c.startsWith("event:") ? `Sự kiện ${c.slice(6)} lệch nhịp` : (CHECK_LABEL[c] || c);
const gpuName = (n) => String(n || "").replace(/^cuda:\d+\s*/, "").replace(/\s*:\s*\w+$/, "");
const fmtTime = (iso) => { try { return new Date(iso).toLocaleString("vi-VN"); } catch { return iso; } };
const seconds = (s) => s >= 60 ? `${Math.floor(s / 60)} phút ${Math.round(s % 60)} giây` : `${Math.round(s)} giây`;

// ───────── tải dữ liệu ─────────
async function loadStatus(refresh = false) {
  try { S.status = await api("GET", `/api/status${refresh ? "?refresh=1" : ""}`); }
  catch (e) { S.status = null; }
  renderMachine();
}
async function loadList() {
  S.monsters = await api("GET", "/api/monsters");
  renderList();
}
async function loadMonster() {
  if (!S.current) { S.monster = null; renderMain(); return; }
  try { S.monster = await api("GET", `/api/monsters/${S.current}`); }
  catch (e) { S.monster = null; toast(e.message); }
  renderMain();
}
async function loadTemplates() {
  try { S.templates = await api("GET", "/api/templates"); } catch { S.templates = []; }
}

// ───────── thanh trên ─────────
function renderMachine() {
  const el = $("#machine");
  const d = S.status?.doctor;
  if (!S.status) { el.innerHTML = `<span class="off"><span class="dot"></span>Không liên lạc được với ứng dụng</span>`; return; }
  if (!d || !d.connected) {
    el.innerHTML = `<span class="off"><span class="dot"></span>ComfyUI chưa mở</span>
      <span>Vẫn nhập khung từ Spine được.</span>
      <button class="btn btn-quiet btn-small" data-action="recheck" type="button">Kiểm tra lại</button>`;
    return;
  }
  el.innerHTML = `<span class="on"><span class="dot"></span>ComfyUI đã kết nối</span>
    <span>Card <b>${esc(gpuName(d.gpu))}</b>, ${esc(d.vram_gb)} GB VRAM</span>
    <button class="btn btn-quiet btn-small" data-action="recheck" type="button">Kiểm tra lại</button>`;
}

// ───────── danh sách quái ─────────
function renderList() {
  const nav = $("#monster-list");
  if (!S.monsters.length) { nav.innerHTML = `<p class="muted">Chưa có quái nào.</p>`; return; }
  nav.innerHTML = S.monsters.map((m) => `
    <a class="m-item" href="#/m/${esc(m.id)}" ${m.id === S.current ? 'aria-current="page"' : ""}>
      <span class="m-name">${esc(m.name)}</span>
      <span class="m-progress">${m.approved ? `<span class="mini-seal" title="Đã duyệt" aria-label="Đã duyệt"></span>` : `${m.ready}/${m.anims}`}</span>
      <span class="m-id">${esc(m.id)}</span>
    </a>`).join("");
}

// ───────── trang quái ─────────
function renderMain() {
  const main = $("#main");
  const focus = rememberFocus();
  if (!S.current || !S.monster) {
    main.innerHTML = S.monsters.length
      ? `<div class="empty-state"><h1>Chọn một con quái</h1><p>Chọn quái ở cột bên trái để làm tiếp, hoặc tạo quái mới.</p></div>`
      : `<div class="empty-state"><h1>Bắt đầu con quái đầu tiên</h1>
           <p>Tạo quái từ file Spine JSON (giữ nguyên tên animation và nhịp đánh) hoặc từ bộ animation chuẩn.</p>
           <button class="btn btn-primary" data-action="new" type="button">Tạo quái mới</button></div>`;
    return;
  }
  const m = S.monster;
  const allReady = m.anims.every((a) => a.source);
  main.innerHTML = `
    <div class="monster-head">
      <div><h1>${esc(m.name)}</h1><p class="sub">${esc(m.id)}</p></div>
      <button class="btn btn-quiet btn-danger adv-only" data-action="delete-monster" type="button">Xoá quái</button>
    </div>
    <ol class="steps">${stepRef(m)}${stepAnims(m, allReady)}${stepProcess(m, allReady)}${stepApprove(m)}</ol>`;
  restoreFocus(focus);
  S.justApproved = false;
}

function stepTitle(n, title) {
  return `<div class="step-title"><span class="step-no" aria-hidden="true">${n}</span><h2>${title}</h2></div>`;
}

function stepRef(m) {
  return `<li class="step ${m.ref ? "done" : ""}">
    ${stepTitle(1, "Ảnh chuẩn")}
    <p class="step-lead">Ảnh quái đứng yên, quay mặt sang phải, nền một màu. AI lấy ảnh này làm điểm xuất phát, bước kiểm tra dùng nó để đo lệch màu. Nếu chỉ nhập khung từ Spine thì có thể bỏ qua.</p>
    <div class="ref-row">
      <div class="lightbox">${m.ref ? `<img src="${esc(m.ref)}" alt="Ảnh chuẩn của ${esc(m.name)}">` : `<div class="empty-frame">Chưa có ảnh chuẩn</div>`}</div>
      <div>
        <button class="btn" data-action="pick-ref" type="button">${m.ref ? "Đổi ảnh chuẩn" : "Tải ảnh chuẩn lên"}</button>
        <input type="file" id="ref-input" accept="image/png,image/jpeg,image/webp" hidden>
      </div>
    </div>
  </li>`;
}

function stepAnims(m, allReady) {
  return `<li class="step ${allReady ? "done" : ""}">
    ${stepTitle(2, "Khung hình từng animation")}
    <p class="step-lead">Quái đã có Spine: trong Spine Editor xuất chuỗi PNG nền trong suốt cho từng animation, rồi nhập thư mục đó. Quái mới: sinh bằng AI và chọn bản đẹp nhất.</p>
    <div class="anims">${m.anims.map((a) => animCard(m, a)).join("")}</div>
    <details class="spec-editor adv-only" data-key="spec-details" ${S.specOpen ? "open" : ""}>
      <summary>Sửa spec (JSON)</summary>
      <p class="muted">Đổi số khung, fps, sự kiện, cách khoá chân (stabilize), chọn khung tay (pick)… Xem reference/spec-format.md.</p>
      <textarea id="spec-text" data-key="spec-text" spellcheck="false">${esc(S.specDraft ?? JSON.stringify(m.spec, null, 2))}</textarea>
      <div class="gen-actions"><button class="btn" data-action="save-spec" type="button">Lưu spec</button>
      <button class="btn btn-quiet" data-action="reset-spec" type="button">Bỏ thay đổi</button></div>
    </details>
  </li>`;
}

function sourceText(a) {
  const s = a.source;
  if (!s) return "Chưa có khung hình.";
  if (s.origin === "spine") return `Đang dùng ${s.count} ảnh nhập từ Spine.`;
  if (s.origin === "ai") return `Đang dùng biến thể AI seed ${esc(s.seed)} (${s.count} ${s.kind === "video" ? "video" : "ảnh"}).`;
  return `Đang dùng ${s.count} ${s.kind === "video" ? "video" : "ảnh"}.`;
}

function animCard(m, a) {
  const events = a.events.map((e) => `sự kiện ${esc(e.name)} ở khung ${e.frame}`).join(", ");
  const facts = [`${a.frames} khung`, `${a.fps} khung/giây`, a.loop ? "lặp" : "chạy một lần"].concat(events ? [events] : []).join(", ");
  const up = S.uploading[a.name];
  const open = S.openGen.has(a.name);
  return `<article class="anim" id="anim-${esc(a.name)}">
    <div class="anim-main">
      <div class="lightbox">${a.source?.preview ? `<img src="${esc(a.source.preview)}" alt="Xem trước ${esc(a.name)}">` : `<div class="empty-frame">Chưa có khung hình</div>`}</div>
      <div>
        <div class="anim-name">${esc(a.name)}</div>
        <p class="anim-facts">${facts}</p>
        <p class="anim-source">${sourceText(a)}</p>
        ${up ? `<p class="progress-note" data-progress="${esc(a.name)}">Đang nhập ${up.done}/${up.total} ảnh…</p>` : ""}
        ${a.busy ? `<p class="progress-note">Đang sinh biến thể, xem tiến độ ở khay bên dưới.</p>` : ""}
        <div class="anim-actions">
          <button class="btn" data-action="pick-frames" data-anim="${esc(a.name)}" type="button" ${up ? "disabled" : ""}>Nhập thư mục PNG từ Spine</button>
          <input type="file" data-frames="${esc(a.name)}" webkitdirectory multiple hidden>
          <button class="btn" data-action="toggle-gen" data-anim="${esc(a.name)}" type="button" aria-expanded="${open}">Sinh bằng AI</button>
          ${a.source ? `<button class="btn btn-quiet btn-danger adv-only" data-action="clear" data-anim="${esc(a.name)}" type="button">Bỏ khung đang dùng</button>` : ""}
        </div>
      </div>
    </div>
    ${open ? genPanel(m, a) : ""}
    ${a.variants.length ? variantsBlock(a) : ""}
  </article>`;
}

function genPanel(m, a) {
  const conn = S.status?.doctor?.connected;
  const f = S.genForm[a.name] || {};
  const tpls = S.templates;
  const sel = f.template || (tpls.find((t) => t.ready) || tpls[0])?.file || "";
  const cur = tpls.find((t) => t.file === sel);
  const d = cur?.defaults || {};
  const notices = [];
  if (!conn) notices.push(`<p class="notice bad">ComfyUI chưa mở hoặc sai địa chỉ. Mở ComfyUI rồi bấm “Kiểm tra lại” ở thanh trên.</p>`);
  if (!m.ref) notices.push(`<p class="notice bad">Cần ảnh chuẩn ở bước 1 trước khi sinh.</p>`);
  if (!tpls.length) notices.push(`<p class="notice bad">Chưa có mẫu workflow nào trong plugin/skills/asset-gen/workflows.</p>`);
  if (cur && cur.status !== "tested") notices.push(`<p class="notice">Mẫu này chưa được chạy thử trên máy thật. Kết quả đầu tiên nên xem kỹ.</p>`);
  if (cur && cur.ready === false) notices.push(`<p class="notice bad">Máy này chưa chạy được mẫu này: ${esc(cur.problems.join("; "))}</p>`);
  const count = Number(f.count || 2);
  const k = (name) => `gen-${a.name}-${name}`;
  return `<div class="gen-panel" data-gen="${esc(a.name)}">
    ${notices.join("")}
    <div class="gen-grid">
      <label class="field field-wide"><span>Mẫu workflow</span>
        <select data-gen-field="template" data-key="${k("template")}">${tpls.map((t) => `<option value="${esc(t.file)}" ${t.file === sel ? "selected" : ""}>${esc(t.name)}${t.status !== "tested" ? " (chưa chạy thử)" : ""}</option>`).join("")}</select></label>
      <label class="field"><span>Số biến thể</span>
        <select data-gen-field="count" data-key="${k("count")}">${[1, 2, 3, 4].map((n) => `<option ${n === count ? "selected" : ""}>${n}</option>`).join("")}</select></label>
      <label class="field adv-only"><span>Seed bắt đầu</span><input type="number" data-gen-field="seed" data-key="${k("seed")}" placeholder="tự chọn" value="${esc(f.seed ?? "")}"></label>
      <label class="field adv-only"><span>Số khung sinh${cur?.rules?.NUM_FRAMES === "4n+1" ? " (dạng 4n+1)" : ""}</span><input type="number" data-gen-field="NUM_FRAMES" data-key="${k("nf")}" placeholder="${esc(d.NUM_FRAMES ?? "")}" value="${esc(f.NUM_FRAMES ?? "")}"></label>
      <label class="field adv-only"><span>Rộng × cao (px)</span><span style="display:flex;gap:6px"><input type="number" data-gen-field="WIDTH" data-key="${k("w")}" placeholder="${esc(d.WIDTH ?? "")}" value="${esc(f.WIDTH ?? "")}" aria-label="Rộng"><input type="number" data-gen-field="HEIGHT" data-key="${k("h")}" placeholder="${esc(d.HEIGHT ?? "")}" value="${esc(f.HEIGHT ?? "")}" aria-label="Cao"></span></label>
      <label class="field adv-only"><span>Số bước lấy mẫu</span><input type="number" data-gen-field="STEPS" data-key="${k("steps")}" placeholder="${esc(d.STEPS ?? "")}" value="${esc(f.STEPS ?? "")}"></label>
    </div>
    <label class="field" style="margin-top:12px"><span>Mô tả động tác</span>
      <textarea rows="2" data-gen-field="motion" data-key="${k("motion")}">${esc(f.motion ?? a.motion_hint)}</textarea>
      <small>Viết tiếng Anh cho kết quả ổn định. Phần nền, góc máy và hướng mặt được tự thêm vào.</small></label>
    <label class="field adv-only"><span>Prompt loại trừ (negative)</span>
      <textarea rows="2" data-gen-field="NEGATIVE" data-key="${k("neg")}" placeholder="${esc(d.NEGATIVE ?? "")}">${esc(f.NEGATIVE ?? "")}</textarea></label>
    <div class="gen-actions">
      <button class="btn btn-primary" data-action="generate" data-anim="${esc(a.name)}" type="button" ${!conn || !m.ref || !tpls.length || a.busy ? "disabled" : ""}>Sinh ${count} biến thể</button>
      <span class="muted">Mỗi biến thể có thể mất vài phút. Máy sẽ chạy lần lượt, anh/chị cứ làm việc khác.</span>
    </div>
  </div>`;
}

function variantsBlock(a) {
  return `<div class="variants"><h3>Biến thể đã sinh</h3><div class="v-strip">${a.variants.map((v) => {
    const first = (v.issues || [])[0];
    const note = first ? `${checkLabel(first.check)}${first.msg ? `: ${first.msg}` : ""}` : (v.qa === "pass" ? "Không có lỗi tự động." : "");
    return `<div class="v-tile ${v.in_use ? "in-use" : ""}">
      <div class="lightbox">${v.preview ? `<img src="${esc(v.preview)}" alt="Biến thể seed ${esc(v.seed)}" loading="lazy">` : `<div class="empty-frame">Chưa có ảnh xem trước</div>`}</div>
      <div class="v-head"><span>Seed ${esc(v.seed)}</span>${badge(v.qa)}</div>
      <p class="v-issue">${esc(note)}</p>
      <div class="v-actions">
        ${v.in_use ? `<span class="muted">Đang dùng</span>` : `<button class="btn btn-small" data-action="choose" data-anim="${esc(a.name)}" data-seed="${esc(v.seed)}" type="button">Dùng bản này</button>`}
        ${v.contact_sheet ? `<a class="btn btn-quiet btn-small adv-only" href="${esc(v.contact_sheet)}" target="_blank" rel="noopener">Bảng khung</a>` : ""}
        <button class="btn btn-quiet btn-small btn-danger adv-only" data-action="del-variant" data-anim="${esc(a.name)}" data-seed="${esc(v.seed)}" type="button">Xoá</button>
      </div>
    </div>`;
  }).join("")}</div></div>`;
}

function stepProcess(m, allReady) {
  const r = m.result;
  const missing = m.anims.filter((a) => !a.source).map((a) => a.name);
  let body = "";
  if (r) {
    const issues = r.checks.filter((c) => c.status !== "pass");
    const verdict = { pass: "Đạt", warn: "Đạt, có cảnh báo", fail: "Còn lỗi" }[r.status];
    body = `<div class="result">
      ${r.stale?.length ? `<p class="notice stale">Kết quả này đã cũ (${esc(r.stale.join(", "))}). Chạy lại hậu xử lý.</p>` : ""}
      <div class="result-head"><span class="result-verdict ${esc(r.status)}">${verdict}</span>
        <span class="muted">Atlas ${r.atlas.w} × ${r.atlas.h} px, mỗi ô ${r.cell.w} × ${r.cell.h} px. Xử lý lúc ${esc(fmtTime(r.processed_at))}.</span></div>
      ${issues.length ? `<ul class="issues">${issues.map((c) => `<li>${badge(c.status)}<span><b>${esc(c.scope)}</b>: ${esc(checkLabel(c.check))}${c.msg ? ` (${esc(c.msg)})` : ""}</span></li>`).join("")}</ul>` : `<p>Không có lỗi tự động. Vẫn cần xem bảng khung bằng mắt: sai chi tiết, mất vũ khí, đổi mặt giữa các khung.</p>`}
      <div class="gif-row">${Object.entries(r.gifs).map(([n, u]) => `<figure><div class="lightbox"><img src="${esc(u)}" alt="Kết quả ${esc(n)}" loading="lazy"></div><figcaption><span>${esc(n)}</span>${badge(r.per_animation[n])}</figcaption></figure>`).join("")}</div>
      <a href="${esc(r.contact_sheet)}" target="_blank" rel="noopener"><img class="sheet-img" src="${esc(r.contact_sheet)}" alt="Bảng toàn bộ khung sau hậu xử lý"></a>
      <p class="muted" style="margin-top:6px">Chữ thập đỏ là điểm neo ở chân, vạch xanh là mặt đất.</p>
      <div class="adv-only">
        <table class="qa-table"><thead><tr><th>Phạm vi</th><th>Kiểm tra</th><th>Kết quả</th><th>Giá trị</th><th>Ngưỡng</th></tr></thead><tbody>
        ${r.checks.map((c) => `<tr><td>${esc(c.scope)}</td><td>${esc(c.check)}</td><td>${badge(c.status)}</td><td>${esc(JSON.stringify(c.value))}</td><td>${esc(c.limit && typeof c.limit === "object" && "warn" in c.limit ? `${c.limit.warn} / ${c.limit.fail}` : JSON.stringify(c.limit ?? ""))}</td></tr>`).join("")}
        </tbody></table>
        <p style="margin-top:8px"><a href="${esc(r.manifest)}" target="_blank" rel="noopener">Manifest JSON</a> · <a href="${esc(r.atlas.url)}" target="_blank" rel="noopener">Atlas PNG</a></p>
      </div>
    </div>`;
  }
  return `<li class="step ${r && !r.stale?.length ? "done" : ""}">
    ${stepTitle(3, "Hậu xử lý và kiểm tra")}
    <p class="step-lead">Tách nền, đưa mọi khung về cùng cỡ, khoá chân cho hết rung, ghép thành atlas rồi tự kiểm tra lỗi.</p>
    <div class="result" style="margin-bottom:${r ? "16px" : "0"}">
      <div class="gen-actions" style="margin-top:0">
        <button class="btn btn-primary" data-action="process" type="button" ${!allReady || m.processing ? "disabled" : ""}>${r ? "Chạy lại hậu xử lý" : "Chạy hậu xử lý"}</button>
        ${m.processing ? `<span class="progress-note">Đang hậu xử lý…</span>` : ""}
        ${!allReady ? `<span class="muted">Còn thiếu khung hình: ${esc(missing.join(", "))}</span>` : ""}
      </div>
    </div>
    ${body}
  </li>`;
}

function stepApprove(m) {
  const ap = m.approval;
  const r = m.result;
  let body;
  if (ap) {
    const d = new Date(ap.approved_at);
    body = `<div class="approve">
      <div class="seal ${S.justApproved ? "stamp" : ""}" role="img" aria-label="Đã duyệt">Đã<br>duyệt<small>${esc(d.toLocaleDateString("vi-VN"))}</small></div>
      <div class="approve-info">
        <p>Duyệt bởi <b>${esc(ap.approved_by)}</b> lúc ${esc(fmtTime(ap.approved_at))}.</p>
        <p>QA khi duyệt: ${badge(ap.qa_status)}${ap.forced ? " (duyệt dù còn lỗi)" : ""}</p>
        ${ap.note ? `<p>Ghi chú: ${esc(ap.note)}</p>` : ""}
        <p>Đã xuất vào thư mục <code>${esc(ap.folder)}</code></p>
        <p class="muted">Bước tiếp theo: nộp draft vào game. Phần này chưa nối tự động.</p>
        <div><button class="btn btn-quiet btn-danger" data-action="unapprove" type="button">Huỷ duyệt</button></div>
      </div>
    </div>`;
  } else if (!r) {
    body = `<p class="step-lead">Chạy hậu xử lý trước khi duyệt.</p>`;
  } else {
    const reviewer = S.status?.settings?.reviewer;
    body = `<div class="approve"><div class="approve-form">
      <label class="field"><span>Ghi chú${r.status === "fail" ? " (bắt buộc khi duyệt dù còn lỗi)" : ""}</span>
        <textarea id="approve-note" data-key="approve-note" rows="2"></textarea></label>
      ${r.status === "fail" ? `<label class="radio"><input type="checkbox" id="approve-force"> Duyệt dù QA còn lỗi</label>` : ""}
      ${!reviewer ? `<p class="muted">Chưa đặt tên người duyệt. Mở Cài đặt để đặt tên.</p>` : ""}
      <div class="gen-actions"><button class="btn btn-primary" data-action="approve" type="button" ${r.stale?.length ? "disabled" : ""}>Duyệt và xuất</button>
      ${r.stale?.length ? `<span class="muted">Chạy lại hậu xử lý trước.</span>` : ""}</div>
    </div></div>`;
  }
  return `<li class="step ${ap ? "done" : ""}">
    ${stepTitle(4, "Duyệt và xuất")}
    ${ap || !r ? "" : `<p class="step-lead">Xem kỹ bảng khung ở bước 3. Khi duyệt, atlas, manifest và báo cáo QA được chép sang thư mục xuất kèm tên người duyệt.</p>`}
    ${body}
  </li>`;
}

// ───────── khay việc ─────────
function renderTray() {
  const tray = $("#tray");
  const now = Date.now();
  // đang chạy luôn hiện; việc xong hiện 60 giây; việc lỗi giữ 10 phút để kịp đọc
  const age = (j) => (j.finished ? now - new Date(j.finished).getTime() : 0);
  const jobs = S.jobs.filter((j) => ["queued", "running"].includes(j.status)
    || (j.status === "error" && age(j) < 10 * 60 * 1000) || (j.status !== "error" && age(j) < 60 * 1000)).slice(0, 4);
  tray.classList.toggle("has-jobs", jobs.length > 0);
  tray.innerHTML = jobs.map((j) => {
    const icon = j.status === "running" ? `<span class="spin" aria-hidden="true"></span>` : `<span class="state-dot ${esc(j.status)}" aria-hidden="true"></span>`;
    const st = { queued: "đang chờ", running: `đang chạy, ${seconds(j.elapsed)}`, done: `xong sau ${seconds(j.elapsed)}`, error: "lỗi", cancelled: "đã dừng" }[j.status];
    const canCancel = ["queued", "running"].includes(j.status);
    return `<div class="job">${icon}<span>${esc(j.title)}</span><span class="muted">${esc(st)}</span>
      ${canCancel ? `<button class="btn btn-quiet btn-small" data-action="cancel-job" data-job="${esc(j.id)}" type="button">Dừng</button>` : "<span></span>"}
      ${j.error ? `<span class="job-error">${esc(j.error)}</span>` : ""}
      ${j.log?.length ? `<details class="adv-only" data-job-log="${esc(j.id)}" ${S.openLogs.has(j.id) ? "open" : ""}><summary>Nhật ký</summary><pre>${esc(j.log.join("\n"))}</pre></details>` : ""}
    </div>`;
  }).join("");
}

async function pollJobs() {
  try {
    S.jobs = await api("GET", "/api/jobs");
  } catch { return; }
  const active = new Set(S.jobs.filter((j) => ["queued", "running"].includes(j.status)).map((j) => j.id));
  const finished = [...S.activeIds].filter((id) => !active.has(id));
  S.activeIds = active;
  renderTray();
  if (finished.length) {
    const fj = S.jobs.filter((j) => finished.includes(j.id));
    for (const j of fj) {
      if (j.status === "error") toast(`${j.title}: ${j.error}`);
      else if (j.status === "done") toast(`${j.title}: xong`, "ok");
    }
    await loadList();
    if (fj.some((j) => j.monster === S.current)) await loadMonster();
  }
}

// ───────── tiện ích giao diện ─────────
function rememberFocus() {
  const ae = document.activeElement;
  if (!ae || !ae.dataset?.key) return null;
  return { key: ae.dataset.key, sel: "selectionStart" in ae ? [ae.selectionStart, ae.selectionEnd] : null };
}
function restoreFocus(f) {
  if (!f) return;
  const el = document.querySelector(`[data-key="${CSS.escape(f.key)}"]`);
  if (!el) return;
  el.focus();
  if (f.sel && el.setSelectionRange) { try { el.setSelectionRange(...f.sel); } catch { /* bỏ qua */ } }
}
const sortNatural = (a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });

async function uploadFrames(anim, fileList) {
  const files = [...fileList].filter((f) => IMG_RE.test(f.name));
  if (!files.length) { toast("Thư mục không có ảnh PNG/JPG/WEBP."); return; }
  const groups = {};
  for (const f of files) {
    const rel = f.webkitRelativePath || f.name;
    const dir = rel.includes("/") ? rel.slice(0, rel.lastIndexOf("/")) : "";
    (groups[dir] ||= []).push(f);
  }
  let pick = Object.values(groups);
  if (pick.length > 1) {
    const match = Object.entries(groups).find(([dir]) => dir.split("/").pop() === anim);
    if (!match) { toast(`Thư mục có nhiều thư mục con. Chọn đúng thư mục chứa ảnh của '${anim}', hoặc đặt tên thư mục con là '${anim}'.`); return; }
    pick = [match[1]];
  }
  const list = pick[0].sort((a, b) => sortNatural(a.webkitRelativePath || a.name, b.webkitRelativePath || b.name));
  S.uploading[anim] = { done: 0, total: list.length };
  renderMain();
  try {
    for (let i = 0; i < list.length; i++) {
      const f = list[i];
      await api("POST", `/api/monsters/${S.current}/anims/${encodeURIComponent(anim)}/frames?name=${encodeURIComponent(f.name)}&first=${i === 0 ? 1 : 0}`, await f.arrayBuffer(), true);
      S.uploading[anim].done = i + 1;
      const p = document.querySelector(`[data-progress="${CSS.escape(anim)}"]`);
      if (p) p.textContent = `Đang nhập ${i + 1}/${list.length} ảnh…`;
    }
    toast(`Đã nhập ${list.length} ảnh cho '${anim}'`, "ok");
  } catch (e) {
    toast(`Nhập ảnh '${anim}' bị lỗi: ${e.message}`);
  } finally {
    delete S.uploading[anim];
    await Promise.all([loadMonster(), loadList()]);
  }
}

// ───────── hành động ─────────
const actions = {
  async recheck() { await loadStatus(true); await loadTemplates(); if (S.monster) renderMain(); },
  new() { openNewDialog(); },
  "pick-ref"() { $("#ref-input").click(); },
  "pick-frames"(el) { document.querySelector(`input[data-frames="${CSS.escape(el.dataset.anim)}"]`).click(); },
  "toggle-gen"(el) {
    const a = el.dataset.anim;
    S.openGen.has(a) ? S.openGen.delete(a) : S.openGen.add(a);
    renderMain();
  },
  async generate(el) {
    const a = el.dataset.anim;
    const f = S.genForm[a] || {};
    const anim = S.monster.anims.find((x) => x.name === a);
    const tpl = f.template || (S.templates.find((t) => t.ready) || S.templates[0])?.file;
    const sets = {};
    for (const k of ["NUM_FRAMES", "WIDTH", "HEIGHT", "STEPS", "NEGATIVE"]) if (f[k] !== undefined && String(f[k]).trim() !== "") sets[k] = f[k];
    el.disabled = true;
    try {
      await api("POST", `/api/monsters/${S.current}/anims/${encodeURIComponent(a)}/generate`, {
        template: tpl, motion: f.motion ?? anim.motion_hint, count: Number(f.count || 2), seed: f.seed || null, sets,
      });
      await pollJobs();
      await loadMonster();
    } catch (e) { toast(e.message); el.disabled = false; }
  },
  async choose(el) {
    try { await api("POST", `/api/monsters/${S.current}/anims/${encodeURIComponent(el.dataset.anim)}/choose`, { seed: Number(el.dataset.seed) }); }
    catch (e) { toast(e.message); }
    await Promise.all([loadMonster(), loadList()]);
  },
  async "del-variant"(el) {
    if (!confirm(`Xoá biến thể seed ${el.dataset.seed} của '${el.dataset.anim}'?`)) return;
    try { await api("DELETE", `/api/monsters/${S.current}/anims/${encodeURIComponent(el.dataset.anim)}/variants/${el.dataset.seed}`); }
    catch (e) { toast(e.message); }
    await loadMonster();
  },
  async clear(el) {
    if (!confirm(`Bỏ khung đang dùng của '${el.dataset.anim}'? Biến thể AI vẫn được giữ.`)) return;
    try { await api("POST", `/api/monsters/${S.current}/anims/${encodeURIComponent(el.dataset.anim)}/clear`, {}); }
    catch (e) { toast(e.message); }
    await Promise.all([loadMonster(), loadList()]);
  },
  async process(el) {
    el.disabled = true;
    try { await api("POST", `/api/monsters/${S.current}/process`, {}); await pollJobs(); }
    catch (e) { toast(e.message); }
    await loadMonster();
  },
  async approve(el) {
    const note = $("#approve-note")?.value || "";
    const force = $("#approve-force")?.checked || false;
    el.disabled = true;
    try {
      await api("POST", `/api/monsters/${S.current}/approve`, { note, force });
      S.justApproved = true;
      toast("Đã duyệt và xuất", "ok");
    } catch (e) { toast(e.message); el.disabled = false; return; }
    await Promise.all([loadMonster(), loadList()]);
  },
  async unapprove() {
    if (!confirm("Huỷ duyệt và xoá bản đã xuất của quái này?")) return;
    try { await api("POST", `/api/monsters/${S.current}/unapprove`, {}); } catch (e) { toast(e.message); }
    await Promise.all([loadMonster(), loadList()]);
  },
  async "delete-monster"() {
    const id = S.current;
    if (prompt(`Xoá hẳn quái '${id}' cùng mọi khung, biến thể và bản xuất? Gõ lại mã quái để xác nhận.`) !== id) return;
    try { await api("DELETE", `/api/monsters/${id}`); location.hash = ""; await loadList(); }
    catch (e) { toast(e.message); }
  },
  async "save-spec"() {
    let spec;
    try { spec = JSON.parse($("#spec-text").value); } catch { toast("Spec không phải JSON hợp lệ."); return; }
    try { await api("PUT", `/api/monsters/${S.current}/spec`, { spec }); S.specDraft = null; toast("Đã lưu spec", "ok"); }
    catch (e) { toast(e.message); return; }
    await Promise.all([loadMonster(), loadList()]);
  },
  "reset-spec"() { S.specDraft = null; renderMain(); },
  async "cancel-job"(el) {
    try { await api("POST", `/api/jobs/${el.dataset.job}/cancel`, {}); } catch (e) { toast(e.message); }
    await pollJobs();
  },
};

document.addEventListener("click", (ev) => {
  const el = ev.target.closest("[data-action]");
  if (!el) return;
  const fn = actions[el.dataset.action];
  if (fn) { ev.preventDefault(); fn(el); }
});

document.addEventListener("input", (ev) => {
  const el = ev.target;
  const field = el.dataset.genField;
  if (field) {
    const a = el.closest("[data-gen]").dataset.gen;
    (S.genForm[a] ||= {})[field] = el.value;
    if (field === "count" || field === "template") renderMain();
  }
  if (el.id === "spec-text") S.specDraft = el.value;
});

document.addEventListener("change", async (ev) => {
  const el = ev.target;
  if (el.id === "ref-input" && el.files?.[0]) {
    const f = el.files[0];
    try {
      await api("POST", `/api/monsters/${S.current}/ref?name=${encodeURIComponent(f.name)}`, await f.arrayBuffer(), true);
      toast("Đã cập nhật ảnh chuẩn", "ok");
    } catch (e) { toast(e.message); }
    await loadMonster();
  } else if (el.dataset.frames && el.files?.length) {
    await uploadFrames(el.dataset.frames, el.files);
  }
});

document.addEventListener("toggle", (ev) => {
  const el = ev.target;
  if (el.dataset?.jobLog) { el.open ? S.openLogs.add(el.dataset.jobLog) : S.openLogs.delete(el.dataset.jobLog); }
  if (el.dataset?.key === "spec-details") S.specOpen = el.open;
}, true);

// ───────── hộp thoại ─────────
async function openNewDialog() {
  const dlg = $("#dlg-new");
  const form = $("#form-new");
  form.reset();
  $(".form-error", form).textContent = "";
  try { S.presets = await api("GET", "/api/presets"); } catch { S.presets = {}; }
  const names = { mob_thuong: "Quái thường", mob_tinh_anh: "Quái tinh anh", boss: "Boss", mob_bay: "Quái bay" };
  form.preset.innerHTML = Object.keys(S.presets).map((k) => `<option value="${esc(k)}">${esc(names[k] || k)} (${Object.keys(S.presets[k].animations).length} animation)</option>`).join("");
  syncFrom();
  dlg.showModal();
  form.monster_id.focus();
}
function syncFrom() {
  const form = $("#form-new");
  const fromSpine = form.from.value === "spine";
  $(".from-spine", form).hidden = !fromSpine;
  $(".from-preset", form).hidden = fromSpine;
}
$("#form-new").addEventListener("change", (e) => { if (e.target.name === "from") syncFrom(); });
$("#form-new").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const err = $(".form-error", form);
  err.textContent = "";
  const body = {
    monster_id: form.monster_id.value.trim(), display_name: form.display_name.value.trim(),
    target_height: form.target_height.value, fps: form.fps.value,
  };
  if (form.from.value === "spine") {
    const f = form.spine.files?.[0];
    if (!f) { err.textContent = "Chọn file Spine JSON, hoặc chuyển sang bộ animation chuẩn."; return; }
    body.spine_json = await f.text();
  } else {
    body.preset = form.preset.value;
  }
  try {
    const r = await api("POST", "/api/monsters", body);
    $("#dlg-new").close();
    await loadList();
    location.hash = `#/m/${r.id}`;
  } catch (ex) { err.textContent = ex.message; }
});
$("#btn-new").addEventListener("click", openNewDialog);

$("#btn-settings").addEventListener("click", () => {
  const form = $("#form-settings");
  const s = S.status?.settings || {};
  form.reviewer.value = s.reviewer || "";
  form.comfy_url.value = s.comfy_url || "";
  form.prompt_suffix.value = s.prompt_suffix || "";
  $("#workspace-path").textContent = S.status ? `Dữ liệu lưu tại: ${S.status.workspace}` : "";
  $(".form-error", form).textContent = "";
  $("#dlg-settings").showModal();
});
$("#form-settings").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  try {
    await api("PUT", "/api/settings", { reviewer: form.reviewer.value, comfy_url: form.comfy_url.value, prompt_suffix: form.prompt_suffix.value });
    $("#dlg-settings").close();
    await loadStatus(true);
    await loadTemplates();
    if (S.monster) renderMain();
  } catch (ex) { $(".form-error", form).textContent = ex.message; }
});
document.querySelectorAll("dialog [data-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));

// ───────── chế độ nâng cao & điều hướng ─────────
const adv = $("#advanced");
try { adv.checked = localStorage.getItem("xq-advanced") === "1"; } catch { /* trình duyệt chặn lưu trữ */ }
document.body.classList.toggle("advanced", adv.checked);
adv.addEventListener("change", () => {
  document.body.classList.toggle("advanced", adv.checked);
  try { localStorage.setItem("xq-advanced", adv.checked ? "1" : "0"); } catch { /* bỏ qua */ }
});

function route() {
  const m = location.hash.match(/^#\/m\/([a-z0-9_]+)$/);
  const next = m ? m[1] : null;
  if (next !== S.current) { S.openGen.clear(); S.genForm = {}; S.specDraft = null; S.specOpen = false; }
  S.current = next;
  renderList();
  loadMonster().then(() => { if (next) $("#main").focus({ preventScroll: true }); });
}
window.addEventListener("hashchange", route);

(async function init() {
  await Promise.all([loadStatus(), loadList().catch(() => {}), loadTemplates()]);
  route();
  await pollJobs();
  setInterval(pollJobs, 1500);
  setInterval(() => loadStatus(), 30000);
})();
