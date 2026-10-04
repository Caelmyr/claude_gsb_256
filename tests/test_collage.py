"""拼图模块单元测试：布局几何、等比不变形、相册式排版、失败隔离、参数校验。

运行：python tests/test_collage.py
不依赖 HTTP 层，直接调用 server.collage 的纯函数。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

from server import collage as cg  # noqa: E402


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def _src(i, w, h):
    return {"id": f"img{i}", "index": i, "width": w, "height": h}


def _params(**over):
    base = {"layout": "vertical", "scale_mode": "uniform", "fit": "contain",
            "gap": 10, "padding": 10, "cols": 3, "background": "#ffffff"}
    base.update(over)
    return base


def _aspect(w, h):
    return w / float(h)


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------
def test_color_parse():
    assert cg.parse_color("#ff0000") == (255, 0, 0)
    assert cg.parse_color("#f00") == (255, 0, 0)
    assert cg.parse_color("garbage", (1, 2, 3)) == (1, 2, 3)
    assert cg.parse_color((10, 20, 300)) == (10, 20, 255)
    print("  颜色解析 ok")


def test_params_validation():
    for bad in [
        {"layout": "diagonal"}, {"scale_mode": "crazy"}, {"fit": "squeeze"},
        {"gap": -1}, {"gap": 99999}, {"cols": 0}, {"cols": 50},
    ]:
        try:
            cg.normalize_params(bad)
        except cg.CollageError:
            pass
        else:
            raise AssertionError(f"应拒绝非法参数: {bad}")
    p = cg.normalize_params({})
    assert p["layout"] == "vertical" and p["padding"] == p["gap"] == 8
    print("  参数校验 ok")


def test_strip_horizontal_uniform_height():
    srcs = [_src(0, 100, 200), _src(1, 300, 100), _src(2, 400, 400)]
    plan = cg.plan_layout(srcs, _params(layout="horizontal", scale_mode="uniform"))
    # 统一高度：每张图槽位高 = 画布内容高，图按原比例缩放 -> 宽 = h * ratio
    h = plan["height"] - 20
    for it, s in zip(plan["items"], srcs):
        assert it["h"] == h
        assert it["img_h"] == h
        assert abs(it["img_w"] - round(h * _aspect(s["width"], s["height"]))) <= 1
    # x 严格按顺序递增（排列顺序）
    xs = [it["x"] for it in plan["items"]]
    assert xs == sorted(xs)
    print(f"  横向统一高度 ok（画布 {plan['width']}x{plan['height']}）")


def test_strip_vertical_uniform_width():
    srcs = [_src(0, 100, 200), _src(1, 300, 100), _src(2, 400, 400)]
    plan = cg.plan_layout(srcs, _params(layout="vertical", scale_mode="uniform"))
    w = plan["width"] - 20
    for it, s in zip(plan["items"], srcs):
        assert it["w"] == w
        assert it["img_w"] == w
        assert abs(it["img_h"] - round(w / _aspect(s["width"], s["height"]))) <= 1
    ys = [it["y"] for it in plan["items"]]
    assert ys == sorted(ys)
    print(f"  纵向统一宽度 ok（画布 {plan['width']}x{plan['height']}）")


def test_order_respected():
    srcs = [_src(i, 100 + i * 30, 200) for i in range(5)]
    order = [4, 0, 2, 3, 1]
    reordered = [dict(srcs[i], index=k) for k, i in enumerate(order)]
    plan = cg.plan_layout(reordered, _params(layout="vertical"))
    assert [it["index"] for it in plan["items"]] == [0, 1, 2, 3, 4]
    assert [it["id"] for it in plan["items"]] == [f"img{i}" for i in order]
    print("  排列顺序 ok")


def test_grid_uniform_cells():
    srcs = [_src(i, 100 + i * 7, 300 - i * 11) for i in range(7)]
    plan = cg.plan_layout(srcs, _params(layout="grid", scale_mode="uniform",
                                        cols=3, gap=6, padding=6, cell_ratio=1.0))
    # 3 列 3 行；所有槽位同尺寸；行列严格对齐
    assert len(plan["items"]) == 7
    ws = {(it["w"], it["h"]) for it in plan["items"]}
    assert len(ws) == 1, ws
    xs = sorted({it["x"] for it in plan["items"]})
    ys = sorted({it["y"] for it in plan["items"]})
    assert len(xs) == 3 and len(ys) == 3
    # 网格间距
    assert xs[1] - xs[0] == plan["items"][0]["w"] + 6
    print(f"  统一网格 ok（{len(xs)} 列 x {len(ys)} 行）")


def test_justified_no_distortion():
    """相册式排版：每张图槽位宽高比必须等于原始宽高比（无拉伸），
    且同一行内所有图高度一致（对齐拼接、无错落大空白）。"""
    srcs = [_src(0, 2000, 1000), _src(1, 800, 2400), _src(2, 1500, 1500),
            _src(3, 3000, 1200), _src(4, 600, 900), _src(5, 1800, 1100),
            _src(6, 1000, 2600)]
    plan = cg.plan_layout(srcs, _params(layout="grid", scale_mode="original",
                                        cols=3, gap=8, padding=0))
    by_id = {s["id"]: s for s in srcs}
    rows = {}
    for it in plan["items"]:
        ratio_slot = it["w"] / float(it["h"])
        ratio_src = _aspect(by_id[it["id"]]["width"], by_id[it["id"]]["height"])
        assert abs(ratio_slot - ratio_src) / ratio_src < 0.02, (it["id"], ratio_slot, ratio_src)
        rows.setdefault(it["y"], []).append(it)
    # 除最后一行外，每行都应凑满到接近行宽（左右无大片空白）
    row_ys = sorted(rows)
    for ry in row_ys[:-1]:
        right = max(it["x"] + it["w"] for it in rows[ry])
        assert right >= plan["width"] * 0.9, (ry, right, plan["width"])
        hs = {it["h"] for it in rows[ry]}
        assert len(hs) == 1
    print(f"  相册式排版 ok（{len(row_ys)} 行，无变形、行间对齐）")


def test_contain_never_crops_or_stretches():
    """contain：贴入的图必须保持原比例（fit_into 返回尺寸等比）。"""
    img = Image.new("RGB", (100, 400))
    piece, ox, oy, crop = cg.fit_into(img, 300, 300, "contain")
    assert crop is None
    assert piece.size[0] / float(piece.size[1]) == 100 / 400.0
    assert piece.size[1] == 300 and piece.size[0] < 300  # 高边填满，宽边留背景
    img2 = Image.new("RGB", (500, 200))
    piece2, _, _, crop2 = cg.fit_into(img2, 300, 300, "contain")
    assert piece2.size[0] == 300 and piece2.size[1] < 300
    print("  contain 不裁剪不变形 ok")


def test_cover_fills_and_crops():
    img = Image.new("RGB", (100, 400))
    piece, ox, oy, crop = cg.fit_into(img, 300, 300, "cover")
    assert crop is not None
    cropped = piece.crop(crop)
    assert cropped.size == (300, 300)  # 恰好填满槽位
    print("  cover 填满裁切 ok")


def test_render_failure_isolation():
    images = {f"ok{i}": Image.new("RGB", (120 + i * 10, 200), (i * 30, 100, 200))
              for i in range(3)}

    def loader(image_id):
        if image_id.startswith("bad"):
            raise ValueError("无法识别的图像格式")
        return images[image_id]

    ordered = [{"id": "ok0"}, {"id": "bad1"}, {"id": "ok1"}, {"id": "bad2"}, {"id": "ok2"}]
    canvas, plan, failures = cg.render(ordered, _params(layout="vertical"), loader)
    assert canvas is not None and len(plan["items"]) == 3
    assert len(failures) == 2
    assert [f["index"] for f in failures] == [1, 3]
    assert all("reason" in f and f["id"].startswith("bad") for f in failures)
    # 成功图仍按其在原请求中的 index 排列
    assert [it["index"] for it in plan["items"]] == [0, 2, 4]
    print(f"  失败隔离 ok（成功 3 / 失败 {len(failures)}）")


def test_render_all_fail():
    def loader(image_id):
        raise OSError("boom")
    canvas, plan, failures = cg.render([{"id": "x"}, {"id": "y"}], _params(), loader)
    assert canvas is None and plan is None and len(failures) == 2
    print("  全部失败时不渲染 ok")


def test_canvas_cap_and_padding():
    """超长大图：成品最长边必须被限制在 CANVAS_MAX_DIM 内；外边距生效。"""
    srcs = [_src(i, 3000, 2000) for i in range(6)]
    plan = cg.plan_layout(srcs, _params(layout="horizontal", gap=20, padding=30))
    assert max(plan["width"], plan["height"]) <= cg.CANVAS_MAX_DIM

    # 不触发整体缩放的小图：首图位置即外边距
    small = cg.plan_layout([_src(0, 100, 100), _src(1, 100, 100)],
                           _params(layout="vertical", gap=10, padding=25))
    assert small["items"][0]["x"] == 25 and small["items"][0]["y"] == 25
    print(f"  大图保护/外边距 ok（{plan['width']}x{plan['height']}）")


def test_render_pixels_background():
    bg = (0, 128, 0)
    img = Image.new("RGB", (50, 200), (255, 0, 0))
    canvas, plan, failures = cg.render(
        [{"id": "a"}], _params(layout="grid", scale_mode="uniform", fit="contain",
                               cols=1, gap=0, padding=0, cell_ratio=1.0,
                               base_cell=200, background="#008000"),
        lambda _id: img)
    assert not failures
    # 竖长图 contain 进 1:1 方槽：上下贴满、左右应露出背景绿
    assert canvas.getpixel((5, plan["height"] // 2)) == bg
    assert canvas.getpixel((plan["width"] - 5, plan["height"] // 2)) == bg
    assert canvas.size == (plan["width"], plan["height"])
    print("  渲染像素/背景色 ok")


def main():
    print("== 拼图模块测试 ==")
    test_color_parse()
    test_params_validation()
    test_strip_horizontal_uniform_height()
    test_strip_vertical_uniform_width()
    test_order_respected()
    test_grid_uniform_cells()
    test_justified_no_distortion()
    test_contain_never_crops_or_stretches()
    test_cover_fills_and_crops()
    test_render_failure_isolation()
    test_render_all_fail()
    test_canvas_cap_and_padding()
    test_render_pixels_background()
    print("\n全部拼图测试通过 ✔")


if __name__ == "__main__":
    main()
