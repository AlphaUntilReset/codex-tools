"""Display-only registration and gentle palette matching of untouched 0.4.7 art.

No layer extraction, warping, resizing, generated pixels or file writes. Geometry
is one integer translation for stationary poses; alpha and intentional gait/jump
are preserved. RGB deltas retain the original shading, with smooth material
selection instead of abrupt threshold edges. This is not an anatomy repair.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtGui import QImage

ALGORITHM = '047-integer-sole-soft-palette-v1'
REVIEWED_PROFILE_SHA = 'ac49c03fced2f2b1ba8077fb01d86c67e0d77cb77ffc0f2db3c25b8ded6b86b9'
REFERENCE_PNG_SHA = 'f5532f2c426b919c3427da182507466c5ebb7dd089e8edae472327369b662c08'
REVIEWED_CACHE_INDEX_SHA = '5fb519fb29e5a258f08eb6cf5b3fa748ed1a97203fd0b48c333f26334f554e80'
MOVING_ROWS = frozenset((1, 2, 4))
PALETTE_NAMES = frozenset(('shirt', 'mint', 'lilac', 'hair'))
MAX_OFFSET = 24
MAX_DELTA = 24
DEFAULT_REGIONS = (330,580,330,590,210)
# Source-space garment bands reviewed on the five ORIGINAL jumping poses.
# The crouched head is lower; fixed standing bands would mistake hair for shirt.
JUMP_REGIONS = ((535,635,520,700,430), (395,540,230,590,300),
                (300,435,200,500,200), (365,510,220,580,280),
                (360,590,330,620,300))


def regions_for_cell(row, col):
    return JUMP_REGIONS[col] if row == 4 else DEFAULT_REGIONS


def sole_anchor(a: np.ndarray) -> np.ndarray:
    """Midpoint of both sole outlines, and bottom row; independent of shoe fill.

    Fixed foot ROI excludes the handbag. Only stationary original poses use
    this anchor: applying it to running would erase gait, to jumping altitude.
    """
    yy, xx = np.nonzero(a[760:825, 250:490, 3] > 200)
    if len(xx) < 300:
        raise ValueError('stationary sole landmark missing')
    bottom = int(yy.max()) + 760
    _, xx = np.nonzero(a[bottom-15:bottom+1, 250:490, 3] > 200)
    if len(xx) < 100 or xx.min() >= 120 or xx.max() <= 120:
        raise ValueError('both stationary shoes required')
    return np.array([(int(xx.min()) + int(xx.max())) / 2 + 250, bottom])


def _rise(x, low, high):
    t = np.clip((x-low)/(high-low), 0, 1)
    return t*t*(3-2*t)


def _band(x, low, high, feather=6):
    return _rise(x, low-feather, low) * (1-_rise(x, high, high+feather))


def palette_weights(a, regions=DEFAULT_REGIONS):
    """Continuous weights, fixed by algorithm, never learned from a new frame.

    Saturated interiors receive the measured delta. Dark lines, skin, whites,
    low-alpha edges and transparent pixels stay unchanged. Feathering acts on
    color values, without blurring textures or moving any pixel.
    """
    r, g, b = (a[..., i].astype(np.float32) for i in range(3))
    y = np.arange(a.shape[0], dtype=np.float32)[:, None]
    opacity = _rise(a[..., 3].astype(np.float32), 200, 240)
    shirt_low,shirt_high,lilac_low,lilac_high,hair_high = regions
    garment = _band(y, shirt_low, shirt_high, 12)
    weights = {
        'shirt': _rise(r,194,200) * _band(g,155,240) * _band(b,70,190)
                 * _rise(r-g,12,18) * _rise(g-b,24,30) * garment,
        'mint': _rise(g,139,145) * _rise(b,134,140) * _rise(g-r,19,25)
                * (1-_rise(np.abs(g-b),45,51)),
        'lilac': _rise(r,134,140) * _rise(b,144,150) * _rise(r-g,4,10)
                 * _rise(b-g,14,20) * _band(y,lilac_low,lilac_high,12),
        'hair': _rise(r,209,215) * _rise(g,169,175) * _band(b,110,220)
                * _rise(r-g,4,10) * _rise(g-b,9,15) * (1-_rise(y,hair_high,hair_high+12)),
    }
    return {name: weight * opacity for name,weight in weights.items()}


def palette_colors(a, regions=DEFAULT_REGIONS):
    return {n:np.median(a[...,:3][w>.999],axis=0).tolist()
            for n,w in palette_weights(a,regions).items() if (w>.999).sum()>100}


def correct_palette(image: QImage, deltas: dict, regions=DEFAULT_REGIONS) -> QImage:
    if not any(any(v for v in delta) for delta in deltas.values()):
        return image
    rgba = image.convertToFormat(QImage.Format_RGBA8888)
    a = np.frombuffer(rgba.constBits(), np.uint8).reshape(
        rgba.height(), rgba.width(), 4).copy()
    change = np.zeros((*a.shape[:2], 3), dtype=np.float32)
    weights = palette_weights(a,regions)
    for name,delta in deltas.items():
        change += weights[name][...,None] * np.asarray(delta,dtype=np.float32)
    a[...,:3] = np.clip(np.rint(a[...,:3].astype(np.float32)+change),0,255).astype(np.uint8)
    return QImage(a.data,rgba.width(),rgba.height(),rgba.width()*4,
                  QImage.Format_RGBA8888).copy()
