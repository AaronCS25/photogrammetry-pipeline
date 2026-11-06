#!/usr/bin/env python3
"""Download a public Drive file directly to the data volume (no folder ZIP)."""
import argparse
import hashlib
from html.parser import HTMLParser
import http.cookiejar
import json
import os
from pathlib import Path
import re
import shutil
import time
import urllib.parse
import urllib.request


class Confirmation(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = None
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form' and attrs.get('id') == 'download-form':
            self.action = attrs.get('action')
        if tag == 'input' and attrs.get('name'):
            self.fields[attrs['name']] = attrs.get('value', '')


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def download(file_id, output, *, min_free_gib=30, max_gib=4):
    if not re.fullmatch(r'[A-Za-z0-9_-]{10,}', file_id):
        raise ValueError('Invalid Drive file ID')
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = output.with_name(output.name + '.fetch.json')
    if output.exists():
        old = json.loads(manifest.read_text()) if manifest.exists() else {}
        if old.get('drive_file_id') == file_id and old.get('sha256') == sha256(output):
            print(f'Already verified: {output}')
            return old
        raise ValueError(f'Existing unverified file: {output}; preserve or move it first')
    minimum = int(min_free_gib * 1024**3)
    maximum = int(max_gib * 1024**3)
    if shutil.disk_usage(output.parent).free < minimum:
        raise RuntimeError('Insufficient free space before download')
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    url = 'https://drive.usercontent.google.com/download?' + urllib.parse.urlencode(
        {'id': file_id, 'export': 'download', 'confirm': 't'})
    response = None
    for _ in range(3):
        response = opener.open(urllib.request.Request(url, headers={'User-Agent': 'PhotogrammetryPipeline/1.0'}), timeout=90)
        if 'text/html' not in response.headers.get('Content-Type', '').lower():
            break
        page = response.read(1024 * 1024).decode('utf-8', errors='replace')
        response.close()
        form = Confirmation()
        form.feed(page)
        if not form.action or urllib.parse.urlparse(form.action).hostname != 'drive.usercontent.google.com':
            raise RuntimeError('Drive returned a login/quota/error page, not a downloadable file')
        url = form.action + '?' + urllib.parse.urlencode(form.fields)
    else:
        raise RuntimeError('Drive confirmation did not produce a file')
    part = output.with_name(output.name + '.part')
    if part.exists():
        raise RuntimeError(f'Partial download exists: {part}; inspect it before retrying')
    total = 0
    start = time.monotonic()
    try:
        with response, part.open('xb') as f:
            for block in iter(lambda: response.read(1024 * 1024), b''):
                if total + len(block) > maximum or shutil.disk_usage(output.parent).free - len(block) < minimum:
                    raise RuntimeError('Download size or free-space limit reached')
                f.write(block)
                total += len(block)
            f.flush()
            os.fsync(f.fileno())
        expected = response.headers.get('Content-Length')
        if expected and int(expected) != total:
            raise RuntimeError('Incomplete download (Content-Length mismatch)')
        if not total:
            raise RuntimeError('Empty file')
        with part.open('rb') as f:
            prefix = f.read(8192)
        if output.suffix.lower() == '.mp4' and b'ftyp' not in prefix[:64]:
            raise RuntimeError('Downloaded content is not an MP4 container')
        if output.suffix.lower() == '.srt' and b'-->' not in prefix:
            raise RuntimeError('Downloaded content is not an SRT subtitle file')
        metadata = {'drive_file_id': file_id, 'filename': output.name, 'bytes': total,
                    'sha256': sha256(part), 'download_seconds': round(time.monotonic() - start, 2)}
        part.rename(output)
        manifest.write_text(json.dumps(metadata, indent=2) + '\n')
        print(json.dumps(metadata, indent=2))
        return metadata
    except BaseException:
        print(f'Download not published; partial data, if any: {part}')
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file-id', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--min-free-gib', type=float, default=30)
    parser.add_argument('--max-gib', type=float, default=4)
    args = parser.parse_args()
    download(args.file_id, args.output, min_free_gib=args.min_free_gib, max_gib=args.max_gib)
