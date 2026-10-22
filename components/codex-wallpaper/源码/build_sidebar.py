from pathlib import Path
import base64, hashlib, json

source = Path(__file__).resolve().parent
project = source.parent
palette_svg = (source / 'sidebar.palette.svg').read_text(encoding='utf-8')
(source / 'SidebarPalette.svg').write_text(palette_svg, encoding='utf-8')
preview_path = project / '预览' / 'preview.html'
if preview_path.is_file():
    preview = preview_path.read_text(encoding='utf-8-sig')
    start_marker, end_marker = '<!--SIDEBAR_PALETTE_START-->', '<!--SIDEBAR_PALETTE_END-->'
    if start_marker in preview:
        start = preview.index(start_marker)
        end = preview.index(end_marker, start) + len(end_marker)
        preview = preview[:start] + preview[end:]
    preview = preview.replace('<body>', '<body>' + start_marker + palette_svg + end_marker, 1)
    preview_path.write_text(preview, encoding='utf-8')
images = {
    '__REPEAT_IMAGE__': project / '图片' / '现代舞_上下重复.jpg',
    '__ENDING_IMAGE__': project / '图片' / '现代舞_底端结束.jpg',
}
css = (source / 'sidebar.template.css').read_text(encoding='utf-8')
manifest = {}
for marker, path in images.items():
    data = path.read_bytes()
    css = css.replace(marker, 'data:image/jpeg;base64,' + base64.b64encode(data).decode())
    manifest[path.name] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
if '__REPEAT_IMAGE__' in css or '__ENDING_IMAGE__' in css:
    raise RuntimeError('Unresolved sidebar image')
(source / 'Sidebar.css').write_text(css, encoding='utf-8')
manifest.update({
    'css_sha256': hashlib.sha256(css.encode()).hexdigest(),
    'repeat_mode': 'repeat-y above one viewport-bottom ending image',
    'image_geometry': {'repeat': [864, 1084], 'ending': [864, 842]},
    'opacity': {'sidebar_backgrounds': .30},
    'display_color_filter': 'near-white background mask to #fffdd7; original other colors preserved',
})
(project / '记录').mkdir(exist_ok=True)
(project / '记录' / 'sidebar-manifest.json').write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
print('Built Sidebar.css; both original images retained.')
