"""Validate repaired motion groups independently and keep unsafe art out.

Required task animations degrade to the accepted static mother when a group
is damaged, while its manual entry is hidden. State/bridge semantics remain.
"""
import hashlib,json,logging
from pathlib import Path
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

MOTHER_SHA='f5532f2c426b919c3427da182507466c5ebb7dd089e8edae472327369b662c08'
LAYOUT={'idle':(0,6),'running-right':(1,8),'running-left':(2,8),'waving':(3,4),
        'jumping':(4,5),'failed':(5,8),'waiting':(6,6),'running':(7,6),'review':(8,6),
        'yawn':(12,8),'thinking':(14,8),'look':(None,16)}

def cells(name):
    row,count=LAYOUT[name]
    return [(9+i//8,i%8) if row is None else (row,i) for i in range(count)]

def load_motion_pack(directory:Path,cache_dir:Path):
    log=logging.getLogger('codex_pet.motion')
    mother=cache_dir/'r00_c00.png'
    if hashlib.sha256(mother.read_bytes()).hexdigest()!=MOTHER_SHA:
        raise ValueError('accepted motion fallback mother changed')
    # Fail closed even when the optional manifest is completely missing.
    paths={c:mother for n in LAYOUT for c in cells(n)};valid=set()
    try:
        metadata=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
        if metadata['cell_size_px']!=[768,832] or metadata['mother_sha256']!=MOTHER_SHA:
            raise ValueError('invalid pack reference')
        schema=metadata.get('schema_version',1)
        if schema not in (1,2):raise ValueError('unsupported motion pack')
    except (OSError,ValueError,KeyError,TypeError):
        log.warning('动作一致性扩展包不可用，使用静态母版')
        return paths,valid
    checked={}
    def check(relative,expected):
        p=(directory/relative).resolve()
        if not p.is_relative_to(directory.resolve()):raise ValueError('layer path escapes pack')
        if p not in checked:
            if hashlib.sha256(p.read_bytes()).hexdigest()!=expected:raise ValueError('motion hash mismatch')
            q=QImage(str(p))
            if q.isNull() or q.size()!=QSize(768,832) or not q.hasAlphaChannel():raise ValueError('bad motion image')
            checked[p]=expected
        elif checked[p]!=expected:raise ValueError('inconsistent layer hash')
        return p
    for name in LAYOUT:
        try:
            frames=metadata['groups'][name]['frames']
            expected=cells(name)
            if len(frames)!=len(expected):raise ValueError('bad motion count')
            group={}
            for entry,(row,col) in zip(frames,expected):
                filename=f'r{row:02d}_c{col:02d}.png'
                if entry['file']!=filename or (entry['row'],entry['column'])!=(row,col):raise ValueError('bad motion cell')
                q=metadata['recipes'][filename]
                if q['scale']!=1:raise ValueError('head was scaled')
                if not -8<=q['angle_degrees']<=8:raise ValueError('head angle out of range')
                dx,dy=q['translation_px']
                if abs(dx)>(48 if row in (1,2) else 32) or not (-16<=dy<=(280 if row==4 else 32)):raise ValueError('motion translation out of range')
                if schema==2:
                    if 'native_projection' in q:raise ValueError('whole original frame replacement forbidden')
                    yaw,pitch=q.get('projection_degrees',[0,0])
                    if abs(yaw)>12 or not -7<=pitch<=22 or ((yaw or pitch) and name not in ('failed','look')):
                        raise ValueError('undeclared projected head motion')
                for key in ('head','body','foreground'):
                    relative=q[key];check(relative,metadata['layers'][relative])
                if (schema==1 and (row,col) in ((3,1),(3,2),(7,2),(4,1),(4,2),(4,3))) or q.get('joint_body'):
                    for key in ('joint_body','joint_fore') if schema==1 else tuple(k for k in ('joint_body','joint_fore') if k in q):
                        relative=q[key];check(relative,metadata['layers'][relative])
                for braid in q.get('braids',[]):
                    relative=braid['file'];check(relative,metadata['layers'][relative])
                group[row,col]=check(filename,entry['sha256'])
            paths.update(group);valid.add(name)
        except (OSError,ValueError,KeyError,TypeError):
            log.warning('动作组校验失败，使用静态母版: %s',name)
    log.info('启用分层一致性动作: %d 组 / %d 帧',len(valid),sum(len(cells(n)) for n in valid))
    return paths,valid
