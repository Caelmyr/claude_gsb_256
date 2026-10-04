/* 视图：拼图拼贴。多图选择 -> 排序/间距/背景/对齐/缩放策略 -> 实时预览 -> 一键保存为新图。 */
window.Views = window.Views || {};
window.Views.collage = (function () {
  const C = window.Common;

  // 选中的图像 id（数组，顺序即拼接顺序）
  let order = [];
  let imagesCache = [];
  let lastPreview = null;
  let previewSeq = 0;

  const DEFAULTS = {
    mode: "vertical", scaling: "uniform", gap: 10,
    background: "#ffffff", align: "center", columns: 0,
  };
  const cfg = Object.assign({}, DEFAULTS);

  function recOf(id) { return imagesCache.find((im) => im.id === id); }

  async function loadImages(el) {
    imagesCache = await C.fetchImages();
    const box = el.querySelector("#cg-gallery");
    box.innerHTML = C.galleryHTML(imagesCache);
    box.querySelectorAll(".card").forEach((card) => {
      card.classList.toggle("selected", order.includes(card.dataset.id));
      card.onclick = () => {
        const id = card.dataset.id;
        if (order.includes(id)) {
          order = order.filter((x) => x !== id);
        } else {
          if (order.length >= 20) { C.toast("单次最多拼接 20 张", "error"); return; }
          order.push(id);
        }
        card.classList.toggle("selected", order.includes(id));
        renderOrder(el);
        schedulePreview(el);
      };
    });
  }

  function renderOrder(el) {
    const box = el.querySelector("#cg-order");
    el.querySelector("#cg-count").textContent = `已选 ${order.length} 张`;
    if (!order.length) {
      box.innerHTML = `<div class="dim" style="font-size:12px;padding:8px 2px">从左侧点选图像，这里可调整拼接顺序</div>`;
      return;
    }
    box.innerHTML = order.map((id, i) => {
      const rec = recOf(id);
      const name = rec ? rec.filename : id.slice(0, 8);
      const thumb = rec ? rec.thumbnail_url : "";
      return `<div class="cg-order-item" data-id="${C.esc(id)}">
        <span class="cg-order-no">${i + 1}</span>
        <img src="${C.esc(thumb)}" class="cg-order-thumb">
        <span class="cg-order-name" title="${C.esc(name)}">${C.esc(name)}</span>
        <span class="cg-order-ctrl">
          <button class="btn btn-sm" data-act="up" ${i === 0 ? "disabled" : ""}>↑</button>
          <button class="btn btn-sm" data-act="down" ${i === order.length - 1 ? "disabled" : ""}>↓</button>
          <button class="btn btn-sm btn-danger" data-act="rm">✕</button>
        </span>
      </div>`;
    }).join("");
    box.querySelectorAll(".cg-order-item").forEach((row) => {
      const id = row.dataset.id;
      const idx = order.indexOf(id);
      row.querySelector('[data-act="up"]').onclick = () => move(idx, -1);
      row.querySelector('[data-act="down"]').onclick = () => move(idx, 1);
      row.querySelector('[data-act="rm"]').onclick = () => {
        order = order.filter((x) => x !== id);
        loadImages(el).then(() => renderAll(el));
        schedulePreview(el);
      };
    });
  }

  function move(idx, delta) {
    const el = document.querySelector('.view[data-view="collage"]');
    const j = idx + delta;
    if (j < 0 || j >= order.length) return;
    [order[idx], order[j]] = [order[j], order[idx]];
    renderAll(el);
    schedulePreview(el);
  }

  function renderAll(el) {
    renderOrder(el);
    const card = (id) => el.querySelector(`#cg-gallery .card[data-id="${id}"]`);
    el.querySelectorAll("#cg-gallery .card").forEach((c) =>
      c.classList.toggle("selected", order.includes(c.dataset.id)));
  }

  function payload() {
    return {
      items: order.map((id) => ({ image_id: id })),
      mode: cfg.mode, scaling: cfg.scaling, gap: Number(cfg.gap),
      background: cfg.background, align: cfg.align,
      columns: cfg.mode === "grid" ? Number(cfg.columns) || 0 : 0,
    };
  }

  const schedulePreview = C.debounce(doPreview, 300);

  async function doPreview(el) {
    if (order.length < 2) {
      el.querySelector("#cg-preview").innerHTML =
        `<div class="empty"><span class="big">🧩</span>至少选择 2 张图像后在此预览布局</div>`;
      el.querySelector("#cg-meta").innerHTML = "";
      el.querySelector("#cg-failed").innerHTML = "";
      lastPreview = null;
      return;
    }
    const seq = ++previewSeq;
    const stage = el.querySelector("#cg-preview");
    stage.innerHTML = `<div class="loading">生成预览…</div>`;
    let r;
    try {
      r = await Api.post("/api/collage/preview", payload());
    } catch (e) {
      if (seq === previewSeq) stage.innerHTML = `<div class="empty">预览失败：${C.esc(e.message)}</div>`;
      return;
    }
    if (seq !== previewSeq) return;
    lastPreview = r;
    stage.innerHTML = `<img src="${r.preview_url}" alt="拼图预览">`;
    el.querySelector("#cg-meta").innerHTML =
      `<span class="badge green">${labelMode(r.mode)} · ${r.scaling === "uniform" ? "统一缩放" : "原比例保留"}</span>
       <span class="dim">${r.width} × ${r.height}px</span>
       <span class="dim">${r.rows} 行 × ${r.columns} 列 · 共 ${r.count} 张</span>`;
    renderFailed(el, r.failed || []);
  }

  function renderFailed(el, failed) {
    const box = el.querySelector("#cg-failed");
    if (!failed.length) { box.innerHTML = ""; return; }
    box.innerHTML = `<div class="cg-failed-title">${failed.length} 张未能参与拼接，其余 ${lastPreview ? lastPreview.count : 0} 张已照常完成：</div>` +
      failed.map((f) => `<div class="cg-failed-item">
        <span class="badge red">#${f.index + 1}</span>
        <span>${C.esc(f.filename || f.image_id.slice(0, 10))}</span>
        <span class="dim">${C.esc(f.reason)}</span></div>`).join("");
  }

  function labelMode(m) {
    return { horizontal: "横向长图", vertical: "纵向长图", grid: "网格拼贴" }[m] || m;
  }

  async function saveResult(el) {
    if (order.length < 2) { C.toast("请先选择至少 2 张图像", "error"); return; }
    const btn = el.querySelector("#cg-save");
    btn.disabled = true; btn.textContent = "拼接中…";
    try {
      const r = await Api.post("/api/collage", payload());
      // 一键保存为新图：直接触发浏览器下载
      const a = document.createElement("a");
      a.href = r.file_url;
      a.download = `collage_${r.width}x${r.height}_${Date.now()}.png`;
      document.body.appendChild(a); a.click(); a.remove();
      C.toast(r.failed && r.failed.length
        ? `已保存（${r.failed.length} 张失败已跳过）` : "拼图已保存为新图", "success");
      if (r.failed && r.failed.length) renderFailed(el, r.failed);
    } catch (e) {
      C.toast("拼图失败：" + e.message, "error");
    } finally {
      btn.disabled = false; btn.textContent = "⬇ 一键保存为新图";
    }
  }

  function bindControls(el) {
    const set = (key) => (e) => {
      cfg[key] = e.target.value;
      if (key === "mode") updateColumnsVisibility(el);
      schedulePreview(el);
    };
    el.querySelector("#cg-mode").onchange = set("mode");
    el.querySelector("#cg-scaling").onchange = set("scaling");
    el.querySelector("#cg-align").onchange = set("align");
    el.querySelector("#cg-bg").oninput = set("background");
    el.querySelector("#cg-gap").oninput = (e) => {
      el.querySelector("#cg-gap-val").textContent = e.target.value + "px";
      cfg.gap = e.target.value;
      schedulePreview(el);
    };
    el.querySelector("#cg-columns").onchange = (e) => { cfg.columns = e.target.value; schedulePreview(el); };
    el.querySelectorAll(".cg-swatch").forEach((b) => {
      b.onclick = () => {
        cfg.background = b.dataset.bg;
        el.querySelector("#cg-bg").value = b.dataset.bg;
        schedulePreview(el);
      };
    });
    el.querySelector("#cg-refresh").onclick = () => { C.invalidate("images"); loadImages(el); };
    el.querySelector("#cg-clear").onclick = () => {
      order = []; loadImages(el).then(() => renderAll(el));
      schedulePreview(el);
    };
    el.querySelector("#cg-save").onclick = () => saveResult(el);
  }

  function updateAlignLabel(el) {
    const sel = el.querySelector("#cg-align");
    const vertical = cfg.mode === "vertical";
    const labels = vertical
      ? { start: "左对齐", center: "水平居中", end: "右对齐" }
      : { start: "顶部对齐", center: "垂直居中", end: "底部对齐" };
    const cur = cfg.align;
    sel.innerHTML = Object.keys(labels).map((k) =>
      `<option value="${k}" ${k === cur ? "selected" : ""}>${labels[k]}</option>`).join("");
  }

  function updateColumnsVisibility(el) {
    el.querySelector("#cg-columns-row").style.display = cfg.mode === "grid" ? "" : "none";
    updateAlignLabel(el);
  }

  return {
    mount(el) {
      el.innerHTML = `
        <div class="cg-layout">
          <div class="panel cg-gallery-panel">
            <div class="panel-title">选择图像<span class="dim">点击加入/移除，最多 20 张</span></div>
            <div id="cg-gallery" class="cg-gallery"></div>
            <div class="toolbar" style="margin:10px 0 0">
              <button class="btn btn-sm" id="cg-refresh">刷新图库</button>
            </div>
          </div>

          <div class="panel cg-side">
            <div class="panel-title">排列顺序</div>
            <div class="toolbar" style="margin-bottom:8px">
              <span class="badge" id="cg-count">已选 0 张</span>
              <span class="spacer"></span>
              <button class="btn btn-sm btn-ghost" id="cg-clear">清空</button>
            </div>
            <div id="cg-order" class="cg-order"></div>

            <div class="panel-title" style="margin-top:16px">拼图参数</div>
            <div class="field"><label>拼接方式</label>
              <select id="cg-mode">
                <option value="vertical">纵向长图</option>
                <option value="horizontal">横向长图</option>
                <option value="grid">网格拼贴</option>
              </select>
            </div>
            <div class="field"><label>缩放方式<span class="hint">统一缩放不变形铺满；原比例保留只缩小不放大</span></label>
              <select id="cg-scaling">
                <option value="uniform">统一缩放（推荐）</option>
                <option value="original">按原比例保留</option>
              </select>
            </div>
            <div class="field" id="cg-columns-row" style="display:none"><label>网格列数<span class="hint">0 = 自动</span></label>
              <select id="cg-columns">
                <option value="0">自动</option>
                <option value="2">2 列</option>
                <option value="3">3 列</option>
                <option value="4">4 列</option>
                <option value="5">5 列</option>
              </select>
            </div>
            <div class="field"><label>对齐<span class="hint">原比例混排时图在轨道内的对齐</span></label>
              <select id="cg-align"></select>
            </div>
            <div class="field"><label>间距 <span id="cg-gap-val" class="range-val">10px</span></label>
              <input type="range" id="cg-gap" min="0" max="100" step="2" value="10">
            </div>
            <div class="field"><label>背景颜色</label>
              <div class="cg-bg-row">
                <input type="color" id="cg-bg" value="#ffffff">
                <button class="btn btn-sm cg-swatch" data-bg="#ffffff" style="background:#fff">白</button>
                <button class="btn btn-sm cg-swatch" data-bg="#000000" style="background:#000;color:#fff">黑</button>
                <button class="btn btn-sm cg-swatch" data-bg="#f0f0f0" style="background:#f0f0f0">浅灰</button>
                <button class="btn btn-sm cg-swatch" data-bg="#1b2330" style="background:#1b2330;color:#fff">深色</button>
              </div>
            </div>
            <button class="btn btn-primary" id="cg-save" style="width:100%;justify-content:center;margin-top:4px">⬇ 一键保存为新图</button>
          </div>

          <div class="panel cg-preview-panel">
            <div class="panel-title">布局预览<span class="dim">调整参数即时预览，保存前确认效果</span></div>
            <div class="toolbar" id="cg-meta" style="margin-bottom:10px"></div>
            <div class="stage cg-stage" id="cg-preview"></div>
            <div id="cg-failed" class="cg-failed"></div>
          </div>
        </div>`;

      bindControls(el);
      updateAlignLabel(el);
      updateColumnsVisibility(el);
      loadImages(el).then(() => renderAll(el));
      doPreview(el);
    },
    refresh() {
      const el = document.querySelector('.view[data-view="collage"]');
      if (el && this.mounted) { C.invalidate("images"); loadImages(el).then(() => renderAll(el)); }
    },
  };
})();
