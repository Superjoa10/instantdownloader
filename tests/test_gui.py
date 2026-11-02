"""Run explicitly on a graphical desktop: INSTANTDOWNLOADER_GUI_TESTS=1 pytest tests/test_gui.py."""
import os
import time
from unittest.mock import patch
import pytest
from datetime import datetime

pytestmark = pytest.mark.skipif(os.environ.get('INSTANTDOWNLOADER_GUI_TESTS') != '1', reason='Requires a graphical desktop')


def test_gui_pages_queue_and_preferences(tmp_path):
    pytest.importorskip('customtkinter')
    from instantdownloader.gui import InstantDownloaderApp
    app = InstantDownloaderApp(tmp_path)
    try:
        app.update()
        for name in app.pages:
            app.show(name)
            app.update()
            assert app.pages[name].winfo_ismapped()
        app.url.set('https://example.com/video')
        app.add_url()
        app.update()
        assert len(app.trees['Fila'].get_children()) == 1
        app.settings['output'].set(str(tmp_path/'saved'))
        app.save_settings()
        app.settings['output'].set(str(tmp_path/'edited'))
        app.restore_defaults()
        assert app.settings['output'].get() == str(tmp_path/'saved')
        app.change_theme('Dark')
        app.update()
        app.change_theme('Light')
        app.update()
        app.trees['Fila'].selection_set(app.trees['Fila'].get_children())
        app.cancel_selected()
        assert app.store.rows()[0]['status'] == 'cancelled'
    finally:
        app.app_lock.close()
        app.destroy()


def test_gui_minimum_layout_long_metadata(tmp_path):
    ctk = pytest.importorskip('customtkinter')
    from instantdownloader.gui import InstantDownloaderApp
    from instantdownloader.core.models import VideoInfo
    app = InstantDownloaderApp(tmp_path)
    try:
        app.geometry('920x720')
        app.emit('analysis', ('https://example.com/a', VideoInfo('https://example.com/a', title='Long video title ' * 40, uploader='Author '*30)))
        app.status.set('Caminho de resultado: ' + '/long-directory-name'*25)
        for theme in ('Light', 'Dark'):
            app.change_theme(theme)
            for page in app.pages:
                app.show(page)
                for _ in range(3):
                    app.update()
                    time.sleep(.06)
                frame = app.pages[page]
                assert frame.winfo_rootx()+frame.winfo_width() <= app.winfo_rootx()+app.winfo_width()
                assert frame.winfo_rooty()+frame.winfo_height() <= app.winfo_rooty()+app.winfo_height()
        assert len(app.preview_title.cget('text')) <= 140
    finally:
        app.app_lock.close()
        app.destroy()


def test_profile_review_to_persistent_queue_and_report(tmp_path, monkeypatch):
    pytest.importorskip('customtkinter')
    from instantdownloader import gui
    from instantdownloader.core.profiles import DiscoveredVideo, DiscoveryResult
    from instantdownloader.core.models import DownloadResult
    app = gui.InstantDownloaderApp(tmp_path/'state')
    def discover(profile, **kwargs):
        item = DiscoveredVideo('ABC:1', 'https://www.instagram.com/p/ABC/', 'A video', '', '2026-11-02', 1)
        kwargs['on_item'](item)
        return DiscoveryResult([item], True, 'Bounded scan complete.')
    def download(job, config, progress_callback, cancel_event):
        assert job.playlist_index == 1
        progress_callback({'status': 'converting'})
        path = tmp_path/'video.mp4'
        path.write_bytes(b'fixture')
        return DownloadResult(job.url, 'success', datetime.now(), datetime.now(), path, width=640, height=360)
    def pump_until(predicate):
        deadline = time.monotonic()+5
        while time.monotonic()<deadline:
            app.update()
            if predicate():
                return
            time.sleep(.03)
        pytest.fail('GUI operation did not complete')
    monkeypatch.setattr(gui, 'discover_profile', discover)
    monkeypatch.setattr('instantdownloader.core.queue.download_job', download)
    try:
        app.update()
        app.profile_url.set('@example')
        app.settings['output'].set(str(tmp_path/'media'))
        app.discover()
        pump_until(lambda: bool(app.profile_tree.get_children()) and not app.worker.is_alive())
        assert not app.store.rows()
        app.profile_tree.selection_set(app.profile_tree.get_children())
        app.add_profile()
        assert app.store.rows()[0]['status'] == 'pending'
        app.start_queue()
        pump_until(lambda: bool(app.runner.thread) and not app.runner.running)
        app.refresh()
        assert app.store.rows()[0]['status'] == 'success'
        assert app.trees['Historico'].get_children()
        assert list((tmp_path/'state/reports').glob('*.csv'))
        assert app.store.rows()[0]['width'] == 640
    finally:
        app.runner.stop()
        if app.runner.thread:
            app.runner.thread.join(5)
        app.app_lock.close()
        app.destroy()
