"""拼图功能测试：引擎几何 + REST API（含单张失败隔离）。

运行：python tests/test_collage.py
不依赖测试框架，全部用断言；覆盖：
- 三种布局 × 两种缩放策略的基本尺寸与无缝拼接；
- 横竖混排 / 宽高比差异极大时不越界、不重叠、形变有界（肉眼不可见）；
- 原比例模式只缩小不放大、严格保留相对比例；
- 间距 / 背景色 / 对齐 / 行列数；
- 最长边限制（整体降采样）；
- API：预览与保存、坏图隔离（其余照常完成）、参数校验、结果可下载。
"""
import io
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

from server.algorithms import collage  # noqa: E402
from server import config  # noqa: E402


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _solid(w, h, color=(80, 120, 200)):
    return Image.new("RGB", (w, h), color)


def _abs_distortion(src, box):
    """最短轴等效形变（像素）：两轴缩放比之差 × 最短边长。"""
    w0, h0 = src
    bw, bh = box[2], box[3]
    return abs(bw / w0 - bh / h0) * min(bw, bh)


def _place(sizes, mode, scaling, gap=8, columns=0, align="start", scale=1.0):
    return collage._place(sizes, mode, scaling, gap, columns, align, scale)


# ---------------------------------------------------------------------------
# 引擎：基本布局
# ---------------------------------------------------------------------------
def test_horizontal_uniform():
    sizes = [(200, 400), (600, 300), (300, 600)]
    cw, ch, pl, cols, rows = _place(sizes, "horizontal", "uniform", gap=10)
    heights = {b[3] for b in pl}
    assert len(heights) == 1, "横排统一缩放必须同高"
    assert cols == 3 and rows == 1
    # 宽度 = 各图宽 + 2 个间距
    assert cw == sum(b[2] for b in pl) + 2 * 10
    # 无缝相邻
    for i in range(2):
        assert pl[i][0] + pl[i][2] + 10 == pl[i + 1][0]


def test_vertical_uniform():
    sizes = [(400, 200), (300, 600), (600, 300)]
    cw, ch, pl, cols, rows = _place(sizes, "vertical", "uniform", gap=10)
    widths = {b[2] for b in pl}
    assert len(widths) == 1, "竖排统一缩放必须同宽"
    assert cols == 1 and rows == 3
    assert ch == sum(b[3] for b in pl) + 2 * 10
    for i in range(2):
        assert pl[i][1] + pl[i][3] + 10 == pl[i + 1][1]


def test_grid_columns():
    sizes = [(300, 300)] * 7
    cw, ch, pl, cols, rows = _place(sizes, "grid", "uniform", columns=3, gap=0)
    assert cols == 3 and rows == 3, f"7 张 3 列应为 3x3，实际 {cols}x{rows}"
    # 完整行铺满，三张同宽，无重叠
    assert pl[0][2] == pl[1][2] == pl[2][2]
    assert pl[0][2] + pl[1][2] + pl[2][2] == cw


def test_gap_zero_no_holes():
    sizes = [(400, 300), (200, 600), (500, 250), (300, 300)]
    cw, ch, pl, *_ = _place(sizes, "grid", "uniform", columns=2, gap=0)
    # 2x2 无间距时，四个角点应严丝合缝
    assert pl[0][0] + pl[0][2] == pl[1][0]
    assert pl[0][1] + pl[0][3] == pl[2][1]
    assert cw == pl[0][2] + pl[1][2]


# ---------------------------------------------------------------------------
# 引擎：横竖混排 / 极端宽高比
# ---------------------------------------------------------------------------
def test_mixed_aspect_bounds():
    random.seed(42)
    for _ in range(300):
        sizes = [(random.randint(60, 1400), random.randint(60, 1400))
                 for _ in range(random.randint(2, 10))]
        for mode in ("horizontal", "vertical", "grid"):
            cw, ch, pl, *_ = collage._place(
                sizes, mode, "uniform", random.randint(0, 30),
                random.choice([0, 2, 3, 4]), "center", 1.0)
            for b in pl:
                assert b[0] >= 0 and b[1] >= 0
                assert b[0] + b[2] <= cw and b[1] + b[3] <= ch, "盒越界"
                assert b[2] >= 1 and b[3] >= 1


def test_no_overlap():
    random.seed(7)
    for _ in range(500):
        n = random.randint(2, 9)
        sizes = [(random.randint(40, 900), random.randint(40, 900)) for _ in range(n)]
        cw, ch, pl, *_ = collage._place(
            sizes, "grid", "uniform", random.randint(0, 8),
            random.choice([2, 3, 4]), "start", 1.0)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = pl[i], pl[j]
                assert (a[0] >= b[0] + b[2] or b[0] >= a[0] + a[2]
                        or a[1] >= b[1] + b[3] or b[1] >= a[1] + a[3]), "图重叠"


def test_distortion_bounded_real_photos():
    """真实照片宽高比（0.5~2，少量全景）下，正常尺寸盒的等效形变 ≤2px。"""
    rng = random.Random(99)
    worst = 0.0
    for _ in range(2000):
        n = rng.randint(2, 9)
        sizes = []
        for _ in range(n):
            base = rng.randint(600, 1400)
            ar = rng.choice([rng.uniform(0.5, 2.0)] * 4 + [rng.uniform(0.34, 3.0)])
            if rng.random() < 0.5:
                sizes.append((base, max(200, int(base / ar))))
            else:
                sizes.append((max(200, int(base * ar)), base))
        mode = rng.choice(("horizontal", "vertical", "grid"))
        cw, ch, pl, *_ = collage._place(
            sizes, mode, "uniform", rng.randint(0, 24),
            rng.choice([0, 2, 3, 4]), rng.choice(("start", "center", "end")), 1.0)
        for src, b in zip(sizes, pl):
            if min(b[2], b[3]) >= 30:
                worst = max(worst, _abs_distortion(src, b))
    assert worst <= 2.1, f"出现可见形变：{worst}px"


# ---------------------------------------------------------------------------
# 原比例模式
# ---------------------------------------------------------------------------
def test_original_no_distortion_or_crop():
    """原比例：每张图严格等比（整数盒宽高比与原图一致，误差 <1px）。"""
    random.seed(5)
    for _ in range(1000):
        sizes = [(random.randint(60, 1200), random.randint(60, 1200))
                 for _ in range(random.randint(2, 8))]
        mode = random.choice(("horizontal", "vertical", "grid"))
        cw, ch, pl, *_ = collage._place(
            sizes, mode, "original", random.randint(0, 30),
            random.choice([0, 2, 3]), "center", 1.0)
        for (w0, h0), b in zip(sizes, pl):
            assert _abs_distortion((w0, h0), b) < 1.0, "原比例模式被拉伸"


def test_original_no_upscale():
    """原比例降采样后，任何图都不超过自己原始尺寸。"""
    sizes = [(2000, 100), (50, 2000), (800, 800)]
    imgs = [_solid(*s) for s in sizes]
    out, meta = collage.render(imgs, {"mode": "grid", "scaling": "original",
                                      "columns": 3, "gap": 0}, 600)
    for (w0, h0), b in zip(sizes, meta["boxes"]):
        assert b["w"] <= w0 and b["h"] <= h0, f"小图被放大：{w0}x{h0} -> {b['w']}x{b['h']}"


# ---------------------------------------------------------------------------
# 间距 / 背景 / 对齐 / 尺寸上限
# ---------------------------------------------------------------------------
def test_background_color():
    imgs = [Image.new("RGBA", (80, 80), (0, 0, 0, 0)),
            Image.new("RGB", (80, 80), (10, 200, 10))]
    out, meta = collage.render(imgs, {"mode": "horizontal", "gap": 20,
                                      "background": "#112233"}, 1000)
    # 第一张完全透明，间隙处应露出背景色
    assert out.getpixel((90, 40)) == (17, 34, 51)
    assert meta["background"] == "#112233"


def test_align_center_and_end():
    sizes = [(100, 100), (100, 300)]
    _, _, start, *_ = collage._place(sizes, "horizontal", "original", 0, 0, "start", 1.0)
    _, _, center, *_ = collage._place(sizes, "horizontal", "original", 0, 0, "center", 1.0)
    _, _, end, *_ = collage._place(sizes, "horizontal", "original", 0, 0, "end", 1.0)
    assert start[0][1] == 0
    assert center[0][1] == 100          # (300-100)/2
    assert end[0][1] == 200             # 贴底


def test_max_dim_downscale():
    sizes = [(1000, 1000)] * 6
    imgs = [_solid(*s) for s in sizes]
    out, meta = collage.render(imgs, {"mode": "vertical", "gap": 0}, 400)
    assert max(out.size) <= 400
    assert meta["scale"] < 1.0


# ---------------------------------------------------------------------------
# API：端到端（Flask test client）
# ---------------------------------------------------------------------------
def _client():
    # 隔离数据目录，避免污染真实 data/
    import tempfile
    import importlib
    tmp = tempfile.mkdtemp(prefix="collage_test_")
    config.DATA_DIR = tmp
    config.IMAGES_DIR = os.path.join(tmp, "images")
    config.RESULTS_DIR = os.path.join(tmp, "results")
    config.THUMBS_DIR = os.path.join(tmp, "thumbnails")
    config.CACHE_DIR = os.path.join(tmp, "cache")
    config.META_DIR = os.path.join(tmp, "metadata")
    config.IMAGES_JSON = os.path.join(config.META_DIR, "images.json")
    config.CACHE_JSON = os.path.join(config.META_DIR, "cache.json")
    # _ALL_DIRS 在模块导入时已固化，这里显式创建被改写后的目录
    for d in (config.IMAGES_DIR, config.RESULTS_DIR, config.THUMBS_DIR,
              config.CACHE_DIR, config.META_DIR):
        os.makedirs(d, exist_ok=True)

    # 重新初始化 api 模块单例，使其指向临时目录（config 已是同一对象，无需 reload）
    from server import api as api_mod
    api_mod.image_store = api_mod.ImageStore()
    api_mod.cache = api_mod.ResultCache()
    app = __import__("flask").Flask(__name__)
    api_mod.init_app(app)
    return app.test_client(), api_mod


def _png_bytes(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_api_preview_and_create():
    client, api_mod = _client()
    ids = []
    for img in [_solid(300, 400, (200, 50, 50)), _solid(500, 250, (50, 200, 90)),
                _solid(400, 400, (50, 90, 200))]:
        r = client.post("/api/images", data={"files": (io.BytesIO(_png_bytes(img)), "a.png")},
                        content_type="multipart/form-data")
        ids.append(r.get_json()["saved"][0]["id"])

    body = {"items": [{"image_id": i} for i in ids], "mode": "grid",
            "scaling": "uniform", "gap": 12, "background": "#ffffff",
            "align": "center", "columns": 2}
    r = client.post("/api/collage/preview", json=body)
    assert r.status_code == 200, r.get_json()
    pj = r.get_json()
    assert pj["count"] == 3 and pj["preview_url"].startswith("data:image/jpeg;base64,")
    assert len(pj["boxes"]) == 3 and not pj["failed"]

    r2 = client.post("/api/collage", json=body)
    assert r2.status_code == 200, r2.get_json()
    cj = r2.get_json()
    assert cj["file_url"].startswith("/api/results/")
    assert cj["width"] > 0 and cj["height"] > 0

    # 结果文件确实可下载且尺寸一致
    rf = client.get(cj["file_url"])
    assert rf.status_code == 200
    got = Image.open(io.BytesIO(rf.data))
    assert got.size == (cj["width"], cj["height"])

    # 相同参数命中缓存
    r3 = client.post("/api/collage", json=body)
    assert r3.get_json()["cache_hit"] is True


def test_api_partial_failure_isolated():
    """一张图损坏/缺失时，其余照常完成，失败单独列出原因。"""
    client, api_mod = _client()
    good = []
    for img in [_solid(300, 300), _solid(400, 300), _solid(300, 400)]:
        r = client.post("/api/images", data={"files": (io.BytesIO(_png_bytes(img)), "g.png")},
                        content_type="multipart/form-data")
        good.append(r.get_json()["saved"][0]["id"])

    # 直接往图像目录写一个损坏文件并登记
    import json as _json
    from server.storage import now_iso
    bad_id = "deadbeef" * 8
    bad_path = os.path.join(config.IMAGES_DIR, bad_id + ".png")
    with open(bad_path, "wb") as f:
        f.write(b"not an image")
    rec = {"id": bad_id, "filename": "broken.png", "stored_name": bad_id + ".png",
           "hash": bad_id, "ext": ".png", "format": "PNG", "width": 10, "height": 10,
           "size_bytes": 12, "created_at": now_iso(), "tags": [], "note": "", "annotations": []}
    meta = api_mod.image_store.meta
    meta.update(lambda doc: {**doc, bad_id: rec})

    items = [{"image_id": good[0]}, {"image_id": bad_id},
             {"image_id": "nonexistent-id"}, {"image_id": good[1]}]
    r = client.post("/api/collage", json={"items": items, "mode": "vertical"})
    # 2 张好图（good[0], good[1]）仍能拼成
    assert r.status_code == 200, r.get_json()
    j = r.get_json()
    assert j["count"] == 2
    reasons = {(f["image_id"], f["reason"]) for f in j["failed"]}
    assert (bad_id, next(rc for i, rc in reasons if i == bad_id))[1]
    assert any(i == "nonexistent-id" for i, _ in reasons)
    assert all(rc for _, rc in reasons), "每个失败都要有原因"


def test_api_validation():
    client, _ = _client()
    r = client.post("/api/images", data={"files": (io.BytesIO(_png_bytes(_solid(10, 10))), "x.png")},
                    content_type="multipart/form-data")
    iid = r.get_json()["saved"][0]["id"]
    # 不足 2 张
    assert client.post("/api/collage", json={"items": [{"image_id": iid}]}).status_code == 400
    # 非法 mode
    r2 = client.post("/api/collage/preview",
                     json={"items": [{"image_id": iid}, {"image_id": iid}], "mode": "diagonal"})
    assert r2.status_code == 400
    # 非法间距
    r3 = client.post("/api/collage/preview",
                     json={"items": [{"image_id": iid}, {"image_id": iid}], "gap": 9999})
    assert r3.status_code == 400


def main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"\n拼图测试全部通过（{len(fns)} 项） ✔")


if __name__ == "__main__":
    main()
