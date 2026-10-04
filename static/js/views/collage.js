/* 视图：图片拼接（长图 / 拼贴画）。
 * 左栏多选图库并调整顺序，中栏实时布局预览，右栏拼接参数；
 * 预览走 /api/collage/preview（只算布局），保存走 /api/collage（渲染+入库）。
 */
window.Views = window.Views || {};
window.Views.collage = (function () {
  const C = window.Common;

  // 有序选择列表（元素为图像记录）
  let order = [];
  let imagesCache = [];
  let lastPlan = null;
  let previewTimer = null;
  let resultUrl = null;

  const DEFAULTS = {
    layout: "grid", scale_mode: "original", fit: "contain",
    gap: 10, padding: 10, cols: 3, cell_ratio: 0,
    background: "#ffffff", cross_align: "center",
  };

  return {
    mount(el) {
      el.innerHTML = `
        <div class="split-3 collage-layout">
          <div class="col">
            <div class="panel">
              <div class="panel-title">选择图片（可多选）<span class="dim">点击加入 / 移除</span></div>
              <div id="cg-gallery" class="collage-gallery"></div>
              <div class="toolbar" style="margin:10px 0 0">
                <span id="cg-count" class="badge">已选 0 张</span>
                <span class="spacer"></span>
                <button class="btn btn-sm" id="cg-clear">清空</button>
              </div>
            </div>
            <div class="panel">
              <div class="panel-title">排列顺序<span class="dim">拖动或用按钮调整</span></div>
              <div id="cg-order" class="collage-order"></div>
            </div>
          </div>

          <div class="col">
            <div class="panel">
              <div class="panel-title">布局预览
                <span class="dim" id="cg-plan-dim"></span>
              </div>
              <div class="stage collage-stage" id="cg-stage">
                <div class="empty"><span class="big">🧷</span>从左侧选择至少 1 张图片开始拼接</div>
              </div>
              <div id="cg-failed" class="collage-failed"></div>
              <div class="toolbar" style="margin:12px 0 0">
                <button class="btn btn-primary" id="cg-save">⚙ 生成并保存为新图</button>
                <a class="btn btn-ghost" id="cg-download" style="text-decoration:none;display:none" download="collage.png">⬇ 下载 PNG</a>
                <span class="spacer"></span>
                <span id="cg-status" class="dim" style="font-size:12px"></span>
              </div>
            </div>
            <div id="cg-result"></div>
          </div>

          <div class="col">
            <div class="panel">
              <div class="panel-title">拼接参数</div>
              <div class="field">
                <label>拼接方式</label>
                <div class="seg" id="cg-layout">
                  <button type="button" data-v="horizontal">横向长图</button>
                  <button type="button" data-v="vertical">纵向长图</button>
                  <button type="button" data-v="grid">网格拼贴</button>
                </div>
              </div>
              <div class="field">
                <label>尺寸策略</label>
                <div class="seg" id="cg-scale">
                  <button type="button" data-v="uniform">统一缩放</button>
                  <button type="button" data-v="original">保留原比例</button>
                </div>
                <span class="hint" id="cg-scale-hint"></span>
              </div>
              <div class="field" id="cg-fit-field">
                <label>比例不一致时</label>
                <div class="seg" id="cg-fit">
                  <button type="button" data-v="contain">完整留白</button>
                  <button type="button" data-v="cover">填满裁切</button>
                </div>
              </div>
              <div class="field" id="cg-align-field">
                <label>交叉轴对齐（长图）</label>
                <div class="seg" id="cg-align">
                  <button type="button" data-v="start">起边</button>
                  <button type="button" data-v="center">居中</button>
                  <button type="button" data-v="end">终边</button>
                </div>
              </div>
              <div class="field" id="cg-cols-field">
                <label>每行列数（网格）<span class="hint">相册式排版的每行参考数量</span></label>
                <div class="range-row">
                  <input type="range" id="cg-cols" min="1" max="6" step="1" value="3">
                  <span class="range-val" id="cg-cols-val">3</span>
                </div>
              </div>
              <div class="field" id="cg-ratio-field">
                <label>单元格宽高比（统一网格）<span class="hint">0 = 按图片自动</span></label>
                <div class="range-row">
                  <input type="range" id="cg-ratio" min="0" max="2" step="0.05" value="0">
                  <span class="range-val" id="cg-ratio-val">自动</span>
                </div>
              </div>
              <div class="field">
                <label>图片间距 <span class="hint">px</span></label>
                <div class="range-row">
                  <input type="range" id="cg-gap" min="0" max="80" step="2" value="10">
                  <span class="range-val" id="cg-gap-val">10</span>
                </div>
              </div>
              <div class="field">
                <label>外边距 <span class="hint">px，默认与间距相同</span></label>
                <div class="range-row">
                  <input type="range" id="cg-padding" min="0" max="80" step="2" value="10">
                  <span class="range-val" id="cg-padding-val">10</span>
                </div>
              </div>
              <div class="field">
                <label>背景颜色（留白处底色）</label>
                <div class="color-row">
                  <input type="color" id="cg-bg" value="#ffffff">
                  <button type="button" class="btn btn-sm" id="cg-bg-white">白</button>
                  <button type="button" class="btn btn-sm" id="cg-bg-black">黑</button>
                  <button type="button" class="btn btn-sm" data-bg="#0f141b">深</button>
                </div>
              </div>
            </div>
          </div>
        </div>`;

      loadGallery(el);
      bindControls(el);
      renderOrder(el);
      updateControlVisibility(el);
    },

    refresh() {
      const el = document.querySelector('.view[data-view="collage"]');
      if (el && this.mounted) loadGallery(el);
    },
  };

  // ---------------------------------------------------------------- 参数
  function params(el) {
    const v = (id) => el.querySelector(id).value;
    const seg = (id) => el.querySelector(`#${id} button.active`)?.dataset.v;
    return {
      layout: seg("cg-layout") || DEFAULTS.layout,
      scale_mode: seg("cg-scale") || DEFAULTS.scale_mode,
      fit: seg("cg-fit") || DEFAULTS.fit,
      cross_align: seg("cg-align") || DEFAULTS.cross_align,
      cols: Number(v("#cg-cols")),
      cell_ratio: Number(v("#cg-ratio")),
      gap: Number(v("#cg-gap")),
      padding: Number(v("#cg-padding")),
      background: v("#cg-bg"),
    };
  }

  function bindControls(el) {
    // 分段按钮
    el.querySelectorAll(".seg").forEach((seg) => {
      const defaultV = {
        "cg-layout": DEFAULTS.layout, "cg-scale": DEFAULTS.scale_mode,
        "cg-fit": DEFAULTS.fit, "cg-align": DEFAULTS.cross_align,
      }[seg.id];
      seg.querySelectorAll("button").forEach((b) => {
        if (b.dataset.v === defaultV) b.classList.add("active");
        b.onclick = () => {
          seg.querySelectorAll("button").forEach((x) => x.classList.remove("active"));
          b.classList.add("active");
          updateControlVisibility(el);
          schedulePreview(el);
        };
      });
    });

    const ranges = [["#cg-cols", "#cg-cols-val", (x) => x],
                    ["#cg-ratio", "#cg-ratio-val", (x) => (Number(x) ? Number(x).toFixed(2) : "自动")],
                    ["#cg-gap", "#cg-gap-val", (x) => x],
                    ["#cg-padding", "#cg-padding-val", (x) => x]];
    ranges.forEach(([inp, out, fmt]) => {
      el.querySelector(inp).addEventListener("input", (e) => {
        el.querySelector(out).textContent = fmt(e.target.value);
        schedulePreview(el);
      });
    });
    el.querySelector("#cg-bg").addEventListener("input", () => schedulePreview(el));
    el.querySelector("#cg-bg-white").onclick = () => { el.querySelector("#cg-bg").value = "#ffffff"; schedulePreview(el); };
    el.querySelector("#cg-bg-black").onclick = () => { el.querySelector("#cg-bg").value = "#000000"; schedulePreview(el); };
    el.querySelectorAll("[data-bg]").forEach((b) => {
      b.onclick = () => { el.querySelector("#cg-bg").value = b.dataset.bg; schedulePreview(el); };
    });

    el.querySelector("#cg-clear").onclick = () => {
      order = [];
      renderOrder(el);
      markGallery(el);
      schedulePreview(el);
    };

    el.querySelector("#cg-save").onclick = () => doSave(el);
  }

  /* 根据拼接方式/策略，显示或隐藏不适用的参数项。 */
  function updateControlVisibility(el) {
    const p = params(el);
    const show = (id, on) => el.querySelector(id).style.display = on ? "" : "none";
    show("#cg-cols-field", p.layout === "grid");
    show("#cg-ratio-field", p.layout === "grid" && p.scale_mode === "uniform");
    show("#cg-align-field", p.layout !== "grid");
    // 长图槽位本身按图片比例生成，cover/contain 无差异，仅网格需要该选项
    show("#cg-fit-field", p.layout === "grid");
    const hints = {
      "horizontal+uniform": "所有图片统一高度，等比缩放后横向排列",
      "horizontal+original": "保持各自尺寸横向排列，按交叉轴对齐",
      "vertical+uniform": "所有图片统一宽度，适合手机长截图 / 长图",
      "vertical+original": "保持各自尺寸纵向排列，按交叉轴对齐",
      "grid+uniform": "固定列数与单元格比例，整齐划一（封面墙）",
      "grid+original": "相册式排版：行高一致、按比例凑满整行，横竖混排不变形",
    };
    el.querySelector("#cg-scale-hint").textContent = hints[`${p.layout}+${p.scale_mode}`] || "";
  }

  // ---------------------------------------------------------------- 图库
  async function loadGallery(el) {
    imagesCache = await C.fetchImages();
    const box = el.querySelector("#cg-gallery");
    box.innerHTML = C.galleryHTML(imagesCache);
    box.querySelectorAll(".card").forEach((card) => {
      card.onclick = () => {
        const id = card.dataset.id;
        const pos = order.findIndex((r) => r.id === id);
        if (pos >= 0) order.splice(pos, 1);
        else {
          const rec = imagesCache.find((r) => r.id === id);
          if (rec && order.length < 30) order.push(rec);
          else if (order.length >= 30) C.toast("最多选择 30 张", "error");
        }
        renderOrder(el);
        markGallery(el);
        schedulePreview(el);
      };
    });
    // 清理已被删除的选中项
    const ids = new Set(imagesCache.map((r) => r.id));
    const before = order.length;
    order = order.filter((r) => ids.has(r.id));
    if (before !== order.length) renderOrder(el);
    markGallery(el);
    schedulePreview(el);
  }

  function markGallery(el) {
    const chosen = new Set(order.map((r) => r.id));
    el.querySelector("#cg-count").textContent = `已选 ${order.length} 张`;
    el.querySelectorAll("#cg-gallery .card").forEach((c) =>
      c.classList.toggle("selected", chosen.has(c.dataset.id)));
  }

  // ---------------------------------------------------------------- 排序列表
  function renderOrder(el) {
    const box = el.querySelector("#cg-order");
    if (!order.length) {
      box.innerHTML = `<div class="dim" style="font-size:12px;padding:6px 0">尚未选择图片</div>`;
      return;
    }
    box.innerHTML = order.map((r, i) => `
      <div class="order-item" data-i="${i}" draggable="true">
        <span class="order-no">${i + 1}</span>
        <img src="${C.esc(r.thumbnail_url)}" draggable="false">
        <span class="order-name" title="${C.esc(r.filename)}">${C.esc(r.filename)}</span>
        <span class="order-dim">${r.width}×${r.height}</span>
        <span class="order-btns">
          <button type="button" data-act="up" ${i === 0 ? "disabled" : ""}>↑</button>
          <button type="button" data-act="down" ${i === order.length - 1 ? "disabled" : ""}>↓</button>
          <button type="button" data-act="del" class="order-del">×</button>
        </span>
      </div>`).join("");

    box.querySelectorAll(".order-item").forEach((row) => {
      const i = Number(row.dataset.i);
      row.querySelectorAll("button").forEach((b) => {
        b.onclick = () => {
          const act = b.dataset.act;
          if (act === "del") order.splice(i, 1);
          else if (act === "up" && i > 0) [order[i - 1], order[i]] = [order[i], order[i - 1]];
          else if (act === "down" && i < order.length - 1) [order[i + 1], order[i]] = [order[i], order[i + 1]];
          renderOrder(el); schedulePreview(el); markGallery(el);
        };
      });
      row.addEventListener("dragstart", (e) => {
        e.dataTransfer.setData("text/plain", String(i));
        row.classList.add("dragging");
      });
      row.addEventListener("dragend", () => row.classList.remove("dragging"));
      row.addEventListener("dragover", (e) => e.preventDefault());
      row.addEventListener("drop", (e) => {
        e.preventDefault();
        const from = Number(e.dataTransfer.getData("text/plain"));
        if (Number.isNaN(from) || from === i) return;
        const [moved] = order.splice(from, 1);
        order.splice(i, 0, moved);
        renderOrder(el); schedulePreview(el); markGallery(el);
      });
    });
  }

  // ---------------------------------------------------------------- 预览
  function schedulePreview(el) {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(() => renderPreview(el), 160);
  }

  async function renderPreview(el) {
    const stage = el.querySelector("#cg-stage");
    const failBox = el.querySelector("#cg-failed");
    if (!order.length) {
      lastPlan = null;
      stage.innerHTML = `<div class="empty"><span class="big">🧷</span>从左侧选择至少 1 张图片开始拼接</div>`;
      el.querySelector("#cg-plan-dim").textContent = "";
      failBox.innerHTML = "";
      return;
    }
    stage.innerHTML = `<div class="loading">计算布局…</div>`;
    let d;
    try {
      d = await Api.post("/api/collage/preview", {
        ...params(el),
        images: order.map((r) => ({ id: r.id, filename: r.filename })),
      });
    } catch (e) {
      stage.innerHTML = `<div class="empty">预览失败：${C.esc(e.message)}</div>`;
      return;
    }
    lastPlan = d.plan;
    drawPreview(el, d.plan);
    el.querySelector("#cg-plan-dim").textContent = `${d.plan.width} × ${d.plan.height} px · 成功 ${d.succeeded}/${d.requested}`;
    renderFailed(el, d.failed);
  }

  /* 用绝对定位 + <img> 还原后端布局；缩放适配预览区，不改变真实比例。 */
  function drawPreview(el, plan) {
    const stage = el.querySelector("#cg-stage");
    const maxW = Math.max(260, stage.clientWidth - 24);
    const maxH = 460;
    const scale = Math.min(maxW / plan.width, maxH / plan.height, 1);
    const W = Math.round(plan.width * scale), H = Math.round(plan.height * scale);
    const byId = new Map(order.map((r) => [r.id, r]));

    stage.innerHTML = `
      <div class="collage-canvas" style="width:${W}px;height:${H}px;background:${plan.background}">
        ${plan.items.map((it) => {
          const rec = byId.get(it.id) || imagesCache.find((r) => r.id === it.id);
          if (!rec) return "";
          const fit = plan.fit === "cover" ? "cover" : "contain";
          return `<img src="${C.esc(rec.thumbnail_url)}" loading="lazy" alt=""
            style="position:absolute;left:${it.x * scale}px;top:${it.y * scale}px;
            width:${it.w * scale}px;height:${it.h * scale}px;object-fit:${fit};display:block">`;
        }).join("")}
      </div>`;
  }

  function renderFailed(el, failed) {
    const box = el.querySelector("#cg-failed");
    if (!failed || !failed.length) { box.innerHTML = ""; return; }
    box.innerHTML = `
      <div class="collage-failed-title">⚠ ${failed.length} 张图片无法处理，其余图片仍会正常拼接：</div>` +
      failed.map((f) => `<div class="collage-failed-item">· 第 ${f.index + 1} 张
        ${f.filename ? `「${C.esc(f.filename)}」` : ""}：${C.esc(f.reason)}</div>`).join("");
  }

  // ---------------------------------------------------------------- 保存
  async function doSave(el) {
    if (!order.length) { C.toast("请先选择图片", "error"); return; }
    const btn = el.querySelector("#cg-save");
    const status = el.querySelector("#cg-status");
    btn.disabled = true;
    status.textContent = "正在拼接渲染…";
    try {
      const d = await Api.post("/api/collage", {
        ...params(el), save: true,
        images: order.map((r) => ({ id: r.id, filename: r.filename })),
      });
      resultUrl = d.file_url;
      const dl = el.querySelector("#cg-download");
      dl.href = `${d.file_url}?t=${Date.now()}`;
      dl.style.display = "";
      status.textContent = `完成：${d.width}×${d.height}`;
      renderFailed(el, d.failed);
      const saved = d.saved_image;
      el.querySelector("#cg-result").innerHTML = `
        <div class="panel">
          <div class="panel-title">拼接结果${saved ? '<span class="dim">已保存到图像库</span>' : ""}</div>
          <div class="stage"><img src="${d.file_url}?t=${Date.now()}"></div>
          ${d.failed.length ? `<div class="collage-failed" style="margin-top:10px"></div>` : ""}
          <div class="keypoint-stats" style="margin-top:8px">
            成品尺寸 <strong>${d.width} × ${d.height}</strong> · 成功 <strong>${d.succeeded}</strong> 张
            ${d.failed.length ? `· 失败 <strong style="color:var(--amber)">${d.failed.length}</strong> 张` : ""}
            ${saved ? `· 新图：${C.esc(saved.filename)}` : ""}
          </div>
        </div>`;
      if (d.failed.length) {
        el.querySelector("#cg-result .collage-failed").innerHTML =
          d.failed.map((f) => `<div class="collage-failed-item">· 第 ${f.index + 1} 张
            ${f.filename ? `「${C.esc(f.filename)}」` : ""}：${C.esc(f.reason)}</div>`).join("");
      }
      if (saved) await C.refreshImages();
      C.toast(d.failed.length
        ? `已完成（${d.succeeded} 成功 / ${d.failed.length} 失败）并保存`
        : "拼接完成，已保存为新图", d.failed.length ? "" : "success");
    } catch (e) {
      status.textContent = "";
      C.toast("拼接失败：" + e.message, "error");
    } finally {
      btn.disabled = false;
    }
  }
})();
