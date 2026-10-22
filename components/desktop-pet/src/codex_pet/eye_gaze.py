"""Small eye-texture gaze inside the SAME verified front-facing frame.

Fixed head/face/eye outlines, zero whole-character transformation. Only two
opaque inner-iris rectangles are sampled; eyelids, eyebrows and sclera outline
are outside these rectangles. Smooth displacement falls to zero at each edge.
No PNG writes, invented face pixels or independent generated direction art.
"""
from __future__ import annotations
import math
import numpy as np
from PySide6.QtGui import QImage,QPainter

# Reviewed SOURCE coordinates on r00_c00; exclude lashes and iris outer lines.
EYE_REGIONS=((320,229,351,254),(438,243,464,269))
MAX_SHIFT=(3,2)

def gaze_offset(index: int):
    if type(index) is not int or not 0<=index<16:
        raise ValueError('gaze index must be one of the sixteen clockwise directions')
    angle=index*math.pi/8
    return round(MAX_SHIFT[0]*math.sin(angle)),round(-MAX_SHIFT[1]*math.cos(angle))

def _ramp(v):
    v=np.clip(v,0,1);return v*v*(3-2*v)

def eye_patch(a,dx,dy):
    """Inverse texture sampling, identity at patch boundary, ≤3/2 px inside."""
    h,w=a.shape[:2];yy,xx=np.mgrid[:h,:w].astype(np.float64)
    weight=_ramp(np.minimum(xx,w-1-xx)/5)*_ramp(np.minimum(yy,h-1-yy)/4)
    sx=np.clip(xx-dx*weight,0,w-1);sy=np.clip(yy-dy*weight,0,h-1)
    x0=sx.astype(int);y0=sy.astype(int);x1=np.minimum(x0+1,w-1);y1=np.minimum(y0+1,h-1)
    fx=(sx-x0)[...,None];fy=(sy-y0)[...,None]
    rgb=(a[y0,x0,:3]*(1-fx)*(1-fy)+a[y0,x1,:3]*fx*(1-fy)
         +a[y1,x0,:3]*(1-fx)*fy+a[y1,x1,:3]*fx*fy)
    result=a.copy();result[...,:3]=np.rint(rgb).astype(np.uint8)
    return result

def render_gaze(front: QImage,index: int) -> QImage:
    if front.size().width()!=768 or front.size().height()!=832:
        raise ValueError('gaze requires the unscaled verified source canvas')
    dx,dy=gaze_offset(index)
    # Keep the original PREMULTIPLIED image outside the opaque eye patches;
    # avoid full-image unpremultiply round trips altering translucent edges.
    result=front.copy()
    painter=QPainter(result);painter.setCompositionMode(QPainter.CompositionMode_Source)
    try:
        for x0,y0,x1,y1 in EYE_REGIONS:
            patch=front.copy(x0,y0,x1-x0,y1-y0).convertToFormat(QImage.Format_RGBA8888)
            a=np.frombuffer(patch.constBits(),np.uint8).reshape(y1-y0,x1-x0,4).copy()
            if not np.all(a[...,3]==255):raise ValueError('reviewed iris patch must stay fully opaque')
            moved=eye_patch(a,dx,dy)
            image=QImage(moved.data,x1-x0,y1-y0,(x1-x0)*4,QImage.Format_RGBA8888).copy()
            painter.drawImage(x0,y0,image)
    finally:painter.end()
    return result
