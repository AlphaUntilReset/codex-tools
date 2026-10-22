"""Reference-based display color management, without changing source artwork or alpha."""
import numpy as np
from PySide6.QtGui import QImage

def material_masks(a):
    r,g,b=(a[...,i].astype(np.int16) for i in range(3))
    yy=np.arange(a.shape[0])[:,None]
    opaque=a[...,3]>240
    return {
        'shirt': opaque & (r>200)&(g>155)&(g<240)&(b>70)&(b<190)&(r-g>18)&(g-b>30)&(yy>330)&(yy<580),
        'mint': opaque & (g>145)&(b>140)&(r<g-25)&(np.abs(g-b)<45),
        'lilac': opaque & (r>140)&(b>150)&(g<r-10)&(b>g+20)&(yy>330)&(yy<590),
        'hair': opaque & (r>215)&(g>175)&(b>110)&(b<220)&(r-g>10)&(g-b>15)&(yy<210),
    }

def material_colors(a):
    return {name:np.median(a[...,:3][mask],axis=0).tolist() for name,mask in material_masks(a).items() if mask.sum()>100}

def apply_profile(image: QImage, profile: dict) -> QImage:
    """Apply small measured material deltas before the single premultiply operation."""
    rgba=image.convertToFormat(QImage.Format_RGBA8888)
    a=np.frombuffer(rgba.constBits(),dtype=np.uint8).reshape(image.height(),image.width(),4).copy()
    # Use the same masks used to measure reference colors; never touch contours or alpha.
    masks=material_masks(a)
    rgb=a[...,:3].astype(np.int16)
    for name,delta in profile.items():
        mask=masks.get(name)
        if mask is not None:
            rgb[mask]=np.clip(rgb[mask]+np.asarray(delta,dtype=np.int16),0,255)
    a[...,:3]=rgb.astype(np.uint8)
    return QImage(a.data,image.width(),image.height(),image.width()*4,QImage.Format_RGBA8888).copy()
