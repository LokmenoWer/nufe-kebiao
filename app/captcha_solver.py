"""人机验证自动求解（纯 Python，无第三方依赖，供后台刷新等非交互场景使用）。

- 滑块拼图：解码大图（JPEG baseline）与拼图条（PNG），以拼图条 alpha 掩膜做
  去均值归一化互相关（NCC）匹配，得到拼图缺口在大图里的横向位置，
  换算成学校插件 280 宽坐标系的 moveLength，并生成减速轨迹（与页面插件采样格式一致）。
- 图形验证码：Otsu 二值化 → 按列切分字形 → 归一化到 5x7 → 与内置点阵模板做
  最近邻匹配（0-9 / A-Z / + - = ?）。

返回结构与 app/cas.py 的既有接口直接对齐：
    solve_slider(challenge) -> (canvas_length, move_length, tracks)
    solve_image_captcha(data) -> "1a2b" 之类的字符串；识别失败返回 ""
"""
from __future__ import annotations

import math
import zlib

# ============================== PNG ==============================


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _decode_png(data):
    """返回 (w, h, grid)，grid 为逐行 [(r,g,b,a), ...]。非隔行；8 位全支持，palette 支持 1/2/4/8 位。"""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG 数据")
    pos, idat, plte, trns = 8, bytearray(), b"", b""
    w = h = bd = ct = interlace = 0
    n = len(data)
    while pos + 8 <= n:
        ln = int.from_bytes(data[pos:pos + 4], "big")
        typ = data[pos + 4:pos + 8]  # 块类型占 4 字节
        body = data[pos + 8:pos + 8 + ln]
        pos += 8 + ln + 4
        if typ == b"IHDR":
            w = int.from_bytes(body[0:4], "big")
            h = int.from_bytes(body[4:8], "big")
            bd, ct, interlace = body[8], body[9], body[12]
        elif typ == b"PLTE":
            plte = body
        elif typ == b"tRNS":
            trns = body
        elif typ == b"IDAT":
            idat += body
        elif typ == b"IEND":
            break
    if not w or not h or interlace or bd not in (1, 2, 4, 8) or ct not in (0, 2, 3, 4, 6):
        raise ValueError("不支持的 PNG 参数")
    if ct != 3 and bd != 8:
        raise ValueError("不支持的 PNG 位深")
    samples = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ct]  # 每像素分量数
    stride = (w * samples + 7) // 8 if bd != 8 else w * samples
    raw = zlib.decompress(bytes(idat))
    lines, prev, i = [], bytearray(stride), 0
    for _ in range(h):
        f = raw[i]
        i += 1
        line = bytearray(raw[i:i + stride])
        i += stride
        if f:
            for x in range(stride):
                a = line[x - samples] if (bd == 8 and x >= samples) else 0
                b_ = prev[x]
                c_ = prev[x - samples] if (bd == 8 and x >= samples) else 0
                if f == 1:
                    line[x] = (line[x] + a) & 0xFF
                elif f == 2:
                    line[x] = (line[x] + b_) & 0xFF
                elif f == 3:
                    line[x] = (line[x] + ((a + b_) >> 1)) & 0xFF
                else:  # f == 4
                    line[x] = (line[x] + _paeth(a, b_, c_)) & 0xFF
        prev = line
        lines.append(line)

    transparent = set(trns) if (ct == 3 and trns) else set()
    grid_rows = []
    for line in lines:
        row = []
        if ct == 3:
            if bd == 8:
                idxs = list(line)
            else:
                div, mask = {1: 8, 2: 4, 4: 2}[bd], (1 << bd) - 1
                idxs = [((byte >> (bd * (div - 1 - k))) & mask) for byte in line for k in range(div)]
            for idx in idxs[:w]:
                rgb = (plte[3 * idx:3 * idx + 3] + b"\x00\x00\x00")[:3]
                row.append((rgb[0], rgb[1], rgb[2], 0 if idx in transparent else 255))
        elif ct == 0:
            row = [(v, v, v, 255) for v in line[:w]]
        elif ct == 4:
            row = [(line[x], line[x], line[x], line[x + 1]) for x in range(0, w * 2, 2)]
        elif ct == 2:
            row = [(line[x], line[x + 1], line[x + 2], 255) for x in range(0, w * 3, 3)]
        else:  # ct == 6
            row = [(line[x], line[x + 1], line[x + 2], line[x + 3]) for x in range(0, w * 4, 4)]
        grid_rows.append(row)
    return w, h, grid_rows


# ============================== JPEG (baseline sequential) ==============================

_ZIGZAG = [0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5,
           12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21, 28,
           35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51,
           58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63]

_COS = [[math.cos((2 * x + 1) * u * math.pi / 16) for x in range(8)] for u in range(8)]
_N0 = math.sqrt(0.5)


def _idct(block):
    tmp = [0.0] * 64
    for v in range(8):
        for x in range(8):
            s = 0.0
            for u in range(8):
                s += (_N0 if u == 0 else 1.0) * block[u * 8 + v] * _COS[u][x]
            tmp[v * 8 + x] = s * 0.25
    out = [0.0] * 64
    for y in range(8):
        for x in range(8):
            s = 0.0
            for v in range(8):
                s += (_N0 if v == 0 else 1.0) * tmp[v * 8 + x] * _COS[v][y]
            out[y * 8 + x] = s
    return out


class _Bits:
    __slots__ = ("d", "i", "n", "cur", "cnt")

    def __init__(self, d, i, n):
        self.d, self.i, self.n, self.cur, self.cnt = d, i, n, 0, 0

    def bit(self):
        if self.cnt == 0:
            self.cur = self.d[self.i]
            self.i += 1
            if self.cur == 0xFF and self.i < self.n and self.d[self.i] == 0:
                self.i += 1  # 压缩填充 0xFF 0x00
            self.cnt = 8
        self.cnt -= 1
        return (self.cur >> self.cnt) & 1

    def bits(self, k):
        v = 0
        for _ in range(k):
            v = (v << 1) | self.bit()
        return v


def _huff_val(br, table):
    if not table:
        return 0
    code = 0
    for length in range(1, 17):
        code = (code << 1) | br.bit()
        v = table.get((length, code))
        if v is not None:
            return v
    return 0


def _signed(v, size):
    if size and v < (1 << (size - 1)):
        return v - (1 << size) + 1
    return v


def _decode_block(br, comp, qt):
    block = [0.0] * 64
    qtab = qt[comp["q"]]
    t = _huff_val(br, comp["dc"])
    size, nv = t, t  # DC 霍夫曼值直接就是附加位数
    if size:
        nv = br.bits(size)
    block[0] = _signed(nv, size) * qtab[0]
    k, ac = 1, comp["ac"]
    while k < 64:
        t = _huff_val(br, ac)
        run, sz = t >> 4, t & 0xF
        if t == 0:  # EOB
            break
        if sz == 0 and run == 15:  # ZLL
            k += 16
            continue
        k += run
        if k >= 64:
            break
        vv = br.bits(sz) if sz else 0
        block[_ZIGZAG[k]] = _signed(vv, sz) * qtab[k]
        k += 1
    return _idct(block)


def _decode_jpeg(data):
    """返回 (w, h, 行主序灰度 list)。支持 baseline/extended sequential，取第一个分量（Y）。"""
    n = len(data)
    if data[:2] != b"\xff\xd8":
        raise ValueError("不是 JPEG 数据")
    pos = 2
    qt, htab, comps, w, h, ridx = {}, {}, [], 0, 0, 0
    while pos + 1 < n:
        if data[pos] != 0xFF:
            pos += 1
            continue
        m = data[pos + 1]
        pos += 2
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            continue
        if m == 0xD9:
            break
        if m == 0xDD:  # DRI
            ridx = (data[pos + 2] << 8) | data[pos + 3]
            pos += (data[pos] << 8) | data[pos + 1]
            continue
        ln = (data[pos] << 8) | data[pos + 1]
        seg = data[pos + 2:pos + ln]
        pos += ln
        if m == 0xDB:  # DQT
            i = 0
            while i < len(seg):
                prec, tid = seg[i] >> 4, seg[i] & 0xF
                i += 1
                size = 2 if prec else 1
                tab = []
                for _ in range(64):
                    tab.append(int.from_bytes(seg[i:i + size], "big") if size == 2 else seg[i])
                    i += size
                qt[tid] = tab
        elif m == 0xC4:  # DHT
            i = 0
            while i < len(seg):
                tc, tid = seg[i] >> 4, seg[i] & 0xF
                i += 1
                counts = list(seg[i:i + 16])
                i += 16
                vals = seg[i:i + sum(counts)]
                i += sum(counts)
                code, k, table = 0, 0, {}
                for length in range(1, 17):
                    for _ in range(counts[length - 1]):
                        table[(length, code)] = vals[k]
                        k += 1
                        code += 1
                    code <<= 1
                htab[(tc, tid)] = table
        elif m in (0xC0, 0xC1):  # SOF baseline / extended sequential
            h = (seg[1] << 8) | seg[2]
            w = (seg[3] << 8) | seg[4]
            comps = []
            for c in range(seg[5]):
                samp = seg[7 + 3 * c]
                comps.append({"id": seg[6 + 3 * c], "h": samp >> 4, "v": samp & 0xF,
                              "q": seg[8 + 3 * c], "dc": None, "ac": None})
        elif m == 0xC2:
            raise ValueError("暂不支持渐进式 JPEG")
        elif m == 0xDA:  # SOS
            i = 1
            for _s in range(seg[0]):
                cs, tq = seg[i], seg[i + 1]
                i += 2
                for comp in comps:
                    if comp["id"] == cs:
                        comp["dc"] = htab.get((0, tq >> 4))
                        comp["ac"] = htab.get((1, tq & 0xF))
            while pos < n and data[pos] == 0xFF:  # 跳过 FFFF 填充
                pos += 1
            return _jpeg_scan(data, pos, n, w, h, comps, qt, ridx)
        # 其它段（注释 / APP 等）已随 ln 跳过
    raise ValueError("JPEG 中未找到完整扫描数据")


def _jpeg_scan(data, pos, n, w, h, comps, qt, ridx):
    hmax = max(c["h"] for c in comps)
    vmax = max(c["v"] for c in comps)
    mcuw, mcuh = hmax * 8, vmax * 8
    nmw, nmh = (w + mcuw - 1) // mcuw, (h + mcuh - 1) // mcuh
    br = _Bits(data, pos, n)
    plane = [0.0] * (w * h)
    y = comps[0]
    for mi in range(nmw * nmh):
        mx, my = (mi % nmw) * mcuw, (mi // nmw) * mcuh
        for comp in comps:
            nwb, nhb = max(1, comp["h"]), max(1, comp["v"])
            sh = max(1, hmax // comp["h"])
            sv = max(1, vmax // comp["v"])
            for j in range(nwb * nhb):
                block = _decode_block(br, comp, qt)
                if comp is not y:
                    continue
                bx = mx + (j % nwb) * 8 * sh
                by = my + (j // nwb) * 8 * sv
                for r in range(8):
                    y0 = by + r * sv
                    if y0 >= h:
                        break
                    for c in range(8):
                        x0 = bx + c * sh
                        if x0 >= w:
                            break
                        val = block[r * 8 + c]
                        val = 255.0 if val > 255 else (0.0 if val < 0 else val)
                        for dy in range(sv):
                            if y0 + dy < h:
                                plane[(y0 + dy) * w + x0] = val
        if ridx and (mi + 1) % ridx == 0:
            while br.i < n and data[br.i] != 0xFF:
                br.i += 1
            if br.i + 1 < n and 0xD0 <= data[br.i + 1] <= 0xD7:
                br.i += 2
                br.cur, br.cnt = 0, 0
    return w, h, plane


# ============================== 统一灰度入口 ==============================


def _gray(data):
    """(w, h, 行主序灰度 list)。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h, grid = _decode_png(data)
        return w, h, [(r + g + b) / 3.0 for row in grid for (r, g, b, _a) in row]
    if data[:2] == b"\xff\xd8":
        return _decode_jpeg(data)
    raise ValueError("不支持的图片格式")


# ============================== 滑块求解 ==============================

SLIDER_CANVAS_WIDTH = 280  # 同 cas.SLIDER_CANVAS_WIDTH


def _num(v):
    fv = float(v)
    return int(fv) if fv.is_integer() else round(fv, 2)


def solve_slider(challenge):
    """challenge: cas.parse_slider_challenge 的结果。返回 (canvas_length, move_length, tracks)。"""
    big_w, big_h, big = _gray(challenge["big"][0])
    sm_w, sm_h, sm_grid = _decode_png(challenge["small"][0])
    rows = [r for r in range(sm_h) if any(px[3] for px in sm_grid[r])]
    cols = [c for c in range(sm_w) if any(sm_grid[r][c][3] for r in rows)]
    if not rows or not cols:  # 无 alpha 通道时整条参与匹配
        rows, cols = list(range(sm_h)), list(range(sm_w))
    y0, x0 = rows[0], cols[0]
    ph, pw = rows[-1] - y0 + 1, cols[-1] - cols[0] + 1
    mask, vals = [], []
    for r in range(y0, y0 + ph):
        mr, vr = [], []
        for c in range(x0, x0 + pw):
            px = sm_grid[r][c]
            mr.append(1 if px[3] else 0)
            vr.append((px[0] + px[1] + px[2]) / 3.0)
        mask.append(mr)
        vals.append(vr)
    idx = [(i, j) for i in range(ph) for j in range(pw) if mask[i][j]]
    if not idx or big_h < y0 + ph or pw > big_w:
        raise ValueError("滑块图片尺寸异常")
    nn = len(idx)
    ma = sum(vals[i][j] for i, j in idx) / nn
    ca = [(i, j, vals[i][j] - ma) for i, j in idx]
    best, best_x = -2.0, 0
    for x in range(0, big_w - pw + 1):
        win = [big[(y0 + i) * big_w + x + j] for i, j, _ in ca]
        mb = sum(win) / nn
        cov = va = vb = 0.0
        for (_i, _j, da), b in zip(ca, win, strict=True):
            db = b - mb
            cov += da * db
            va += da * da
            vb += db * db
        if va > 0 and vb > 0:
            score = cov / math.sqrt(va * vb)
            if score > best:
                best, best_x = score, x
    move = best_x * SLIDER_CANVAS_WIDTH / big_w
    return SLIDER_CANVAS_WIDTH, _num(move), _make_tracks(move)


def _make_tracks(move, steps=10):
    """模拟人手拖动：起步快、接近终点减速、最后收敛（字段格式同 static/js/slider.js）。"""
    tracks = [{"a": 0, "b": 0, "c": 0}]
    for k in range(1, steps + 1):
        t = k / steps
        a = move * (1 - (1 - t) ** 3)  # ease-out 三次
        tracks.append({"a": _num(a), "b": 0, "c": 18 + (k % 3) * 4})
    tracks[-1]["a"] = _num(move)
    return tracks


# ============================== 图形验证码 ==============================

_FONT = {
    "0": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": (".###.", "#...#", "....#", "..##.", ".#...", "#....", "#####"),
    "3": (".###.", "#...#", "....#", ".###.", "....#", "#...#", ".###."),
    "4": ("#...#", "#...#", "#...#", ".####", "....#", "....#", "....#"),
    "5": ("####.", "#....", "#....", ".###.", "....#", "#...#", ".###."),
    "6": (".###.", "#...#", "#....", "###..", "#...#", "#...#", ".###."),
    "7": ("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    "8": (".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": (".###.", "#...#", "#...#", ".####", "....#", "....#", ".###."),
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "B": ("####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."),
    "C": (".###.", "#...#", "#....", "#....", "#....", "#...#", ".###."),
    "D": ("####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "F": ("#####", "#....", "#....", "####.", "#....", "#....", "#...."),
    "G": (".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".###."),
    "H": ("#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "I": (".###.", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "J": ("..###", "...#.", "...#.", "...#.", "...#.", "#...#", ".###."),
    "K": ("#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"),
    "L": ("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "M": ("#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"),
    "N": ("#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"),
    "O": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "P": (".###.", "#...#", "#...#", "####.", "#....", "#....", "#...."),
    "Q": (".###.", "#...#", "#...#", "#...#", "#.#.#", "....#", ".###."),
    "R": (".###.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "S": (".###.", "#...#", "#....", ".###.", "....#", "#...#", ".###."),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "U": ("#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "V": ("#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "W": ("#...#", "#...#", "#...#", "#.#.#", "#.#.#", "##.##", "#...#"),
    "X": ("#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"),
    "Y": ("#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."),
    "Z": ("#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"),
    "+": (".....", ".....", "..#..", ".###.", "..#..", ".....", "....."),
    "-": (".....", ".....", ".....", ".###.", ".....", ".....", "....."),
    "=": (".....", ".....", ".###.", ".....", ".###.", ".....", "....."),
    "?": (".###.", "#...#", "....#", "..##.", "..#..", ".....", "..#.."),
}
_TMPL = {k: [[1 if ch == "#" else 0 for ch in row] for row in v] for k, v in _FONT.items()}


def _otsu(vals):
    hist = [0] * 256
    for v in vals:
        hist[int(v)] += 1
    total = len(vals)
    sum_all = sum(i * hist[i] for i in range(256))
    best_t, best_var, w_b, sum_b = 0, -1.0, 0, 0.0
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        if w_b == total:
            break
        sum_b += t * hist[t]
        m_b = sum_b / w_b
        m_w = (sum_all - sum_b) / (total - w_b)
        var = w_b * (total - w_b) * (m_b - m_w) ** 2
        if var > best_var:
            best_var, best_t = var, t
    return best_t


def _norm5x7(bin_grid, r0, r1, c0, c1):
    hh, ww = r1 - r0 + 1, c1 - c0 + 1
    out = []
    for r in range(7):
        sr = r0 + min(hh - 1, int((r + 0.5) * hh / 7))
        out.append([bin_grid[sr][c0 + min(ww - 1, int((c + 0.5) * ww / 5))] for c in range(5)])
    return out


def _binarize(px, w, h, thr):
    grid = [[1 if v <= thr else 0 for v in row] for row in (px[i * w:(i + 1) * w] for i in range(h))]
    n1 = sum(sum(row) for row in grid)
    if n1 * 2 > len(px):  # 少数类是字：反相
        grid = [[1 - v for v in row] for row in grid]
    return grid


def _split_columns(grid, w, h):
    """列投影切分：先按 >1px 空列分段；过宽连笔段用“参考字宽”等分断开。

    参考字宽优先取其它分段的中位宽；只有一个连笔段时按 5x7 点阵的宽高比（hh*5/7）估计。
    """
    cols_with = [c for c in range(w) if any(grid[r][c] for r in range(h))]
    if not cols_with:
        return []
    groups, start, prev = [], cols_with[0], cols_with[0]
    for c in cols_with[1:]:
        if c - prev > 1:
            groups.append((start, prev))
            start = c
        prev = c
    groups.append((start, prev))
    # 单行验证码字高恒定：用全图行墨数按 5x7 点阵宽高比估计标准字宽，过宽连笔段等分
    hh = sum(1 for r in range(h) if any(grid[r])) or h
    est = max(2, round(hh * 5 / 7))
    out = []
    for a, b in groups:
        width = b - a + 1
        k = round(width / est)
        if k < 2 or width <= est * 1.35:
            out.append((a, b))
            continue
        step = width / k
        for part in range(k):
            pa = a + int(round(part * step))
            pb = a + int(round((part + 1) * step)) - 1
            if pa <= pb:
                out.append((pa, min(pb, b)))
    return sorted(out)


def _match_glyph(grid, h, c0, c1):
    rr = [r for r in range(h) if any(grid[r][c] for c in range(c0, c1 + 1))]
    if not rr:
        return "", -1
    glyph = _norm5x7(grid, rr[0], rr[-1], c0, c1)
    best, best_name = -1, ""
    for name, tmpl in _TMPL.items():
        same = sum(1 for r in range(7) for c in range(5) if glyph[r][c] == tmpl[r][c])
        if same > best:
            best, best_name = same, name
    return best_name, best


def _ocr_binarized(grid, w, h):
    groups = _split_columns(grid, w, h)
    if not (2 <= len(groups) <= 6):
        return "", -1.0
    names, scores = [], []
    for c0, c1 in groups:
        name, score = _match_glyph(grid, h, c0, c1)
        if not name or score < 30:  # 35 格里至少 30 格一致，否则视为失败
            return "", -1.0
        names.append(name)
        scores.append(score)
    return "".join(names), sum(scores) / len(scores)


def solve_image_captcha(data):
    """识别 2~6 位数字/字母验证码；失败返回 ""（由调用方决定回退到真人）。

    成熟流程：多个候选二值化阈值（Otsu / 双峰中点）各自完整跑一遍，
    取平均匹配分最高的结果；单个字形分不够即整体失败，不猜。
    """
    try:
        w, h, px = _gray(data)
    except ValueError:
        return ""
    mn, mx = min(px), max(px)
    if mx - mn < 16:  # 几乎没有前景
        return ""
    thrs = {_otsu(px), int((mn + mx) / 2)}
    best_code, best_score = "", -1.0
    for thr in thrs:
        grid = _binarize(px, w, h, thr)
        code, score = _ocr_binarized(grid, w, h)
        if code and score > best_score:
            best_code, best_score = code, score
    return best_code
