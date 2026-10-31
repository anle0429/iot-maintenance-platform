"""Encode captured authored SVG frames as a compact GIF; no architecture layout or runtime data is generated."""
from pathlib import Path
from hashlib import sha256
import argparse
import json
import re
from PIL import Image


def encode(folder, output, fps, scene_name):
    """Use one palette so stationary text remains stable across the illustration's motion frames."""
    paths = sorted(folder.glob('frame-*.png'))
    if len(paths) != 36 or fps != 6:
        raise ValueError('The reviewed preview uses 36 captured frames at six frames per second')
    frames = []
    here = Path(__file__).resolve().parent
    if scene_name not in {p.stem for p in here.glob('*.excalidraw')}:
        raise ValueError('Select an existing native architecture scene')
    scene = json.loads((here/(scene_name+'.excalidraw')).read_bytes())
    canvas = next(e for e in scene['elements'] if e['id'].endswith('-canvas'))
    width = min(1920, canvas['width'])
    height = round(canvas['height'] * width / canvas['width'])
    bank = json.loads((here/'icons/brands.json').read_bytes())
    colors = set()
    for element in scene['elements']:
        for key in ['strokeColor', 'backgroundColor']:
            color = element.get(key, '')
            if re.fullmatch(r'#[0-9a-fA-F]{6}', color):
                colors.add(color.lower())
        if element['type'] == 'image':
            colors.update(color.lower() for color in re.findall(r'#[0-9a-fA-F]{6}\b', bank[element['fileId']].get('svg', '')))
    # Reserve real mark/role colors: area-weighted quantization alone can recolor small logos.
    reserved = sorted({tuple(bytes.fromhex(color[1:])) for color in colors})
    reserved = list(dict.fromkeys(reserved + [tuple(round(channel * .5 + 127.5) for channel in color) for color in reserved]))
    if len(reserved) >= 200:
        raise ValueError('Leave room for antialiased text tones in the GIF palette')
    with Image.open(folder/'static.png') as raw:
        learned = raw.convert('RGB').quantize(colors=256-len(reserved), method=Image.Quantize.MEDIANCUT)
        learned_colors = learned.getpalette()[:(256-len(reserved))*3]
    palette = Image.new('P', (1, 1))
    palette.putpalette([channel for color in reserved for channel in color] + learned_colors)
    for path in paths:
        with Image.open(path) as raw:
            frame = raw.convert('RGB').quantize(palette=palette, dither=Image.Dither.NONE)
            if frame.size != (width, height):
                raise ValueError('Unexpected preview frame size')
            frames.append(frame)
    # GIF uses centiseconds; distribute rounding rather than shortening a six-second loop.
    durations = [10 * (((i + 1) * 100 // fps) - (i * 100 // fps)) for i in range(len(frames))]
    frames[0].save(output, save_all=True, append_images=frames[1:], loop=0, duration=durations, optimize=True, disposal=1)
    with Image.open(output) as saved:
        count = saved.n_frames
        actual_duration = 0
        identities = set()
        for i in range(count):
            saved.seek(i)
            actual_duration += saved.info.get('duration', 0)
            identities.add(sha256(saved.convert('RGB').tobytes()).hexdigest())
        if count != 36 or len(identities) != 36 or actual_duration != 6000 or saved.size != (width, height):
            raise ValueError('Encoded GIF does not preserve the reviewed frames and duration')
    if output.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('Preview exceeds the eight-MiB documentation budget')
    return {'frames': count, 'distinct_frames': len(identities), 'duration_ms': actual_duration, 'reserved_source_colors': len(reserved),
            'width': width, 'height': height, 'bytes': output.stat().st_size, 'sha256': sha256(output.read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('frames', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--fps', type=int, default=6)
    parser.add_argument('--scene', default='platform-system')
    args = parser.parse_args()
    print(json.dumps(encode(args.frames, args.output, args.fps, args.scene)))
