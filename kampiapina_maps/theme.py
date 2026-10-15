"""Theme loading. Themes live in kampiapina_maps/themes/<name>.toml or any .toml path."""

import tomllib
from pathlib import Path

THEMES_DIR = Path(__file__).parent / "themes"
FONTS_DIR = Path(__file__).parent / "fonts"


def rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))


def load_theme(name_or_path: str) -> dict:
    path = Path(name_or_path)
    if not path.suffix:
        path = THEMES_DIR / f"{name_or_path}.toml"
    if not path.exists():
        available = ", ".join(p.stem for p in THEMES_DIR.glob("*.toml"))
        raise ValueError(f"Theme not found: {name_or_path} (available: {available})")
    with open(path, "rb") as f:
        theme = tomllib.load(f)
    # `extends = "light"` inherits everything not overridden.
    base = theme.pop("extends", None)
    return _merge(load_theme(base), theme) if base else theme


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def font_path(filename: str) -> Path:
    path = Path(filename)
    return path if path.is_absolute() else FONTS_DIR / filename
