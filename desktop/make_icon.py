"""生成桌面客户端图标（蓝色圆角方块 + 白色「作」）。

为什么要脚本而不是直接放图片：
    仓库里放二进制图标不方便 review，脚本可复现，改颜色/文字也只要改几行。

用法：
    python desktop/make_icon.py
产物：
    desktop/app.ico（多尺寸：16/32/48/64/128/256）
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
BG = (79, 124, 255)  # 和前端主题色 --accent 一致
FONT = "C:/Windows/Fonts/msyhbd.ttc"  # 微软雅黑 Bold
SIZES = [16, 32, 48, 64, 128, 256]


def draw(size: int) -> Image.Image:
    # 4 倍超采样后缩小，边缘更干净
    scale = 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    radius = int(s * 0.22)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=BG)

    font = ImageFont.truetype(FONT, int(s * 0.52))
    box = d.textbbox((0, 0), "作", font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text(((s - w) / 2 - box[0], (s - h) / 2 - box[1]), "作", font=font, fill="white")

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    frames = [draw(n) for n in SIZES]
    out = HERE / "app.ico"
    frames[-1].save(out, format="ICO", sizes=[(n, n) for n in SIZES])
    print(f"已生成 {out}（{out.stat().st_size} 字节，尺寸 {SIZES}）")


if __name__ == "__main__":
    main()
