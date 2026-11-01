#!/usr/bin/env python3
"""팩 이미지를 200×200으로 맞춥니다. 폴더를 생략하면 모든 팩을 처리합니다."""

import argparse
from collections import Counter
from datetime import datetime
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import zlib

sys.dont_write_bytecode = True
try:
    from PIL import GifImagePlugin, Image, ImageOps, features
except ImportError:
    sys.exit('Pillow가 필요합니다. 설치: py -3 -m pip install Pillow')
from update_pack import linked, normalized, pack_folders, repository_root, select_folder


SIZE = (200, 200)
EXTENSIONS = {'.gif', '.png', '.jpg', '.jpeg', '.webp'}
FORMATS = {'GIF', 'PNG', 'JPEG', 'WEBP'}
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def visible_hash(frame):
    """완전히 투명한 픽셀의 보이지 않는 RGB 차이는 무시합니다."""
    rgba = frame.convert('RGBA')
    mask = rgba.getchannel('A').point(lambda alpha: 255 if alpha else 0)
    clean = Image.composite(rgba, Image.new('RGBA', rgba.size), mask)
    return digest(clean.tobytes())


def resized_frame(frame, fit, opaque=False):
    rgba = ImageOps.exif_transpose(frame).convert('RGBA')
    if fit == 'stretch':
        result = rgba.resize(SIZE, Image.Resampling.LANCZOS)
    else:
        width, height = rgba.size
        scale = min(SIZE[0] / width, SIZE[1] / height)
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        scaled = rgba.resize(size, Image.Resampling.LANCZOS)
        result = Image.new('RGBA', SIZE)
        result.paste(scaled, ((SIZE[0] - size[0]) // 2, (SIZE[1] - size[1]) // 2))
    if opaque:
        result = Image.alpha_composite(Image.new('RGBA', SIZE, 'white'), result)
    # 원본의 disposal/transparency 등 인코더 설정을 새 프레임에 전달하지 않습니다.
    result.info.clear()
    return result


def gif_palette(frame):
    # GIF의 투명색 하나를 예약합니다. GIF는 반투명을 지원하지 않습니다.
    indexed = frame.convert('RGB').quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    palette = (indexed.getpalette() + [0] * 768)[:768]
    palette[765:768] = [0, 0, 0]
    indexed.putpalette(palette)
    indexed.paste(255, mask=frame.getchannel('A').point(lambda alpha: 255 if alpha < 128 else 0))
    indexed.info = {'transparency': 255}
    return indexed


def write_gif(path, frames, durations, metadata):
    """save_all의 동일 프레임 병합을 피하고 모든 프레임을 개별 기록합니다.

    원본의 disposal을 적용한 표시 화면을 읽은 후 전체 화면 프레임으로
    저장합니다. 새 disposal=2는 이전 화면의 잔상이 남지 않도록 합니다.
    Pillow GIF plugin: https://pillow.readthedocs.io/en/stable/reference/plugins.html
    """
    expected = []
    header = {'transparency': 255, 'background': 255, 'optimize': False}
    for key in ('loop', 'comment'):
        if key in metadata:
            header[key] = metadata[key]
    with path.open('wb') as stream:
        for index, (frame, duration) in enumerate(zip(frames, durations)):
            indexed = gif_palette(frame)
            if index == 0:
                blocks, _ = GifImagePlugin.getheader(indexed, info=header)
                stream.writelines(blocks)
            expected.append(visible_hash(indexed))
            stream.writelines(GifImagePlugin.getdata(
                indexed, duration=duration, disposal=2, transparency=255,
                include_color_table=index > 0, optimize=False,
            ))
        stream.write(b';')
    return expected


def png_chunks(data):
    offset = 8
    while offset < len(data):
        size = struct.unpack_from('>I', data, offset)[0]
        tag = data[offset + 4:offset + 8]
        yield tag, data[offset + 8:offset + 8 + size]
        offset += 12 + size


def png_chunk(tag, data):
    return (struct.pack('>I', len(data)) + tag + data
            + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))


def write_apng(path, frames, metadata, save_options):
    """APNG도 같은 프레임을 합치지 않고 원본의 정확한 시간 분수를 기록합니다.

    https://www.w3.org/TR/png-3/#11fcTL
    """
    default = bool(metadata.get('default_image', False))
    delays = metadata['png_delays']
    if len(delays) != len(frames) - int(default):
        raise ValueError('APNG의 프레임 시간 정보가 일치하지 않습니다.')
    sequence = 0
    with path.open('wb') as stream:
        stream.write(PNG_SIGNATURE)
        for index, frame in enumerate(frames):
            buffer = BytesIO()
            frame.save(buffer, format='PNG', **save_options)
            chunks = list(png_chunks(buffer.getvalue()))
            if index == 0:
                for tag, data in chunks:
                    if tag not in {b'IDAT', b'IEND'}:
                        stream.write(png_chunk(tag, data))
                stream.write(png_chunk(b'acTL', struct.pack(
                    '>II', len(delays), metadata.get('loop', 0))))
            if not (default and index == 0):
                numerator, denominator = delays[index - int(default)]
                # 전체 화면을 SOURCE 방식으로 덮어써서 원본의 합성 결과를 보존합니다.
                control = struct.pack('>IIIIIHHBB', sequence, *SIZE, 0, 0,
                                      numerator, denominator, 0, 0)
                stream.write(png_chunk(b'fcTL', control))
                sequence += 1
            for tag, data in chunks:
                if tag != b'IDAT':
                    continue
                if index == 0:
                    stream.write(png_chunk(b'IDAT', data))
                else:
                    stream.write(png_chunk(b'fdAT', struct.pack('>I', sequence) + data))
                    sequence += 1
        stream.write(png_chunk(b'IEND', b''))


def riff_chunk(tag, data):
    return tag + struct.pack('<I', len(data)) + data + (b'\0' if len(data) % 2 else b'')


def uint24(value):
    return int(value).to_bytes(3, 'little')


def write_animated_webp(path, frames, durations, metadata, save_options):
    """개별 무손실 프레임을 묶어 동일 프레임의 병합을 방지합니다.

    https://developers.google.com/speed/webp/docs/riff_container
    """
    flags = 0x02 | 0x10  # Animation + Alpha
    metadata_chunks = []
    for key, tag, flag in [('icc_profile', b'ICCP', 0x20),
                           ('exif', b'EXIF', 0x08), ('xmp', b'XMP ', 0x04)]:
        if save_options.get(key):
            flags |= flag
            metadata_chunks.append((tag, save_options[key]))
    chunks = [riff_chunk(b'VP8X', bytes([flags, 0, 0, 0]) + uint24(199) + uint24(199))]
    chunks.extend(riff_chunk(tag, data) for tag, data in metadata_chunks if tag == b'ICCP')
    red, green, blue, alpha = metadata.get('background', (0, 0, 0, 0))
    chunks.append(riff_chunk(b'ANIM', bytes([blue, green, red, alpha])
                             + struct.pack('<H', metadata.get('loop', 0))))
    for frame, duration in zip(frames, durations):
        buffer = BytesIO()
        frame.save(buffer, format='WEBP', lossless=True, exact=True, method=4)
        raw = buffer.getvalue()
        encoded = bytearray()
        offset = 12
        while offset < len(raw):
            tag = raw[offset:offset + 4]
            length = struct.unpack_from('<I', raw, offset + 4)[0]
            end = offset + 8 + length + (length % 2)
            if tag in {b'ALPH', b'VP8 ', b'VP8L'}:
                encoded.extend(raw[offset:end])
            offset = end
        # 좌표 0, 전체 화면, no-blend: 프레임마다 독립된 표시 화면입니다.
        control = b'\0' * 6 + uint24(199) + uint24(199) + uint24(duration) + b'\x02'
        chunks.append(riff_chunk(b'ANMF', control + encoded))
    chunks.extend(riff_chunk(tag, data) for tag, data in metadata_chunks if tag != b'ICCP')
    payload = b'WEBP' + b''.join(chunks)
    path.write_bytes(b'RIFF' + struct.pack('<I', len(payload)) + payload)


def encode_and_validate(original, output, fit):
    frames, durations = [], []
    with Image.open(BytesIO(original)) as source:
        image_format = source.format
        if image_format not in FORMATS:
            raise ValueError(f'지원하지 않는 실제 이미지 형식: {image_format}')
        metadata = dict(source.info)
        original_size = source.size
        if image_format == 'PNG':
            metadata['png_delays'] = [struct.unpack_from('>HH', data, 20)
                                      for tag, data in png_chunks(original) if tag == b'fcTL']
        options = {}
        if metadata.get('icc_profile') and source.mode in {'RGB', 'RGBA', 'P', 'L', 'LA'}:
            options['icc_profile'] = metadata['icc_profile']
        for index in range(getattr(source, 'n_frames', 1)):
            source.seek(index)
            source.load()  # GIF 합성과 WebP의 프레임별 duration을 먼저 확정합니다.
            duration = source.info.get('duration', 0)
            if image_format == 'PNG' and metadata.get('default_image') and index == 0:
                duration = 0
            durations.append(duration)
            frames.append(resized_frame(source, fit, opaque=image_format == 'JPEG'))
        if metadata.get('exif'):
            exif = ImageOps.exif_transpose(source).getexif()
            for tag in (256, 257, 40962, 40963):
                if tag in exif:
                    exif[tag] = 200
            if exif:
                options['exif'] = exif.tobytes()
    expected = [visible_hash(frame) for frame in frames]
    if image_format == 'GIF':
        expected = write_gif(output, frames, durations, metadata)
    elif image_format == 'WEBP' and len(frames) > 1:
        write_animated_webp(output, frames, durations, metadata, options)
    elif image_format == 'PNG' and metadata['png_delays']:
        write_apng(output, frames, metadata, options)
    elif image_format == 'JPEG':
        frames[0].convert('RGB').save(output, format='JPEG', quality=95,
                                      subsampling=0, optimize=True, **options)
    elif image_format == 'WEBP':
        frames[0].save(output, format='WEBP', lossless=True, exact=True, method=4, **options)
    else:
        frames[0].save(output, format='PNG', **options)

    # 검증에 실패하면 원본 교체 단계로 넘어가지 않습니다.
    with Image.open(output) as result:
        if result.format != image_format or result.size != SIZE:
            raise ValueError('변환 결과의 형식 또는 크기가 일치하지 않습니다.')
        if getattr(result, 'n_frames', 1) != len(frames):
            raise ValueError('변환 결과의 프레임 수가 원본과 다릅니다.')
        if image_format == 'GIF' or len(frames) > 1 or metadata.get('png_delays'):
            if result.info.get('loop') != metadata.get('loop'):
                raise ValueError('반복 재생 설정이 원본과 다릅니다.')
        if bool(result.info.get('default_image')) != bool(metadata.get('default_image')):
            raise ValueError('APNG 기본 이미지 설정이 원본과 다릅니다.')
        for index in range(len(frames)):
            result.seek(index)
            result.load()
            duration = result.info.get('duration', 0)
            if metadata.get('default_image') and index == 0:
                duration = 0
            if abs(duration - durations[index]) > 0.000001:
                raise ValueError(f'{index + 1}번 프레임의 재생 시간이 달라졌습니다.')
            if image_format != 'JPEG' and visible_hash(result) != expected[index]:
                raise ValueError(f'{index + 1}번 프레임의 표시 결과가 일치하지 않습니다.')
    return {'format': image_format, 'original_size': original_size,
            'size': SIZE, 'frames': len(frames), 'duration_ms': sum(durations)}


def resize_file(path, root, backup_root, fit, dry_run):
    if linked(path):
        raise ValueError('연결 파일 대신 packs 안의 원본 파일을 사용해 주세요.')
    original = path.read_bytes()
    with Image.open(BytesIO(original)) as image:
        if image.format not in FORMATS:
            raise ValueError(f'지원하지 않는 실제 이미지 형식: {image.format}')
        if image.size == SIZE:
            return {'status': 'skipped'}
        if dry_run:
            return {'status': 'planned', 'original_size': image.size}
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(prefix='.resize-', suffix='.tmp', dir=path.parent)
        os.close(descriptor)
        temporary = Path(name)
        details = encode_and_validate(original, temporary, fit)
        if digest(path.read_bytes()) != digest(original):
            raise ValueError('작업 중 원본 파일이 변경되어 교체하지 않았습니다.')
        backup = backup_root / path.relative_to(root)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        if digest(backup.read_bytes()) != digest(original):
            raise ValueError('백업 검증에 실패했습니다.')
        temporary.chmod(path.stat().st_mode & 0o777)
        os.replace(temporary, path)
        return {'status': 'resized', 'original_sha256': digest(original), **details}
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', nargs='?', help='특정 팩 이름 또는 폴더 경로; 생략하면 전체 팩')
    parser.add_argument('--dry-run', action='store_true', help='파일을 바꾸지 않고 변환 대상 수만 확인')
    parser.add_argument('--fit', choices=['contain', 'stretch'], default='contain',
                        help='contain: 비율 유지+여백(기본); stretch: 여백 없이 200×200으로 늘림')
    args = parser.parse_args()
    root = repository_root()
    try:
        folders = [select_folder(root, args.folder)] if args.folder else list(pack_folders(root).values())
        paths = [path for folder in folders
                 for path in sorted(folder.iterdir(), key=lambda path: normalized(path.name))
                 if path.is_file() and not path.name.startswith('.') and path.suffix.lower() in EXTENSIONS]
        if any(path.suffix.lower() == '.webp' for path in paths) and not features.check('webp'):
            raise ValueError('현재 Pillow에 WebP 지원이 없습니다. WebP를 지원하는 Pillow가 필요합니다.')
        backup_root = root / 'script' / 'backups' / 'resize-200' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        totals = Counter()
        report = []
        for index, path in enumerate(paths, 1):
            relative = normalized(str(path.relative_to(root)))
            try:
                details = resize_file(path, root, backup_root, args.fit, args.dry_run)
            except (OSError, ValueError, EOFError) as error:
                details = {'status': 'failed', 'error': str(error)}
                print(f'오류: {relative}: {error}', file=sys.stderr, flush=True)
            totals[details['status']] += 1
            report.append({'file': relative, **details})
            if details['status'] == 'resized':
                print(f'[{index}/{len(paths)}] 변환: {relative} ({details["frames"]}프레임)', flush=True)
        if backup_root.exists():
            (backup_root / 'report.json').write_text(
                json.dumps({'fit': args.fit, 'files': report}, ensure_ascii=False, indent=2) + '\n',
                encoding='utf-8')
            print(f'원본 백업: {backup_root}')
        print(f'총 {len(paths)}개: 변환 {totals["resized"]}, 이미 200×200 {totals["skipped"]}, '
              f'변환 예정 {totals["planned"]}, 실패 {totals["failed"]}')
        return 1 if totals['failed'] else 0
    except (OSError, ValueError) as error:
        print(f'오류: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
