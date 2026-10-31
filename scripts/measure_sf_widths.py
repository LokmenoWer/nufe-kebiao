"""量 iOS 系统字体（SF Pro 半粗体、小字号光学尺寸）的字宽和行高，给小组件脚本的 textWidth 用。

Scriptable 量不了文字，脚本只能按每个字符的字宽估；这里从 macOS 自带的同一套字体里读出真实的前进宽度，
输出一段 JS，贴进 scriptable/nufe-kebiao-widget.js 的 SF_WIDTHS。用法：python3 scripts/measure_sf_widths.py
需要 fontTools（pip install fonttools）。
"""
from __future__ import annotations

import glob
import string

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

SF = "/System/Library/Fonts/SFNS.ttf"
PINGFANG = glob.glob("/System/Library/AssetsV2/*/*/AssetData/PingFang.ttc")
CHARS = string.digits + string.ascii_letters + " :.-–+()/·"
WEIGHT = 590   # 半粗体：小组件里最宽的字重，按它估只会估宽
OPSZ = 17      # 10pt 上下的字用最小的光学尺寸（字形更宽）


def main() -> None:
    font = instantiateVariableFont(TTFont(SF), {"wght": WEIGHT, "opsz": OPSZ, "wdth": 100, "GRAD": 400})
    cmap, hmtx, upm = font.getBestCmap(), font["hmtx"], font["head"].unitsPerEm
    widths = {c: round(hmtx[cmap[ord(c)]][0] / upm, 3) for c in CHARS if ord(c) in cmap}
    hhea = font["hhea"]
    print(f"// SF 行高 {(hhea.ascent - hhea.descent + hhea.lineGap) / upm:.3f} em")
    if PINGFANG:
        pf = TTFont(PINGFANG[0], fontNumber=0)
        h, u = pf["hhea"], pf["head"].unitsPerEm
        print(f"// 苹方行高 {(h.ascent - h.descent + h.lineGap) / u:.3f} em")
    body = ", ".join(f'"{c}": {w}' if not c.isalnum() else f"{c}: {w}" if not c.isdigit() else f'"{c}": {w}'
                     for c, w in widths.items())
    print(f"const SF_WIDTHS = {{ {body} }};")


if __name__ == "__main__":
    main()
