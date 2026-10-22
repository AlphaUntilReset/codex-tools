"""Deterministic native rig compositor shared by authoring and release audit.

Head transforms are rigid (unit scale). Bodies and occluders are distinct
reviewed layers, so preserving a gait never licenses resizing the head.
"""
from pathlib import Path
import numpy as np
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage,QPainter,QTransform

def rgba(q):
    q=q.convertToFormat(QImage.Format_RGBA8888)
    return np.frombuffer(q.constBits(),np.uint8).reshape(q.height(),q.width(),4).copy()

def compose(directory:Path,spec:dict):
    body=QImage(str(directory/spec['body'])).convertToFormat(QImage.Format_ARGB32_Premultiplied)
    head=QImage(str(directory/spec['head'])).convertToFormat(QImage.Format_ARGB32_Premultiplied)
    if body.isNull() or head.isNull():raise ValueError('missing rig layer')
    pivot=spec['pivot_px'];dx,dy=spec['translation_px'];angle=spec['angle_degrees']
    if spec.get('scale')!=1:raise ValueError('head scale must be exactly one')
    transform=QTransform().translate(pivot[0]+dx,pivot[1]+dy).rotate(angle).translate(-pivot[0],-pivot[1])
    if spec.get('projection_degrees'):
        yaw,pitch=spec['projection_degrees']
        transform=(QTransform().translate(pivot[0]+dx,pivot[1]+dy).rotate(angle)
                   .rotate(yaw,Qt.YAxis).rotate(pitch,Qt.XAxis).translate(-pivot[0],-pivot[1]))
    head_canvas=QImage(768,832,QImage.Format_ARGB32_Premultiplied);head_canvas.fill(Qt.transparent)
    painter=QPainter(head_canvas);painter.setRenderHint(QPainter.SmoothPixmapTransform)
    painter.setTransform(transform);painter.drawImage(0,0,head);painter.end()
    for braid in spec.get('braids',[]):
        part=QImage(str(directory/braid['file'])).convertToFormat(QImage.Format_ARGB32_Premultiplied)
        if part.isNull():raise ValueError('missing braid layer')
        x,y=braid['pivot_px']
        local=QTransform().translate(x,y).rotate(braid['angle_degrees']).translate(-x,-y)
        # Qt composes local first, then parent head transform.
        painter=QPainter(head_canvas);painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setTransform(local*transform);painter.drawImage(0,0,part);painter.end()
    frame=body.copy();painter=QPainter(frame);painter.drawImage(0,0,head_canvas)
    fore=QImage(str(directory/spec['foreground'])).convertToFormat(QImage.Format_ARGB32_Premultiplied)
    if fore.isNull():raise ValueError('missing foreground layer')
    painter.drawImage(0,0,fore);painter.end()
    pixels=rgba(frame)
    # Avoid introducing a premultiply roundtrip into untouched straight pixels.
    h=rgba(head_canvas);f=np.asarray(Image.open(directory/spec['foreground']).convert('RGBA'))
    b=np.asarray(Image.open(directory/spec['body']).convert('RGBA'))
    pixels[(h[...,3]==0)&(f[...,3]==0)]=b[(h[...,3]==0)&(f[...,3]==0)]
    if angle==0 and dx==0 and dy==0 and not any(spec.get('projection_degrees',[])):
        raw=np.asarray(Image.open(directory/spec['head']).convert('RGBA'))
        mask=(raw[...,3]>0)&(f[...,3]==0)
        pixels[mask]=raw[mask]
        for braid in spec.get('braids',[]):
            if braid['angle_degrees']==0:
                raw=np.asarray(Image.open(directory/braid['file']).convert('RGBA'))
                mask=(raw[...,3]>0)&(f[...,3]==0);pixels[mask]=raw[mask]
    return pixels,{'head':h,'foreground':f,'body':b,'transform':transform}
