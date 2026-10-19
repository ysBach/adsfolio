#!/usr/bin/env python3
"""Generate deterministic ADSfolio brand logos and multi-platform icon assets.

Produces
--------
- Vector SVG: ``src/adsfolio/gui/assets/adsfolio.svg``
- High-res PNG: ``src/adsfolio/gui/assets/adsfolio.png``
- Windows ICO: ``src/adsfolio/gui/assets/adsfolio.ico``
- macOS ICNS: ``src/adsfolio/gui/assets/adsfolio.icns``
- Documentation static assets: ``docs/_static/adsfolio.svg``, ``docs/_static/adsfolio.png``
"""

from __future__ import annotations

import logging
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

logger = logging.getLogger(__name__)


def generate_svg(size: int = 512) -> str:
    """Return deterministic vector SVG markup for the ADSfolio logo.

    Parameters
    ----------
    size : int, default=512
        Rendered pixel dimension for width and height attributes while
        preserving the 512x512 coordinate viewBox.

    Returns
    -------
    str
        SVG markup text.
    """
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="{size}" height="{size}">
  <defs>
    <!-- Background tile gradient -->
    <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0f172a" />
      <stop offset="100%" stop-color="#1e293b" />
    </linearGradient>

    <!-- Paper gradients -->
    <linearGradient id="paperBack" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#94a3b8" />
      <stop offset="100%" stop-color="#64748b" />
    </linearGradient>
    <linearGradient id="paperMid" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#f1f5f9" />
      <stop offset="100%" stop-color="#cbd5e1" />
    </linearGradient>
    <linearGradient id="paperFront" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#ffffff" />
      <stop offset="100%" stop-color="#e2e8f0" />
    </linearGradient>

    <!-- Magnifier rim, handle and collar -->
    <linearGradient id="magRing" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#38bdf8" />
      <stop offset="50%" stop-color="#0284c7" />
      <stop offset="100%" stop-color="#0369a1" />
    </linearGradient>
    <linearGradient id="magHandle" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#64748b" />
      <stop offset="50%" stop-color="#475569" />
      <stop offset="100%" stop-color="#1e293b" />
    </linearGradient>
    <linearGradient id="magCollar" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#94a3b8" />
      <stop offset="100%" stop-color="#64748b" />
    </linearGradient>

    <!-- Glass lens radial shine -->
    <radialGradient id="lensGlow" cx="35%" cy="30%" r="70%">
      <stop offset="0%" stop-color="#ffffff" stop-opacity="0.70" />
      <stop offset="35%" stop-color="#38bdf8" stop-opacity="0.25" />
      <stop offset="75%" stop-color="#0284c7" stop-opacity="0.12" />
      <stop offset="100%" stop-color="#0369a1" stop-opacity="0.20" />
    </radialGradient>
  </defs>

  <!-- Background squircle tile -->
  <rect x="28" y="28" width="456" height="456" rx="96" fill="url(#bgGrad)" />

  <!-- Stack of Papers -->
  <!-- Back sheet tilted left (-8 deg) -->
  <g transform="rotate(-8 210 250)">
    <rect x="110" y="100" width="200" height="260" rx="14" fill="url(#paperBack)" opacity="0.65" />
  </g>

  <!-- Middle sheet tilted right (+5 deg) -->
  <g transform="rotate(5 220 255)">
    <rect x="120" y="105" width="200" height="260" rx="14" fill="url(#paperMid)" />
    <!-- Document lines -->
    <line x1="145" y1="145" x2="235" y2="145" stroke="#94a3b8" stroke-width="7" stroke-linecap="round" />
    <line x1="145" y1="170" x2="290" y2="170" stroke="#cbd5e1" stroke-width="5" stroke-linecap="round" />
    <line x1="145" y1="193" x2="275" y2="193" stroke="#cbd5e1" stroke-width="5" stroke-linecap="round" />
    <line x1="145" y1="216" x2="260" y2="216" stroke="#cbd5e1" stroke-width="5" stroke-linecap="round" />
  </g>

  <!-- Front sheet upright -->
  <g>
    <rect x="130" y="112" width="205" height="265" rx="14" fill="url(#paperFront)" />
    <!-- Folded corner accent -->
    <path d="M 305 112 L 335 142 L 305 142 Z" fill="#cbd5e1" opacity="0.6" />
    <!-- Header title bar -->
    <rect x="155" y="142" width="100" height="9" rx="4.5" fill="#2563eb" />
    <!-- Document lines -->
    <line x1="155" y1="172" x2="310" y2="172" stroke="#64748b" stroke-width="5.5" stroke-linecap="round" />
    <line x1="155" y1="196" x2="300" y2="196" stroke="#94a3b8" stroke-width="5" stroke-linecap="round" />
    <line x1="155" y1="220" x2="270" y2="220" stroke="#94a3b8" stroke-width="5" stroke-linecap="round" />
    <line x1="155" y1="244" x2="305" y2="244" stroke="#cbd5e1" stroke-width="5" stroke-linecap="round" />
    <line x1="155" y1="268" x2="275" y2="268" stroke="#cbd5e1" stroke-width="5" stroke-linecap="round" />
    <line x1="155" y1="292" x2="295" y2="292" stroke="#cbd5e1" stroke-width="5" stroke-linecap="round" />
    <line x1="155" y1="316" x2="245" y2="316" stroke="#cbd5e1" stroke-width="5" stroke-linecap="round" />
    <line x1="155" y1="340" x2="285" y2="340" stroke="#cbd5e1" stroke-width="5" stroke-linecap="round" />
  </g>

  <!-- Magnifier (overlapping paper stack at lower right) -->
  <g>
    <!-- Handle extending toward bottom-right (-45 deg rotation around center) -->
    <g transform="rotate(-45 285 265)">
      <!-- Collar connecting lens to handle -->
      <rect x="274" y="350" width="22" height="12" rx="4" fill="url(#magCollar)" />
      <!-- Main handle body -->
      <rect x="275" y="360" width="20" height="100" rx="10" fill="url(#magHandle)" />
      <!-- Metallic handle tip -->
      <rect x="276" y="450" width="18" height="10" rx="4" fill="url(#magCollar)" />
    </g>

    <!-- Outer rim -->
    <circle cx="285" cy="265" r="90" fill="none" stroke="url(#magRing)" stroke-width="18" />
    <!-- Inner metallic bevel ring -->
    <circle cx="285" cy="265" r="80" fill="none" stroke="#38bdf8" stroke-width="2.5" opacity="0.7" />

    <!-- Lens glass -->
    <circle cx="285" cy="265" r="79" fill="url(#lensGlow)" />

    <!-- Lens specular highlight arc -->
    <path d="M 230 230 A 64 64 0 0 1 330 230 A 58 56 0 0 0 230 230 Z" fill="#ffffff" opacity="0.55" />
  </g>
</svg>"""


def render_png(svg_content: str, width: int, height: int) -> bytes:
    """Render SVG content into PNG-encoded bytes.

    Parameters
    ----------
    svg_content : str
        SVG markup text.
    width : int
        Width in pixels.
    height : int
        Height in pixels.

    Returns
    -------
    bytes
        PNG image bytes.
    """
    renderer = QSvgRenderer(svg_content.encode("utf-8"))
    img = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    renderer.render(painter, QRectF(0, 0, width, height))
    painter.end()

    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    img.save(buffer, "PNG")
    return bytes(buffer.data())


def build_ico(png_map: dict[int, bytes]) -> bytes:
    """Pack PNG images into Windows ICO binary format.

    Parameters
    ----------
    png_map : dict[int, bytes]
        Mapping from square dimension to PNG bytes.

    Returns
    -------
    bytes
        ICO binary content.
    """
    sizes = sorted(png_map.keys())
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + len(sizes) * 16

    dir_entries = bytearray()
    body = bytearray()

    for size in sizes:
        png_data = png_map[size]
        w = 0 if size >= 256 else size
        h = 0 if size >= 256 else size
        dir_entries.extend(
            struct.pack(
                "<BBBBHHII",
                w,
                h,
                0,
                0,
                1,
                32,
                len(png_data),
                offset,
            )
        )
        offset += len(png_data)
        body.extend(png_data)

    return header + dir_entries + body


def build_icns(png_map: dict[int, bytes]) -> bytes:
    """Pack PNG representations into Apple ICNS binary format.

    Parameters
    ----------
    png_map : dict[int, bytes]
        Mapping from square dimension to PNG bytes.

    Returns
    -------
    bytes
        ICNS binary content.
    """
    ostype_tags = [
        (16, "icp4"),
        (32, "icp5"),
        (64, "icp6"),
        (128, "ic07"),
        (256, "ic08"),
        (512, "ic09"),
        (1024, "ic10"),
    ]

    body = bytearray()
    for size, tag in ostype_tags:
        if size in png_map:
            data = png_map[size]
            body.extend(tag.encode("ascii"))
            body.extend(struct.pack(">I", len(data) + 8))
            body.extend(data)

    header = b"icns" + struct.pack(">I", len(body) + 8)
    return header + body


def main() -> None:
    """Generate all ADSfolio brand assets."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    _app = QApplication.instance() or QApplication(sys.argv)

    root = Path(__file__).resolve().parents[1]
    assets_dir = root / "src" / "adsfolio" / "gui" / "assets"
    docs_static_dir = root / "docs" / "_static"

    assets_dir.mkdir(parents=True, exist_ok=True)
    docs_static_dir.mkdir(parents=True, exist_ok=True)

    svg_content = generate_svg(512)

    (assets_dir / "adsfolio.svg").write_text(svg_content, encoding="utf-8")
    (docs_static_dir / "adsfolio.svg").write_text(svg_content, encoding="utf-8")

    dimensions = [16, 32, 48, 64, 128, 256, 512, 1024]
    png_map = {}
    for dim in dimensions:
        png_map[dim] = render_png(svg_content, dim, dim)

    (assets_dir / "adsfolio.png").write_bytes(png_map[512])
    (docs_static_dir / "adsfolio.png").write_bytes(png_map[512])

    ico_bytes = build_ico({s: png_map[s] for s in [16, 32, 48, 64, 128, 256]})
    (assets_dir / "adsfolio.ico").write_bytes(ico_bytes)

    icns_bytes = build_icns(png_map)
    (assets_dir / "adsfolio.icns").write_bytes(icns_bytes)

    logger.info("Generated logo assets in %s and %s", assets_dir, docs_static_dir)
    for item in sorted(assets_dir.iterdir()):
        logger.info("  %s (%d bytes)", item.name, item.stat().st_size)


if __name__ == "__main__":
    main()
