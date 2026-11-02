"""macOS visual audit in an isolated temporary workspace; never downloads media."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import plistlib
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from instantdownloader.gui import InstantDownloaderApp
from instantdownloader.core.downloader import analyze_url
from instantdownloader.core.thumbnail import load_thumbnail


def window_id():
    cg = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
    cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    cg.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
    cf.CFPropertyListCreateData.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long, ctypes.c_ulong, ctypes.c_void_p]
    cf.CFPropertyListCreateData.restype = ctypes.c_void_p
    cf.CFDataGetLength.argtypes = [ctypes.c_void_p]
    cf.CFDataGetLength.restype = ctypes.c_long
    cf.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
    cf.CFDataGetBytePtr.restype = ctypes.c_void_p
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    info = cg.CGWindowListCopyWindowInfo(1, 0)
    data = cf.CFPropertyListCreateData(None, info, 100, 0, None)
    try:
        windows = plistlib.loads(ctypes.string_at(cf.CFDataGetBytePtr(data), cf.CFDataGetLength(data)))
        return next(w['kCGWindowNumber'] for w in windows if w['kCGWindowOwnerPID'] == os.getpid() and w['kCGWindowLayer'] == 0)
    finally:
        cf.CFRelease(data)
        cf.CFRelease(info)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('portfolio_assets/audit'))
    parser.add_argument('--live-url')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    cg = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
    cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
    capture_allowed = cg.CGPreflightScreenCaptureAccess()
    if not capture_allowed:
        print('Screen recording permission unavailable; recording layout metrics only.', flush=True)
    results = []
    with TemporaryDirectory(prefix='instantdownloader-audit-') as home:
        app = InstantDownloaderApp(Path(home))
        try:
            app.update()
            if args.live_url:
                info = analyze_url(args.live_url, app.config())
                app.emit('analysis', (args.live_url, info))
                if info.thumbnail:
                    app.emit('thumbnail', (args.live_url, load_thumbnail(info.thumbnail)))
                app.url.set(args.live_url)
            for theme in ('Light', 'Dark'):
                app.change_theme(theme)
                for size in ('1080x800', '920x720'):
                    app.geometry(size)
                    for page in ('Adicionar', 'Fila', 'Historico', 'Configuracoes'):
                        app.show(page)
                        app.update()
                        for _ in range(4):
                            time.sleep(.08)
                            app.update()
                        target = args.output / f'{theme.lower()}-{size}-{page.lower()}.png'
                        results.append({'theme': theme, 'size': size, 'page': page,
                                        'width': app.winfo_width(), 'height': app.winfo_height(),
                                        'thumbnail_loaded': app.thumbnail_image is not None})
                        if capture_allowed:
                            subprocess.run(['/usr/sbin/screencapture', '-x', '-l', str(window_id()), str(target.resolve())], check=True)
                            print(target, flush=True)
                    app.show('Adicionar')
                    for source in ('Excel', 'Perfil'):
                        app.source.set(source)
                        app.sources[source].tkraise()
                        app.update()
                        time.sleep(.15)
                        app.update()
                        target = args.output / f'{theme.lower()}-{size}-{source.lower()}.png'
                        results.append({'theme': theme, 'size': size, 'page': source,
                                        'width': app.winfo_width(), 'height': app.winfo_height()})
                        if capture_allowed:
                            subprocess.run(['/usr/sbin/screencapture', '-x', '-l', str(window_id()), str(target.resolve())], check=True)
                            print(target, flush=True)
                    app.source.set('URL')
                    app.sources['URL'].tkraise()
        finally:
            app.app_lock.close()
            app.destroy()
    (args.output/'layout.json').write_text(json.dumps({'screen_capture': capture_allowed, 'screens': results}, indent=2))
    print(f'Checked {len(results)} screen/theme/size combinations.', flush=True)


if __name__ == '__main__':
    main()
