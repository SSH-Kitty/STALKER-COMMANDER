"""Draw the installer's wizard images from COMMANDER's own artwork and palette.

    python packaging/windows/installer/make_images.py

Writes PNGs to packaging/windows/installer/images/ (Inno Setup 6.3+ reads PNG
and picks the size that fits the user's display scaling):

    wizard-large-<w>x<h>.png   Welcome / Finish side panel
    wizard-small-<n>.png       the top-right icon on every other page (transparent)
    app.ico                    Setup's and the uninstaller's icon
    preview-welcome.png        a mock of the Welcome page, for looking at only

Palette = the "black" theme in commander_gui/themes.py (the default).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / "commander_gui" / "assets" / "commander_artwork"
FONT = ROOT / "commander_gui" / "fonts" / "Exo2-Variable.ttf"
ICON = ROOT / "cli" / "stalker-gamma.png"
OUT = Path(__file__).resolve().parent / "images"

BLACK = (0, 0, 0)
CARD = (10, 10, 10)
BORDER = (31, 31, 31)
TEXT = (224, 224, 224)
DIM = (138, 138, 138)
ACCENT = (159, 233, 111)  # #9fe96f
PRIMARY = (95, 181, 72)  # #5fb548
HERO_TOP, HERO_BOTTOM = (79, 166, 60), (111, 201, 90)

# The part of the 3840x1240 hero.jpg used for the side panel: its left edge,
# top edge and height (the width follows from the panel's aspect ratio). This
# frames the apartment block's balconies and brick end wall, with clouds above
# it for the logo to sit on (a smaller HERO_CROP_Y shows more sky).
# Try HERO_CROP_X ~ 1300 for the middle of the block, ~ 0 for the dead trees.
HERO_CROP_X, HERO_CROP_Y, HERO_CROP_H = 1700, 230, 820

# Inno Setup's recommended sizes for the large panel at 100%..250% scaling.
LARGE_SIZES = [(164, 314), (192, 386), (246, 459), (273, 556), (328, 628), (355, 700), (410, 797)]
SMALL_SIZES = [55, 64, 80, 96, 110, 128]


def font(size: int, weight: str = "Bold") -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONT), size)
    try:
        f.set_variation_by_name(weight)
    except (OSError, ValueError):
        pass
    return f


def vertical_fade(width: int, height: int, top: int, bottom: int) -> Image.Image:
    """Alpha mask: 0 -> 255 over the first `top` px, 255 -> 0 over the last `bottom`."""
    mask = Image.new("L", (width, height), 255)
    px = mask.load()
    for y in range(height):
        a = 255
        if y < top:
            a = min(a, int(255 * y / max(top, 1)))
        if y >= height - bottom:
            a = min(a, int(255 * (height - 1 - y) / max(bottom, 1)))
        for x in range(width):
            px[x, y] = a
    return mask


def draw_large(width: int, height: int) -> Image.Image:
    """The tall side panel: logo on top, the two stalkers fading up out of black, footer text."""
    s = 4  # draw at 4x, then downsample for smooth edges
    W, H = width * s, height * s
    img = Image.new("RGB", (W, H), BLACK)

    # Artwork: a slice of hero.jpg - the apartment block under the storm
    # clouds - cut to the panel's own aspect ratio so it fills the whole panel.
    hero = Image.open(ART / "hero.jpg").convert("RGB")
    crop_w = round(HERO_CROP_H * width / height)
    crop = hero.crop((HERO_CROP_X, HERO_CROP_Y, HERO_CROP_X + crop_w, HERO_CROP_Y + HERO_CROP_H))
    art = crop.resize((W, H), Image.LANCZOS)
    art = Image.eval(art, lambda v: int(v * 0.95))
    img.paste(art, (0, 0))
    # Shade the top (behind the logo) and the bottom (behind the footer) into black.
    shade = Image.new("L", (W, H), 0)
    sp = shade.load()
    for y in range(H):
        t = y / H
        a = 0.0
        if t < 0.34:
            a = 0.42 * (1 - t / 0.34) ** 1.4
        if t > 0.80:
            a = max(a, 0.9 * (t - 0.80) / 0.20)
        v = int(255 * a)
        for x in range(W):
            sp[x, y] = v
    img.paste(Image.new("RGB", (W, H), BLACK), (0, 0), shade)

    # Logo, white on black, near the top.
    logo = Image.open(ART / "logo.png").convert("RGBA")
    lw = int(W * 0.88)
    logo = logo.resize((lw, int(logo.height * lw / logo.width)), Image.LANCZOS)
    lx, ly = (W - lw) // 2, int(H * 0.045)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    tint = Image.new("RGBA", logo.size, ACCENT + (0,))
    tint.putalpha(logo.split()[3].point(lambda v: int(v * 0.55)))
    glow.paste(tint, (lx, ly), tint)
    glow = glow.filter(ImageFilter.GaussianBlur(10 * s / 4))
    img.paste(glow, (0, 0), glow)
    img.paste(logo, (lx, ly), logo)

    d = ImageDraw.Draw(img)
    # Accent rule under the logo, like the green underline under the active tab.
    ry = ly + logo.height + int(H * 0.018)
    d.rectangle([int(W * 0.12), ry, int(W * 0.88), ry + max(2, s)], fill=PRIMARY)

    # Footer.
    f1, f2 = font(int(13 * s), "Bold"), font(int(10 * s), "Regular")
    # Setup draws a divider line across the bottom of the panel: keep the text above it.
    d.text((int(W * 0.12), H - int(H * 0.125)), "INSTALLER  v1.4.0", font=f1, fill=ACCENT)
    d.text((int(W * 0.12), H - int(H * 0.080)), "by SSH-Kitty", font=f2, fill=DIM)
    return img.resize((width, height), Image.LANCZOS)


def draw_small(n: int) -> Image.Image:
    """The page-header badge: just the app icon, on a transparent background.

    No tile or border: against the dark header any filled box shows up as a grey
    square, and the icon has to blend into the page.
    """
    icon = Image.open(ICON).convert("RGBA")
    return icon.resize((n, n), Image.LANCZOS)


def draw_preview(panel: Image.Image) -> Image.Image:
    """A mock-up of the Welcome page in COMMANDER's look. Not used by the installer."""
    pw, ph = 1000, 640
    img = Image.new("RGB", (pw, ph), BLACK)
    d = ImageDraw.Draw(img)
    # Title bar, like COMMANDER's custom one.
    d.rectangle([0, 0, pw, 34], fill=(8, 8, 8))
    d.text((14, 8), "STALKER COMMANDER Setup", font=font(14, "Regular"), fill=DIM)
    for i, glyph in enumerate(("-", "[]", "x")):
        d.text((pw - 90 + i * 28, 8), glyph, font=font(14, "Regular"), fill=DIM)
    d.line([0, 34, pw, 34], fill=BORDER)

    panel = panel.resize((int(panel.width * (ph - 35 - 70) / panel.height), ph - 35 - 70), Image.LANCZOS)
    img.paste(panel, (0, 35))
    cx = panel.width + 40
    d.text((cx, 78), "Welcome to STALKER COMMANDER", font=font(30, "Bold"), fill=(240, 240, 240))
    d.rectangle([cx, 126, cx + 70, 128], fill=PRIMARY)
    body = font(16, "Regular")
    y = 150
    for line in (
        "This will install STALKER COMMANDER 1.4.0 on your computer.",
        "",
        "COMMANDER installs, updates, launches and repairs",
        "S.T.A.L.K.E.R. Anomaly with the GAMMA modpack.",
        "",
        "Setup also installs the Visual C++ and DirectX runtimes",
        "the game needs, so nothing else has to be set up by hand.",
    ):
        d.text((cx, y), line, font=body, fill=TEXT)
        y += 26
    # A "card", the way the Dashboard groups things.
    d.rounded_rectangle([cx, 372, pw - 40, 452], radius=8, fill=CARD, outline=BORDER)
    d.text((cx + 18, 384), "Install location", font=font(13, "Bold"), fill=ACCENT)
    d.text((cx + 18, 410), r"C:\Users\You\AppData\Local\Programs\STALKER COMMANDER", font=font(14, "Regular"), fill=TEXT)
    # Footer buttons.
    d.rectangle([0, ph - 70, pw, ph], fill=(6, 6, 6))
    d.line([0, ph - 70, pw, ph - 70], fill=BORDER)
    d.rounded_rectangle([pw - 330, ph - 52, pw - 190, ph - 16], radius=6, fill=(20, 20, 20), outline=(46, 46, 46))
    d.text((pw - 290, ph - 44), "Cancel", font=font(15, "Regular"), fill=TEXT)
    d.rounded_rectangle([pw - 170, ph - 52, pw - 30, ph - 16], radius=6, fill=HERO_BOTTOM, outline=ACCENT)
    d.text((pw - 128, ph - 44), "Install", font=font(15, "Bold"), fill=(12, 19, 10))
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for w, h in LARGE_SIZES:
        draw_large(w, h).save(OUT / f"wizard-large-{w}x{h}.png")
    for n in SMALL_SIZES:
        draw_small(n).save(OUT / f"wizard-small-{n}.png")
    draw_preview(draw_large(*LARGE_SIZES[4])).save(OUT / "preview-welcome.png")
    Image.open(ICON).convert("RGBA").save(
        OUT / "app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    )
    print(f"wrote {len(list(OUT.glob('*.png')))} images to {OUT}")


if __name__ == "__main__":
    main()
