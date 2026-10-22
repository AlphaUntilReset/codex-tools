"""Build local wallpaper resources from images supplied by their owner."""
import argparse
import base64
from pathlib import Path

SOURCE = Path(__file__).resolve().parent


def prepare(background, repeat, ending, output):
    inputs = [(Path(background), b'\x89PNG\r\n\x1a\n', 'background PNG'),
              (Path(repeat), b'\xff\xd8\xff', 'repeating sidebar JPEG'),
              (Path(ending), b'\xff\xd8\xff', 'bottom sidebar JPEG')]
    data = []
    for image, signature, label in inputs:
        if not image.is_file():
            raise ValueError(f'Supply an existing {label}: {image}')
        image_data = image.read_bytes()
        if not image_data.startswith(signature):
            raise ValueError(f'Supply a correctly encoded {label}; renaming an extension does not convert an image.')
        data.append(image_data)
    output = Path(output).resolve()
    generated = {'SunFaded.png', 'SunFaded.css', 'Sidebar.css', 'SidebarPalette.svg'}
    if any(image.resolve() == output / name for image, _, _ in inputs for name in generated):
        raise ValueError('Choose an output directory different from the original input files.')
    theme = (SOURCE / 'theme.template.css').read_text(encoding='utf-8')
    sidebar = (SOURCE / 'sidebar.template.css').read_text(encoding='utf-8')
    palette = (SOURCE / 'sidebar.palette.svg').read_text(encoding='utf-8')
    theme = theme.replace('__IMAGE__', 'data:image/png;base64,' + base64.b64encode(data[0]).decode())
    sidebar = sidebar.replace('__REPEAT_IMAGE__', 'data:image/jpeg;base64,' + base64.b64encode(data[1]).decode())
    sidebar = sidebar.replace('__ENDING_IMAGE__', 'data:image/jpeg;base64,' + base64.b64encode(data[2]).decode())
    if any(marker in theme + sidebar for marker in ('__IMAGE__', '__REPEAT_IMAGE__', '__ENDING_IMAGE__')):
        raise ValueError('Unresolved template image marker.')
    # Validate and read every input before creating any build output.
    output.mkdir(parents=True, exist_ok=True)
    (output / 'SunFaded.png').write_bytes(data[0])
    (output / 'SunFaded.css').write_text(theme, encoding='utf-8')
    (output / 'Sidebar.css').write_text(sidebar, encoding='utf-8')
    (output / 'SidebarPalette.svg').write_text(palette, encoding='utf-8')
    print(f'Prepared local assets: {output}')
    print('Images and generated CSS contain your artwork. Keep this output private.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--background', required=True, type=Path, help='Your main background PNG')
    parser.add_argument('--repeat', required=True, type=Path, help='Your repeating sidebar JPEG (recommended 864 x 1084)')
    parser.add_argument('--ending', required=True, type=Path, help='Your bottom sidebar JPEG (recommended 864 x 842)')
    parser.add_argument('--output', type=Path, default=SOURCE.parent / 'build' / 'own-images')
    args = parser.parse_args()
    try:
        prepare(args.background, args.repeat, args.ending, args.output)
    except ValueError as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
