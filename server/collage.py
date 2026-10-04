"""多图拼接（长图 / 拼贴画）核心算法。

设计目标（对应需求）：
- 三种布局：horizontal（横向长图）/ vertical（纵向长图）/ grid（网格拼贴）。
- 两种缩放策略：
  scale_mode="uniform" 统一缩放到公共基准（横/纵向统一对边高度/宽度，
    网格统一单元格宽高比），整体整齐；
  scale_mode="original" 保留每张图原始比例：横/纵向按自然尺寸排列，
    网格采用「行高自适应」的相册式（justified）排布，横竖混排也不会
    被拉伸或留下大片空白。
- fit 决定比例不一致时如何处理：
  fit="contain" 完整保留、补背景色（绝不裁剪、绝不拉伸）；
  fit="cover"   填满槽位、居中裁切。
- 间距 gap、外边距 padding（默认等于 gap）、背景色均可配置。
- 横竖混排、尺寸悬殊：任何排布槽位都有明确尺寸，每图按比例缩放，
  统一缩放模式保证没有大小不一；网格原比例模式用相册式排版消化差异。
- 大图保护：单源最长边压到 SOURCE_MAX_DIM，成品最长边压到 CANVAS_MAX_DIM，
  整体等比缩小，不会因拼接数十张大图而耗尽内存。

输出：
- plan_layout(sources, params) 只算布局（预览接口用，纯 dict/整数坐标）；
- render(sources, params, image_loader) 载入并渲染成 PIL.Image，
  单张图载入失败不影响其余图，失败项单独收集返回。
"""
import math

from PIL import Image, ImageOps

# ---------------------------------------------------------------------------
# 限制
# ---------------------------------------------------------------------------
SOURCE_MAX_DIM = 2000          # 参与拼接的单张源图最长边上限（超出等比降采样）
CANVAS_MAX_DIM = 5000          # 成品长/宽最长边上限（超出整体等比缩小）
MIN_IMAGES = 1
MAX_IMAGES = 30
MAX_GAP = 200
MAX_COLS = 10
JUSTIFIED_ROW_TOL = 0.25       # 相册式排版行高搜索的容差（±25%）


class CollageError(ValueError):
    """参数级错误（请求不合法，返回 400）。"""


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------
def parse_color(value, default=(255, 255, 255)):
    """把 #rgb / #rrggbb / (r,g,b) 解析成 (r, g, b)。非法值回退默认色。"""
    if isinstance(value, (tuple, list)) and len(value) == 3:
        try:
            return tuple(max(0, min(255, int(v))) for v in value)
        except (TypeError, ValueError):
            return tuple(default)
    if not isinstance(value, str):
        return tuple(default)
    s = value.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) == 6:
        try:
            return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            pass
    return tuple(default)


def normalize_params(data):
    """校验并规范化前端传入的参数。非法值抛 CollageError。"""
    if not isinstance(data, dict):
        raise CollageError("参数必须是 JSON 对象")

    layout = str(data.get("layout", "vertical"))
    if layout not in ("horizontal", "vertical", "grid"):
        raise CollageError("layout 只能是 horizontal / vertical / grid")

    scale_mode = str(data.get("scale_mode", "uniform"))
    if scale_mode not in ("uniform", "original"):
        raise CollageError("scale_mode 只能是 uniform / original")

    fit = str(data.get("fit", "contain"))
    if fit not in ("contain", "cover"):
        raise CollageError("fit 只能是 contain / cover")

    def _int(key, default, lo, hi):
        try:
            v = int(data.get(key, default))
        except (TypeError, ValueError):
            raise CollageError(f"{key} 必须是整数")
        if not (lo <= v <= hi):
            raise CollageError(f"{key} 必须在 {lo}~{hi} 之间")
        return v

    gap = _int("gap", 8, 0, MAX_GAP)
    cols = _int("cols", 3, 1, MAX_COLS)

    # cell_ratio：网格统一模式下的单元格宽/高比。0 表示按图片比例中位数自动取值。
    try:
        cell_ratio = float(data.get("cell_ratio", 0) or 0)
    except (TypeError, ValueError):
        raise CollageError("cell_ratio 必须是数字")
    if cell_ratio < 0 or cell_ratio > 10:
        raise CollageError("cell_ratio 必须在 0~10 之间")

    # 统一网格的基准单元格宽度（像素），只影响原图像素密度；成品另有最长边约束
    base_cell = _int("base_cell", 420, 80, 1200)
    # 相册式排版（网格原比例）的基准行高
    base_row = _int("base_row", 360, 80, 1500)

    padding = data.get("padding", None)
    padding = gap if padding is None else _int("padding", gap, 0, MAX_GAP)

    cross_align = str(data.get("cross_align", "start"))
    if cross_align not in ("start", "center", "end"):
        raise CollageError("cross_align 只能是 start / center / end")

    bg = parse_color(data.get("background", "#ffffff"))

    return {
        "layout": layout,
        "scale_mode": scale_mode,
        "fit": fit,
        "gap": gap,
        "padding": padding,
        "cols": cols,
        "cell_ratio": cell_ratio,
        "base_cell": base_cell,
        "base_row": base_row,
        "cross_align": cross_align,
        "background": bg,
    }


# ---------------------------------------------------------------------------
# 源载入
# ---------------------------------------------------------------------------
def source_work_size(w, h):
    """源图参与排版时的工作尺寸（最长边降采样，比例不变）。"""
    longest = max(w, h)
    if longest > SOURCE_MAX_DIM:
        scale = SOURCE_MAX_DIM / float(longest)
        return max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    return w, h


def prepare_source(img):
    """打开的 PIL 图 -> 规整的 RGB 工作副本（EXIF 转置 + 最长边降采样）。"""
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")
    w, h = source_work_size(*img.size)
    if (w, h) != img.size:
        img = img.resize((w, h), Image.Resampling.LANCZOS)
    return img


# ---------------------------------------------------------------------------
# 槽位内适配（contain / cover），返回 (绘制图, 槽内偏移)
# ---------------------------------------------------------------------------
def fit_into(img, box_w, box_h, mode):
    """把 img 等比放进 box_w × box_h 的槽位。

    contain: 完整可见，不足部分留背景（调用方画背景），图居中；
    cover:   等比放大填满，超出部分由调用方按 crop 居中裁剪。
    返回 (绘制用 RGB 图, 槽内 x 偏移, 槽内 y 偏移, 裁剪盒或 None)。
    """
    w, h = img.size
    box_w = max(1, int(round(box_w)))
    box_h = max(1, int(round(box_h)))
    if mode == "cover":
        scale = max(box_w / float(w), box_h / float(h))
        nw = max(box_w, int(round(w * scale)))
        nh = max(box_h, int(round(h * scale)))
        resized = img.resize((nw, nh), Image.Resampling.LANCZOS)
        cx = (nw - box_w) // 2
        cy = (nh - box_h) // 2
        return resized, 0, 0, (cx, cy, cx + box_w, cy + box_h)

    # contain：等比缩放到「恰好放进」槽位（允许放大，不留可避免的空白）
    scale = min(box_w / float(w), box_h / float(h))
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    resized = img.resize((nw, nh), Image.Resampling.LANCZOS)
    return resized, (box_w - nw) // 2, (box_h - nh) // 2, None


def _cross_offset(slot_cross, item_cross, align):
    """交叉轴对齐：start 靠上/靠左，center 居中，end 靠下/靠右。"""
    if align == "center":
        return (slot_cross - item_cross) // 2
    if align == "end":
        return slot_cross - item_cross
    return 0


# ---------------------------------------------------------------------------
# 布局计算（不含图像像素，只依赖每张图的宽高）
# sources: [{"id", "index", "width", "height"}, ...]
# 返回 {"width","height","items":[{"index","x","y","w","h", ...}]}
# 坐标为槽位左上角与槽位尺寸；contain 时图可能小于槽位（居中）。
# ---------------------------------------------------------------------------
def plan_layout(sources, params):
    p = normalize_params(params) if not params.get("_normalized") else params
    if not sources:
        raise CollageError("至少需要 1 张图片")
    gap, pad = p["gap"], p["padding"]
    layout = p["layout"]

    if layout == "horizontal":
        items, canvas_w, canvas_h = _plan_strip(sources, p, horizontal=True)
    elif layout == "vertical":
        items, canvas_w, canvas_h = _plan_strip(sources, p, horizontal=False)
    else:
        items, canvas_w, canvas_h = _plan_grid(sources, p)

    # 外边距
    for it in items:
        it["x"] += pad
        it["y"] += pad
    canvas_w += 2 * pad
    canvas_h += 2 * pad

    # 成品最长边保护：整体等比缩小（坐标与槽位同步缩放）
    longest = max(canvas_w, canvas_h)
    out_scale = 1.0
    if longest > CANVAS_MAX_DIM:
        out_scale = CANVAS_MAX_DIM / float(longest)
        canvas_w = max(1, int(round(canvas_w * out_scale)))
        canvas_h = max(1, int(round(canvas_h * out_scale)))
        for it in items:
            it["x"] = int(round(it["x"] * out_scale))
            it["y"] = int(round(it["y"] * out_scale))
            it["w"] = max(1, int(round(it["w"] * out_scale)))
            it["h"] = max(1, int(round(it["h"] * out_scale)))

    return {
        "width": canvas_w,
        "height": canvas_h,
        "gap": gap,
        "background": "#%02x%02x%02x" % p["background"],
        "fit": p["fit"],
        "items": items,
        "out_scale": round(out_scale, 6),
    }


def _plan_strip(sources, p, horizontal):
    """横/纵向长条布局。

    uniform：所有图统一对边尺寸（横向统一高度、纵向统一宽度），
             取各图缩放到基准后对边的中位数，避免被极端图带偏；
    original：每图保持自然尺寸（源已被 SOURCE_MAX_DIM 限制）。
    """
    gap = p["gap"]
    natural = [(max(1, s["width"]), max(1, s["height"])) for s in sources]

    if p["scale_mode"] == "uniform":
        # 统一后的对边尺寸：用自然对边的中位数（与排布方向垂直的那条边）
        cross_sizes = sorted((h if horizontal else w) for w, h in natural)
        target_cross = cross_sizes[len(cross_sizes) // 2]
    else:
        target_cross = None

    slots = []  # (主轴尺寸, 交叉轴尺寸)：横向=(宽,高)，纵向=(高,宽)
    for w, h in natural:
        if target_cross is not None:
            if horizontal:
                new_h = target_cross
                new_w = max(1, int(round(w * (target_cross / float(h)))))
                slots.append((new_w, new_h))
            else:
                new_w = target_cross
                new_h = max(1, int(round(h * (target_cross / float(w)))))
                slots.append((new_h, new_w))
        elif horizontal:
            slots.append((w, h))
        else:
            slots.append((h, w))

    if horizontal:
        strip_cross = max(c for _, c in slots)
        total_main = sum(m for m, _ in slots) + gap * (len(slots) - 1)
        canvas_w, canvas_h = total_main, strip_cross
    else:
        strip_cross = max(c for _, c in slots)
        total_main = sum(m for m, _ in slots) + gap * (len(slots) - 1)
        canvas_w, canvas_h = strip_cross, total_main

    items = []
    main_cursor = 0
    for src, (main_s, cross_s) in zip(sources, slots):
        off = _cross_offset(strip_cross, cross_s, p["cross_align"])
        if horizontal:
            x, y = main_cursor, off
            slot_w, slot_h = main_s, strip_cross
            img_w, img_h = main_s, cross_s
        else:
            x, y = off, main_cursor
            slot_w, slot_h = strip_cross, main_s
            img_w, img_h = cross_s, main_s
        items.append({
            "index": src["index"], "id": src.get("id"),
            "x": x, "y": y, "w": slot_w, "h": slot_h,
            "img_w": img_w, "img_h": img_h,
        })
        main_cursor += main_s + gap
    return items, canvas_w, canvas_h


def _plan_grid(sources, p):
    """网格布局。

    uniform：固定列数 + 固定单元格宽高比，逐行摆放，整齐划一；
    original：相册式（justified）排版，固定行高、按比例缩放并凑满整行宽，
              最后一行左对齐、保持自然高度——横竖混排也无拉伸、无大空白。
    """
    if p["scale_mode"] == "original":
        return _plan_justified(sources, p)

    cols = max(1, min(p["cols"], len(sources)))
    gap = p["gap"]
    cell = p["base_cell"]

    # 单元格宽高比：显式给定优先，否则取源图比例中位数（横图多则横格）
    ratio = p["cell_ratio"]
    if ratio <= 0:
        ratios = sorted(max(s["width"], 1) / float(max(s["height"], 1)) for s in sources)
        ratio = ratios[len(ratios) // 2]
    cell_w = cell
    cell_h = max(1, int(round(cell / ratio)))

    rows = math.ceil(len(sources) / cols)
    canvas_w = cols * cell_w + (cols - 1) * gap
    canvas_h = rows * cell_h + (rows - 1) * gap

    items = []
    for i, src in enumerate(sources):
        r, c = divmod(i, cols)
        items.append({
            "index": src["index"], "id": src.get("id"),
            "x": c * (cell_w + gap), "y": r * (cell_h + gap),
            "w": cell_w, "h": cell_h, "img_w": cell_w, "img_h": cell_h,
        })
    return items, canvas_w, canvas_h


def _plan_justified(sources, p):
    """相册式排版：每行高度尽量一致，行内图片等比缩放后恰好凑满行宽。"""
    cols = max(1, min(p["cols"], len(sources)))
    gap = p["gap"]
    target_h = p["base_row"]

    # 行宽：按 cols 个「目标宽度 = 目标行高 * 平均比例」估算
    avg_ratio = sum(max(s["width"], 1) / float(max(s["height"], 1)) for s in sources) / len(sources)
    row_w = max(1, int(round(cols * target_h * max(avg_ratio, 0.7))))

    # 贪心分行：不断把图加入当前行，行高（凑满 row_w 时）下降；
    # 一旦再加入会让行高低于容差下限，就封行。
    lines = []
    cur = []

    def row_height(idxs):
        n = len(idxs)
        sum_ratio = sum(sources[i]["width"] / float(sources[i]["height"]) for i in idxs)
        avail = row_w - gap * (n - 1)
        return avail / sum_ratio if sum_ratio > 0 else float(target_h)

    for i in range(len(sources)):
        trial = cur + [i]
        h_trial = row_height(trial)
        if cur and h_trial < target_h * (1 - JUSTIFIED_ROW_TOL):
            lines.append(cur)
            cur = [i]
        else:
            cur = trial
            if len(cur) >= cols and h_trial <= target_h * (1 + JUSTIFIED_ROW_TOL):
                lines.append(cur)
                cur = []
    if cur:
        lines.append(cur)

    items = []
    y = 0
    for li, idxs in enumerate(lines):
        is_last = li == len(lines) - 1 and len(lines) > 1
        # 最后一行不强行凑宽：行高封顶为目标行高，左对齐
        h = row_height(idxs)
        if is_last:
            h = min(float(target_h), h)
        h = max(1, int(round(h)))
        x = 0
        for i in idxs:
            src = sources[i]
            r = src["width"] / float(src["height"])
            w = max(1, int(round(h * r)))
            items.append({
                "index": src["index"], "id": src.get("id"),
                "x": x, "y": y, "w": w, "h": h,
                "img_w": w, "img_h": h,
            })
            x += w + gap
        y += h + gap

    canvas_w = max((it["x"] + it["w"] for it in items), default=0)
    canvas_h = y - gap
    # 非末行理论上凑满 row_w；以实际最大宽为准（消除取整误差）
    return items, max(canvas_w, row_w), canvas_h


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
def render(ordered, params, image_loader):
    """载入图片并渲染拼图。

    ordered: 按目标顺序排列的 [{"id": ...}, ...]（id 由调用方解释）；
    image_loader: callable(id) -> PIL.Image 或抛异常；
    返回 (canvas, plan, failures)：
      canvas   —— PIL.Image（RGB）；
      plan     —— plan_layout 的完整结果（仅含成功项）；
      failures —— [{"id","index","reason"}]，单张失败不影响其余。
    """
    p = normalize_params(params) if not params.get("_normalized") else params

    sources, failures = [], []
    loaded = {}  # index -> RGB PIL image
    for idx, entry in enumerate(ordered):
        image_id = entry.get("id")
        try:
            img = image_loader(image_id)
            if img is None:
                raise ValueError("图像不存在或无法读取")
            img = prepare_source(img)
        except Exception as exc:  # 单张失败隔离
            failures.append({"id": image_id, "index": idx,
                             "filename": entry.get("filename", ""),
                             "reason": str(exc) or exc.__class__.__name__})
            continue
        loaded[idx] = img
        sources.append({"id": image_id, "index": idx,
                        "width": img.size[0], "height": img.size[1]})

    if not sources:
        return None, None, failures

    plan = plan_layout(sources, {**p, "_normalized": True})
    canvas = Image.new("RGB", (plan["width"], plan["height"]), p["background"])

    for it in plan["items"]:
        idx = it["index"]
        img = loaded[idx]
        piece, ox, oy, crop = fit_into(img, it["w"], it["h"], p["fit"])
        if p["fit"] == "cover":
            piece = piece.crop(crop)
            canvas.paste(piece, (it["x"], it["y"]))
        else:
            # contain：先把槽位补背景（防透明残留），图按偏移贴入
            slot = Image.new("RGB", (it["w"], it["h"]), p["background"])
            slot.paste(piece, (ox, oy))
            canvas.paste(slot, (it["x"], it["y"]))

    return canvas, plan, failures
