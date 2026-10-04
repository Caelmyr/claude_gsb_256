"""拼图引擎：把多张图按横向 / 纵向 / 网格拼成一张长图或拼贴画。

需求对应关系：
- 三种布局（mode）：
  * horizontal 横向长图：从左到右排成一行；
  * vertical   纵向长图：从上到下排成一列；
  * grid       网格拼贴：按列数 row-major 排列。
- 两种缩放策略（scaling）：
  * uniform  统一缩放（默认）——横排同高、竖排同宽；网格采用「行内两端对齐」：
    同一行的图共享行高、整行宽度精确铺满画布。横竖混排、宽高比差异很大时
    不会出现可见拉伸（每图宽高比误差 <1px），也不会在图间留大片空白。
  * original 原比例保留——各图保持原始相对尺寸（只缩小不放大），横竖混排时
    用最大边做轨道（横排按最高、竖排按最宽、网格按表格单元格），由 align
    决定在轨道内顶/中/底或左/中/右对齐，空出的区域只露出背景色。
- gap 间距、background 背景色、align 对齐均可配置。
- 输出最长边受 max_dim 限制，整体等比降采样（间距同比例缩放）。

布局在连续坐标计算后统一量化为整数像素；沿主轴相邻图共享边界，无缝不重叠。
render() 只接收已成功打开的 PIL 图像，单张失败的隔离由 API 层负责。
"""
import math
from statistics import median

from PIL import Image, ImageOps

MODES = ("horizontal", "vertical", "grid")
SCALINGS = ("uniform", "original")
ALIGNS = ("start", "center", "end")

NAMED_COLORS = {
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "gray": (240, 240, 240),
    "red": (242, 95, 92),
}


def parse_color(value, default=(255, 255, 255)):
    """把 #rgb / #rrggbb / 常见颜色名解析成 (r,g,b)，无法识别时回退默认。"""
    if not value:
        return default
    v = str(value).strip().lower().lstrip("#")
    if v in NAMED_COLORS:
        return NAMED_COLORS[v]
    try:
        if len(v) == 3:
            return tuple(int(c * 2, 16) for c in v)  # type: ignore[return-value]
        if len(v) == 6:
            return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
    except ValueError:
        pass
    return default


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _distribute(weights, target):
    """把整数 target 按权重拆成 len(weights) 个 >=1 整数，和恰好为 target。

    余数逐像素发给「调整后离理想值最近」的项，每项沿自身轴的偏差 <1px。
    """
    n = len(weights)
    target = max(int(target), n)
    total = sum(weights) or 1e-9
    ideal = [target * w / total for w in weights]
    out = [max(1, int(math.floor(x))) for x in ideal]
    step = 1 if target > sum(out) else -1
    for _ in range(n * (target + n) + 1):
        if sum(out) == target:
            break
        idx = min(range(n),
                  key=lambda i: abs(out[i] + step - ideal[i])
                  if out[i] + step >= 1 else float("inf"))
        out[idx] += step
    return out


def _offset(extent, item, align):
    """交叉轴起始偏移：start 贴头 / center 居中 / end 贴尾。"""
    slack = extent - item
    if align == "end":
        return max(0, slack)
    if align == "center":
        return max(0, slack // 2)
    return 0


def _contain(w, h, max_w, max_h, allow_grow=False):
    """等比放进整数包围盒 (max_w,max_h) 的最大整数盒，绝不越界。

    allow_grow=False 时同时保证不放大。宽锚 / 高锚两个候选取失真最小者，
    最短轴等效形变 <1px。
    """
    r = min(max_w / float(w), max_h / float(h))
    if not allow_grow:
        r = min(1.0, r)
    cands = []
    aw = max(1, int(round(w * r)))
    ah = max(1, int(round(h / float(w) * aw)))
    if aw <= max_w and ah <= max_h:
        cands.append((aw, ah))
    bh = max(1, int(round(h * r)))
    bw = max(1, int(round(w / float(h) * bh)))
    if bw <= max_w and bh <= max_h:
        cands.append((bw, bh))
    if not cands:
        return 1, 1
    return min(cands, key=lambda b: abs(b[0] / w - b[1] / h) * min(b))


# ---------------------------------------------------------------------------
# 各模式整数布局：统一返回 (canvas_w, canvas_h, placements, cols, rows)
# placements = [(x, y, w, h), ...]
# ---------------------------------------------------------------------------
def _fit_uniform_line(ratios, anchor, total_target=None):
    """统一缩放的一行/一列：交叉轴锚定整数 anchor，沿主轴等比铺满。

    在 anchor±1 内搜索，使每图「宽=round(r·H)」独立取整后整行长度最接近
    total_target（默认即 sum(r·anchor)），残余差额逐像素摊给调整后宽高比
    误差最小的图。返回 (anchor, lengths)，单图最短轴等效形变 <1px。
    """
    n = len(ratios)
    target = total_target if total_target is not None else max(n, int(round(sum(ratios) * anchor)))
    # anchor±1 择优：消除锚定轴取整的亚像素误差被宽高比放大
    best = None
    for cand in range(max(1, anchor - 1), anchor + 2):
        lengths = [max(1, int(round(r * cand))) for r in ratios]
        diff = abs(sum(lengths) - target)
        worst = max(abs(l / cand - rr) * min(cand, l) for l, rr in zip(lengths, ratios))
        key = (diff, worst)
        if best is None or key < best[0]:
            best = (key, cand, lengths)
    anchor, lengths = best[1], list(best[2])
    diff = target - sum(lengths)
    step = 1 if diff > 0 else -1
    guard = 0
    while diff != 0 and guard < n * (target + n) + 1:
        idx = min(range(n),
                  key=lambda i: abs((lengths[i] + step) / anchor - ratios[i])
                  if lengths[i] + step >= 1 else float("inf"))
        lengths[idx] += step
        diff -= step
        guard += 1
    return anchor, lengths


def _place_horizontal(sizes, gap, align, scale, uniform):
    n, gi = len(sizes), max(0, int(round(gap * scale)))
    if uniform:
        anchor = max(1, int(round(median(h for _, h in sizes) * scale)))
        ratios = [w / float(h) for w, h in sizes]
        ch, widths = _fit_uniform_line(ratios, anchor)
        x, placements = 0, []
        for iw in widths:
            placements.append((x, 0, iw, ch))
            x += iw + gi
        return x - gi, ch, placements, n, 1

    # 原比例：每图沿主轴（宽）等比缩放、高度以宽为锚推导；轨道高取最高图
    ch = max(1, int(round(max(h for _, h in sizes) * scale)))
    x, placements = 0, []
    for w, h in sizes:
        bw = max(1, int(round(w * scale)))
        bh = max(1, int(round(h / float(w) * bw)))
        if bh > ch:  # 少数更高图改以轨道高为锚
            bh = ch
            bw = max(1, int(round(w / float(h) * bh)))
        placements.append((x, _offset(ch, bh, align), bw, bh))
        x += bw + gi
    return x - gi, ch, placements, n, 1


def _place_vertical(sizes, gap, align, scale, uniform):
    n, gi = len(sizes), max(0, int(round(gap * scale)))
    if uniform:
        anchor = max(1, int(round(median(w for w, _ in sizes) * scale)))
        ratios = [h / float(w) for w, h in sizes]
        cw, heights = _fit_uniform_line(ratios, anchor)
        y, placements = 0, []
        for ih in heights:
            placements.append((0, y, cw, ih))
            y += ih + gi
        return cw, y - gi, placements, 1, n

    # 原比例：沿主轴（高）等比、宽以高为锚；轨道宽取最宽图
    cw = max(1, int(round(max(w for w, _ in sizes) * scale)))
    y, placements = 0, []
    for w, h in sizes:
        bh = max(1, int(round(h * scale)))
        bw = max(1, int(round(w / float(h) * bh)))
        if bw > cw:  # 少数更宽图改以轨道宽为锚
            bw = cw
            bh = max(1, int(round(h / float(w) * bw)))
        placements.append((_offset(cw, bw, align), y, bw, bh))
        y += bh + gi
    return cw, y - gi, placements, 1, n


def _place_grid_uniform(sizes, gap, columns, align, scale):
    """统一缩放网格：行内两端对齐。完整行铺满画布宽；末行自然成块。"""
    n = len(sizes)
    k = max(1, min(n, columns or int(math.ceil(math.sqrt(n)))))
    gi = max(0, int(round(gap * scale)))
    rows = [sizes[i:i + k] for i in range(0, n, k)]
    ratios = [[w / float(h) for w, h in row] for row in rows]

    cw = max(k, int(round(k * median(w for w, _ in sizes) * scale)))
    content_w = max(k, cw - (k - 1) * gi)
    full_h = [content_w / (sum(rr) or 1e-9)
              for rr in (ratios[:-1] if len(rows) > 1 else [])]
    ref_h = max(1, int(round(
        median(full_h) if full_h else median(h for _, h in sizes) * scale)))

    y, placements = 0, []
    for ri, row_r in enumerate(ratios):
        last = (ri == len(rows) - 1) and len(rows) > 1
        only = len(rows) == 1
        if not (last or only):
            anchor = max(1, int(round(content_w / (sum(row_r) or 1e-9))))
            rh, widths = _fit_uniform_line(row_r, anchor, content_w)
            x = 0
        else:
            cap = (content_w - (len(row_r) - 1) * gi) / (sum(row_r) or 1e-9)
            rh = max(1, min(ref_h, int(math.floor(max(1.0, cap)))))
            widths = [max(1, int(round(r * rh))) for r in row_r]
            block = sum(widths) + (len(widths) - 1) * gi
            # 取整后块可能比画布宽 1px，夹回画布内避免越界
            if block > cw:
                widths = _distribute(row_r, max(len(row_r), cw - (len(row_r) - 1) * gi))
                block = sum(widths) + (len(widths) - 1) * gi
            x = _offset(cw, block, align)
        for iw in widths:
            placements.append((x, y, iw, rh))
            x += iw + gi
        y += rh + gi
    return cw, y - gi, placements, k, len(rows)


def _place_grid_original(sizes, gap, columns, align, scale):
    """原比例网格：表格单元格，每图 contain 进格（只缩小不放大），格内对齐。"""
    n = len(sizes)
    k = max(1, min(n, columns or int(math.ceil(math.sqrt(n)))))
    rn = int(math.ceil(n / k))
    gi = max(0, int(round(gap * scale)))

    own = [(max(1, int(round(w * scale))), max(1, int(round(h * scale))))
           for w, h in sizes]
    col_w = [max((own[r * k + c][0] if r * k + c < n else 0)
                 for r in range(rn)) for c in range(k)]
    row_h = [max((own[r * k + c][1] if r * k + c < n else 0)
                 for c in range(k)) for r in range(rn)]
    cw = sum(col_w) + (k - 1) * gi
    ch = sum(row_h) + (rn - 1) * gi

    placements = []
    y = 0
    for r in range(rn):
        x = 0
        for c in range(k):
            idx = r * k + c
            if idx < n:
                w0, h0 = sizes[idx]
                bw, bh = _contain(w0, h0, min(own[idx][0], col_w[c]),
                                  min(own[idx][1], row_h[r]))
                placements.append((x + _offset(col_w[c], bw, align),
                                   y + _offset(row_h[r], bh, align), bw, bh))
            x += col_w[c] + gi
        y += row_h[r] + gi
    return cw, ch, placements, k, rn


def _place(sizes, mode, scaling, gap, columns, align, scale):
    if mode == "horizontal":
        return _place_horizontal(sizes, gap, align, scale, scaling == "uniform")
    if mode == "vertical":
        return _place_vertical(sizes, gap, align, scale, scaling == "uniform")
    if scaling == "uniform":
        return _place_grid_uniform(sizes, gap, columns, align, scale)
    return _place_grid_original(sizes, gap, columns, align, scale)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def render(images, params, max_dim):
    """拼接图像。

    images: 已成功打开的 PIL.Image 列表（顺序即排列顺序）。
    params: mode / scaling / gap / align / background / columns。
    max_dim: 输出最长边上限制（整体等比降采样）。
    返回 (canvas: RGB Image, meta: dict)。
    """
    if not images:
        raise ValueError("没有可拼接的图像")
    mode = params.get("mode", "horizontal")
    scaling = params.get("scaling", "uniform")
    if mode not in MODES:
        raise ValueError(f"未知的拼接方式：{mode}")
    if scaling not in SCALINGS:
        raise ValueError(f"未知的缩放策略：{scaling}")
    gap = max(0, min(500, int(params.get("gap", 8))))
    align = params.get("align", "start")
    if align not in ALIGNS:
        align = "start"
    columns = int(params.get("columns") or 0)
    background = parse_color(params.get("background", "#ffffff"))
    max_dim = max(64, int(max_dim))

    opened = [ImageOps.exif_transpose(im) for im in images]
    sizes = [im.size for im in opened]

    # 二分搜索 (0,1] 上满足最长边上上限的最大 scale（整数布局近似单调）
    w0, h0, *_ = _place(sizes, mode, scaling, gap, columns, align, 1.0)
    scale = 1.0
    if max(w0, h0) > max_dim:
        lo, hi = 0.0, 1.0
        for _ in range(14):
            mid = (lo + hi) / 2
            mw, mh, *_ = _place(sizes, mode, scaling, gap, columns, align, mid)
            if max(mw, mh) <= max_dim:
                lo = mid
            else:
                hi = mid
        scale = lo

    cw, ch, placements, k_eff, rows_n = _place(
        sizes, mode, scaling, gap, columns, align, scale)

    canvas = Image.new("RGB", (cw, ch), background)
    for im, (x, y, dw, dh) in zip(opened, placements):
        resized = im.resize((dw, dh), Image.Resampling.LANCZOS)
        if resized.mode in ("RGBA", "LA", "PA"):
            resized = resized.convert("RGBA")
            canvas.paste(resized, (x, y), resized.getchannel("A"))
        else:
            canvas.paste(resized.convert("RGB"), (x, y))

    meta = {
        "mode": mode, "scaling": scaling, "gap": gap, "align": align,
        "background": "#%02x%02x%02x" % background,
        "columns": k_eff, "rows": rows_n,
        "width": cw, "height": ch, "count": len(opened), "scale": round(scale, 4),
        "boxes": [{"index": i, "x": x, "y": y, "w": dw, "h": dh}
                  for i, (x, y, dw, dh) in enumerate(placements)],
    }
    return canvas, meta
