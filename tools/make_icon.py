"""アプリのアイコン（assets/rocket.ico, rocket.png）を作る。

    python tools/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
S = 512  # 下書きの大きさ（縮小してなめらかにする）


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 背景の丸
    d.ellipse((16, 16, S - 16, S - 16), fill=(21, 101, 192, 255))
    d.ellipse((40, 40, S - 40, S - 40), outline=(144, 202, 249, 255), width=10)

    # 斜め 45° のロケット: 縦向きに描いてから回す
    r = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    g = ImageDraw.Draw(r)
    cx, w = S // 2, 70
    top, body_top, body_bot = 70, 175, 380
    # 炎
    g.polygon([(cx - 40, body_bot), (cx + 40, body_bot), (cx, body_bot + 110)], fill=(255, 152, 0, 255))
    g.polygon([(cx - 22, body_bot), (cx + 22, body_bot), (cx, body_bot + 70)], fill=(255, 235, 59, 255))
    # フィン
    g.polygon([(cx - w // 2, body_bot - 90), (cx - w // 2 - 70, body_bot + 20), (cx - w // 2, body_bot)],
              fill=(198, 40, 40, 255))
    g.polygon([(cx + w // 2, body_bot - 90), (cx + w // 2 + 70, body_bot + 20), (cx + w // 2, body_bot)],
              fill=(198, 40, 40, 255))
    # 胴体とノーズ
    g.rectangle((cx - w // 2, body_top, cx + w // 2, body_bot), fill=(245, 245, 245, 255))
    g.pieslice((cx - w // 2, top, cx + w // 2, body_top + (body_top - top)), 180, 360, fill=(229, 57, 53, 255))
    g.rectangle((cx - w // 2, (top + body_top) // 2 + 30, cx + w // 2, body_top), fill=(229, 57, 53, 255))
    # 窓
    g.ellipse((cx - 22, 215, cx + 22, 259), fill=(66, 165, 245, 255), outline=(13, 71, 161, 255), width=6)
    # 中央のフィン
    g.rectangle((cx - 8, body_bot - 70, cx + 8, body_bot + 15), fill=(183, 28, 28, 255))
    r = r.rotate(-40, resample=Image.BICUBIC, center=(S // 2, S // 2))
    img.alpha_composite(r)
    return img


def main():
    out = ROOT / "assets"
    out.mkdir(exist_ok=True)
    img = draw()
    img.resize((256, 256), Image.LANCZOS).save(out / "rocket.png")
    img.save(out / "rocket.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("作成しました:", out / "rocket.ico")


if __name__ == "__main__":
    main()
