"""보험메이트 로고 SVG 생성: 캐릭터(방패) + 텍스트 로고(Pretendard 글자를 path로 변환).

이미지 속 글자에는 웹 글꼴이 적용되지 않으므로 Pretendard 글자 모양을 선(path)으로 따서 넣는다.
실행: fonttools가 설치된 파이썬으로
    python assets/make_logos.py <Pretendard-ExtraBold.otf·SemiBold.otf가 있는 폴더> assets
"""
import sys
from pathlib import Path
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen

FONT_DIR, OUT = Path(sys.argv[1]), Path(sys.argv[2])
OUT.mkdir(exist_ok=True)
NAVY, NAVY2, BLUE, GOLD, INK = "#123A7A", "#1F4FA3", "#2F6FED", "#F2B33D", "#0E1F3D"


def text_path(font_file, text, size, x, y, tracking=0):
    """글자를 SVG path d 문자열로. (x, y)는 글자 기준선 왼쪽."""
    font = TTFont(font_file)
    glyphs = font.getGlyphSet()
    cmap = font.getBestCmap()
    scale = size / font["head"].unitsPerEm
    pen = SVGPathPen(glyphs)
    cursor = x
    for ch in text:
        name = cmap[ord(ch)]
        tp = TransformPen(pen, (scale, 0, 0, -scale, cursor, y))
        glyphs[name].draw(tp)
        cursor += glyphs[name].width * scale + tracking
    return pen.getCommands(), cursor


def character(prefix=""):
    """방패 모양 캐릭터. viewBox 0 0 120 120 기준."""
    g = f"{prefix}g"
    return f'''<defs><linearGradient id="{g}" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="{NAVY2}"/><stop offset="1" stop-color="{NAVY}"/></linearGradient></defs>
  <path d="M60 10 C76 18 92 20 104 20 C104 64 92 94 60 112 C28 94 16 64 16 20 C28 20 44 18 60 10 Z" fill="url(#{g})"/>
  <path d="M27 30 C27 46 30 58 35 68" fill="none" stroke="#FFFFFF" stroke-opacity=".22" stroke-width="5" stroke-linecap="round"/>
  <ellipse cx="44" cy="60" rx="9.5" ry="11" fill="#FFFFFF"/><ellipse cx="76" cy="60" rx="9.5" ry="11" fill="#FFFFFF"/>
  <circle cx="46" cy="62" r="5.2" fill="{INK}"/><circle cx="78" cy="62" r="5.2" fill="{INK}"/>
  <circle cx="47.8" cy="59.6" r="1.8" fill="#FFFFFF"/><circle cx="79.8" cy="59.6" r="1.8" fill="#FFFFFF"/>
  <ellipse cx="33" cy="76" rx="6.5" ry="4" fill="#FF8FA3" fill-opacity=".75"/><ellipse cx="87" cy="76" rx="6.5" ry="4" fill="#FF8FA3" fill-opacity=".75"/>
  <path d="M53 77 Q60 84 67 77" fill="none" stroke="#FFFFFF" stroke-width="3.6" stroke-linecap="round"/>
  <circle cx="60" cy="13" r="10.5" fill="{GOLD}" stroke="#FFFFFF" stroke-width="2.5"/>
  <path d="M60 8 V18 M55 13 H65" stroke="#FFFFFF" stroke-width="3" stroke-linecap="round"/>'''


# 1) 캐릭터 로고 (정사각형, 아이콘·파비콘·채팅 프로필용)
(OUT / "logo_character.svg").write_text(
    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" width="120" height="120">\n  {character("c")}\n</svg>\n',
    encoding="utf-8")

# 2) 텍스트 로고 (캐릭터 + 보험메이트 + 영문). 높이 80 기준
eb, sb = FONT_DIR / "Pretendard-ExtraBold.otf", FONT_DIR / "Pretendard-SemiBold.otf"
d1, end1 = text_path(eb, "보험", 40, 84, 50, tracking=-1)
d2, end2 = text_path(eb, "메이트", 40, end1, 50, tracking=-1)
d3, end3 = text_path(sb, "BOHEOM MATE", 11.5, 86, 70, tracking=2.6)
width = int(max(end2, end3) + 6)
(OUT / "logo_text.svg").write_text(f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 80" width="{width}" height="80">
  <g transform="translate(2 2) scale(0.633)">
  {character("t")}
  </g>
  <path d="{d1}" fill="{NAVY}"/>
  <path d="{d2}" fill="{BLUE}"/>
  <path d="{d3}" fill="#6B7A90"/>
</svg>
''', encoding="utf-8")
print("width", width)
