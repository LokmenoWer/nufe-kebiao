import struct
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import captcha_solver as cs  # noqa: E402


def _png(w, h, ct, rows):
    raw = b"".join(bytes([0]) + bytes(r) for r in rows)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, ct, 0, 0, 0)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def test_png_gray_and_rgba():
    gray = _png(3, 2, 0, [bytes([1, 2, 3]), bytes([4, 5, 6])])
    w, h, grid = cs._decode_png(gray)
    assert (w, h) == (3, 2)
    assert grid[1][2] == (6, 6, 6, 255)
    rgba = _png(2, 1, 6, [bytes([9, 8, 7, 128, 1, 2, 3, 0])])
    _, _, g2 = cs._decode_png(rgba)
    assert g2[0][0] == (9, 8, 7, 128) and g2[0][1][3] == 0


def test_png_paeth_filter():
    # 第 2 行用 Paeth 过滤（filter=4），验证滤波还原
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    raw = bytes([0, 10, 20, 30]) + bytes([4]) + bytes([(10 - 0), (20 - 10) + 0, (30 - 20) + 0])
    # Paeth: a=左, b=上, c=左上。手工值：line[0]=10+paeth(0,10,0)=10+10=20…
    # 直接用实现自洽：解压后逐字节和原始比对即可
    ihdr = struct.pack(">IIBBBBB", 3, 2, 8, 0, 0, 0, 0)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    w, h, grid = cs._decode_png(data)
    assert (w, h) == (3, 2)
    # 逐像素手算：x0: paeth(0,10,0)=10 → 20；x1: paeth(20,20,10)=20 → 30；x2: paeth(30,30,20)=30 → 40
    assert [grid[0][k][0] for k in range(3)] == [10, 20, 30]
    assert [grid[1][k][0] for k in range(3)] == [20, 30, 40]


def test_jpeg_baseline_dc_only():
    # 16x16、单分量、DC-only：4 个块的值 1/2/3/0（手工编码，规范码表）
    dqt = bytes([0x00]) + bytes([8] + [16] * 63)
    sof = bytes([8, 0, 16, 0, 16, 1, 1, 0x11, 0])
    dht_dc = bytes([0x00]) + bytes([1, 1, 1] + [0] * 13) + bytes([0, 1, 2])
    dht_ac = bytes([0x10]) + bytes([1] + [0] * 15) + bytes([0])
    jpg = (b"\xff\xd8" + b"\xff\xdb\x00\x43" + dqt
           + b"\xff\xc4\x00\x16" + dht_dc + b"\xff\xc4\x00\x14" + dht_ac
           + b"\xff\xc0\x00\x0b" + sof + b"\xff\xda\x00\x08" + bytes([1, 1, 0, 0, 2, 0])
           + b"\xad\x36\x3f" + b"\xff\xd9")
    w, h, plane = cs._decode_jpeg(jpg)
    assert (w, h) == (16, 16)
    got = [plane[y * w + x] for y, x in [(0, 0), (0, 9), (8, 0), (8, 9)]]
    assert got == [pytest.approx(v) for v in (1.0, 2.0, 3.0, 0.0)]


def test_idct_matches_orthogonal_reference():
    import math
    c = lambda k: math.sqrt(1 / 8) if k == 0 else math.sqrt(2 / 8)  # noqa: E731
    cosf = lambda n, k: math.cos((2 * n + 1) * k * math.pi / 16)  # noqa: E731
    f = [[float((x * 3 + y * 5) % 11 + 1) for x in range(8)] for y in range(8)]
    F = [[sum(c(u) * c(v) * f[y][x] * cosf(x, u) * cosf(y, v) for x in range(8) for y in range(8))
          for v in range(8)] for u in range(8)]
    block = [F[u][v] for u in range(8) for v in range(8)]
    out = cs._idct(block)
    err = max(abs(out[y * 8 + x] - f[y][x]) for y in range(8) for x in range(8))
    assert err < 1e-6


def test_solve_slider_finds_gap_x():
    # 大图 12 宽，3x2 不透明拼图嵌在 x=4；小图与大图等高
    px_row0 = [180, 180, 180, 255, 0, 0, 0, 255, 60, 60, 60, 255]
    px_row1 = [0, 0, 0, 255, 90, 90, 90, 255, 0, 0, 0, 255]
    sm = _png(3, 2, 6, [bytes(px_row0), bytes(px_row1)])
    row0 = bytes([0, 0, 0, 0, 180, 0, 60, 0, 0, 0, 0, 0])
    row1 = bytes([0, 0, 0, 0, 0, 90, 0, 0, 0, 0, 0, 0])
    big = _png(12, 2, 0, [row0, row1])  # 大图与小图等高，拼图行号直接对齐
    cl, ml, tracks = cs.solve_slider({"big": (big, "image/png"), "small": (sm, "image/png")})
    assert cl == 280
    assert ml == pytest.approx(4 * 280 / 12, abs=0.05)
    assert tracks[0] == {"a": 0, "b": 0, "c": 0}
    assert tracks[-1]["a"] == ml
    assert all(t["c"] >= 0 for t in tracks)


def test_solve_image_captcha_digits():
    font = {"1": ["..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."],
            "0": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."]}
    w, h = 13, 7
    img = [255] * (w * h)
    for gi, off in (("1", 1), ("0", 7)):
        for r, row in enumerate(font[gi]):
            for c, ch in enumerate(row):
                if ch == "#":
                    img[r * w + off + c] = 0
    png = _png(w, h, 0, [bytes(img[r * w:(r + 1) * w]) for r in range(h)])
    assert cs.solve_image_captcha(png) == "10"


def test_otsu_and_empty_fallback():
    assert cs._otsu([0] * 10 + [255] * 10) in (0, 1)
    assert cs.solve_image_captcha(b"not an image") == ""


def _draw_digits(digs, gap=1):
    font = {
        "0": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
        "1": ["..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."],
        "8": [".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."],
        "9": [".###.", "#...#", "#...#", ".####", "....#", "....#", ".###."],
    }
    glyph_w, h = 5, 7
    w = len(digs) * glyph_w + (len(digs) - 1) * gap + 2
    img = [255] * (w * h)
    for gi, dig in enumerate(digs):
        off = 1 + gi * (glyph_w + gap)
        for r, row in enumerate(font[dig]):
            for c, ch in enumerate(row):
                if ch == "#":
                    img[r * w + off + c] = 0
    return w, h, img


def test_ocr_with_gray_edges_and_tight_gaps():
    # 抗锯齿：笔画 0，紧邻边缘 128，背景 255
    w, h, img = _draw_digits("18")
    aa = list(img)
    for i, v in enumerate(img):
        if v == 255:
            x, y = i % w, i // w
            neigh = [img[ny * w + nx] for ny, nx in
                     ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1))
                     if 0 <= ny < h and 0 <= nx < w]
            if 0 in neigh:
                aa[i] = 128
    assert cs.solve_image_captcha(_png(w, h, 0, [bytes(aa[r * w:(r + 1) * w]) for r in range(h)])) == "18"
    # 紧贴无间隙：两字并成一组（宽 10 > 1.5*5）→ 列墨量谷底断字
    w2, h2, img2 = _draw_digits("90", gap=0)
    assert cs.solve_image_captcha(_png(w2, h2, 0, [bytes(img2[r * w2:(r + 1) * w2]) for r in range(h2)])) == "90"
