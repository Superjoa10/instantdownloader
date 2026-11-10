"""Run explicitly on a graphical desktop: INSTANTDOWNLOADER_GUI_TESTS=1 pytest tests/test_gui.py."""
import os
import time
from unittest.mock import patch
import pytest
from datetime import datetime

pytestmark = pytest.mark.skipif(os.environ.get('INSTANTDOWNLOADER_GUI_TESTS') != '1', reason='Requires a graphical desktop')


def test_dedicated_connection_consent_and_status(tmp_path, monkeypatch):
    from instantdownloader import gui
    from unittest.mock import Mock
    app = gui.InstantDownloaderApp(tmp_path)
    try:
        app.show('Configuracoes')
        app.update()
        assert not app.advanced_session.winfo_manager()
        monkeypatch.setattr(gui.messagebox, 'askyesno', Mock(return_value=False))
        connect = Mock(return_value='owner')
        monkeypatch.setattr(app.instagram_session, 'connect', connect)
        app.session_action('connect')
        connect.assert_not_called()
        assert app.worker is None
        monkeypatch.setattr(gui.messagebox, 'askyesno', Mock(return_value=True))
        app.session_action('connect')
        app.worker.join(timeout=2)
        app.drain()
        assert 'Conectado como @owner' in app.session_status.get()
        assert 'ainda nao testada' in app.session_status.get()
        assert app.session_cancel_button.cget('state') == 'disabled'
        assert all(button.cget('state') == 'normal' for button in app.session_buttons)
    finally:
        app.app_lock.close()
        app.destroy()


def test_session_error_opens_dialog_and_preserves_original_failure(tmp_path, monkeypatch):
    from instantdownloader import gui
    from unittest.mock import Mock
    app = gui.InstantDownloaderApp(tmp_path)
    dialog = Mock()
    monkeypatch.setattr(gui.messagebox, 'showerror', dialog)
    try:
        app.emit('session_error', 'HTTP 403. Etapa: verify_identity.')
        app.drain()
        dialog.assert_called_once()
        assert 'HTTP 403' in app.last_session_error
        assert app.session_error_button.cget('state') == 'normal'
        app.emit('session_error', 'Sessao ausente, invalida ou expirada.')
        app.drain()
        assert dialog.call_count == 2
        assert 'HTTP 403' in app.last_session_error
        app.show_session_error()
        assert 'HTTP 403' in dialog.call_args.args[1]
    finally:
        app.app_lock.close()
        app.destroy()


@pytest.mark.parametrize('complete', [True, False])
def test_reels_test_reports_pages_and_unknown_total(tmp_path, monkeypatch, complete):
    from instantdownloader import gui
    from instantdownloader.core.profiles import DiscoveryResult
    from unittest.mock import Mock
    app = gui.InstantDownloaderApp(tmp_path)
    dialog = Mock()
    monkeypatch.setattr(gui.messagebox, 'showerror', dialog)
    local = {'username': 'owner', 'cookies': []}
    result = DiscoveryResult([], complete, 'A pagina parou de entregar resultados.',
                             inspected={'reels': 0}, exhausted={'reels': complete},
                             error_code=None if complete else 'discovery_incomplete', pages={'reels': 1})
    discover = Mock(return_value=result)
    monkeypatch.setattr(app.instagram_session, 'load', lambda: local)
    monkeypatch.setattr(gui, 'discover_profile', discover)
    try:
        app.profile_url.set('example')
        app.session_action('reels')
        app.worker.join(timeout=2)
        app.drain()
        discover.assert_called_once_with('example', limit=20, scope='reels',
                                        cancel_event=app.worker_cancel, local_session=local)
        text = app.session_status.get() if complete else app.last_session_error
        assert 'Paginas: 1' in text and 'Tempo:' in text
        if complete:
            assert 'Fim da origem' in text
            dialog.assert_not_called()
        else:
            assert 'Total desconhecido' in text
            assert result.message in text
            dialog.assert_called_once()
        assert not app.store.rows()
    finally:
        app.app_lock.close()
        app.destroy()


def test_login_manual_confirmation_and_retry_controls(tmp_path, monkeypatch):
    from instantdownloader import gui
    from unittest.mock import Mock
    from threading import Event
    app = gui.InstantDownloaderApp(tmp_path)
    failed = Event()
    consent = Mock(return_value=True)
    warning = Mock()
    monkeypatch.setattr(gui.messagebox, 'askyesno', consent)
    monkeypatch.setattr(gui.messagebox, 'showwarning', warning)
    def connect(cancel, confirm, **callbacks):
        callbacks['on_ready']()
        assert confirm.wait(3)
        confirm.clear()
        callbacks['on_attempt_error']('HTTP 302. Cookies preservados; navegador aberto.')
        failed.set()
        assert confirm.wait(3)
        return 'owner'
    monkeypatch.setattr(app.instagram_session, 'connect', connect)
    def wait_for(predicate):
        deadline = time.monotonic()+2
        while time.monotonic() < deadline and not predicate():
            app.update()
            time.sleep(.02)
        assert predicate()
    try:
        app.session_action('connect')
        wait_for(lambda: app.session_confirm_button.cget('state') == 'normal')
        assert not app.session_confirm.is_set()
        app.confirm_instagram_login()
        assert app.session_confirm_button.cget('state') == 'disabled'
        wait_for(lambda: failed.is_set() and app.session_confirm_button.cget('state') == 'normal')
        assert app.worker.is_alive()
        assert app.session_cancel_button.cget('state') == 'normal'
        warning.assert_called_once()
        app.confirm_instagram_login()
        app.worker.join(timeout=2)
        app.drain()
        assert app.session_confirm_button.cget('state') == 'disabled'
        assert 'Conectado como @owner' in app.session_status.get()
    finally:
        app.worker_cancel.set()
        app.session_confirm.set()
        if app.worker:
            app.worker.join(timeout=3)
        app.app_lock.close()
        app.destroy()


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


@pytest.mark.parametrize('complete', [False, True])
def test_empty_profile_never_offers_starting_downloads(tmp_path, complete):
    pytest.importorskip('customtkinter')
    from instantdownloader.gui import InstantDownloaderApp
    from instantdownloader.core.profiles import DiscoveryResult
    app = InstantDownloaderApp(tmp_path)
    try:
        app.geometry('920x720')
        app.source.set('Perfil')
        app.sources['Perfil'].tkraise()
        app.profile_scopes = ('reels',)
        result = DiscoveryResult([], complete, 'Technical provider error', {'reels': 0}, {'reels': complete},
                                 None if complete else 'session_unavailable', None if complete else 'reels')
        app.emit('discovery_done', result)
        app.drain()
        app.update()
        label = app.profile_tree.empty_label
        assert label.winfo_rooty() >= app.profile_tree.winfo_rooty() + 40
        assert label.winfo_rooty() + label.winfo_height() <= app.profile_tree.winfo_rooty() + app.profile_tree.winfo_height()
        assert 'Iniciar' not in app.status.get()
        assert app.profile_add_button.cget('state') == 'disabled'
        assert app.profile_select_button.cget('state') == 'disabled'
        assert app.profile_stop_button.cget('state') == 'disabled'
        assert 'Technical provider' not in app.profile_feedback.cget('text')
        assert ('Reels: 0' if complete else 'Reels: ?') in app.profile_counts.cget('text')
        assert 'Feed: -' in app.profile_counts.cget('text')
        if not complete:
            assert 'sessao autorizada' in app.profile_feedback.cget('text')
    finally:
        app.app_lock.close()
        app.destroy()


def test_browser_discovery_requires_confirmation_before_worker(tmp_path, monkeypatch):
    pytest.importorskip('customtkinter')
    from instantdownloader import gui
    from unittest.mock import Mock
    app = gui.InstantDownloaderApp(tmp_path)
    try:
        app.profile_url.set('@example')
        app.settings['profile_browser'].set('chrome')
        app.settings['login'].set('owner')
        consent = Mock(return_value=False)
        discover = Mock(side_effect=AssertionError('No consent'))
        monkeypatch.setattr(gui.messagebox, 'askyesno', consent)
        monkeypatch.setattr(gui, 'discover_profile', discover)
        app.discover()
        consent.assert_called_once()
        discover.assert_not_called()
        assert app.worker is None
    finally:
        app.app_lock.close()
        app.destroy()


def test_profile_date_selection_inclusive_unknown_excluded(tmp_path):
    from instantdownloader.gui import InstantDownloaderApp
    from datetime import date
    app = InstantDownloaderApp(tmp_path)
    try:
        app.profile_items = [
            {'id': str(index), 'title': 'Example', 'date': stamp, 'sources': ['reels']}
            for index, stamp in enumerate(['2026-11-01', '2026-11-07T23:59:59Z', '2026-11-08', ''])
        ]
        app.render_profile()
        app.profile_tree.selection_set(('2', '3'))
        app.apply_profile_cutoff(date(2026, 11, 7))
        assert app.profile_tree.selection() == ('0', '1')
        assert app.profile_tree.item('1', 'values')[2] == '07/11/2026'
        assert app.profile_tree.item('3', 'values')[2] == 'Nao informada'
        assert str(app.profile_tree.column('date', 'anchor')) == 'center'
        assert '1 sem data' in app.status.get()
        assert not app.store.rows()
        app.profile_cutoff.set('31/02/2026')
        app.select_profile_until()
        assert app.profile_tree.selection() == ('0', '1')
        assert 'data valida' in app.status.get()
    finally:
        app.app_lock.close()
        app.destroy()


def test_select_until_loads_missing_dates_before_selecting(tmp_path, monkeypatch):
    from instantdownloader import gui
    app = gui.InstantDownloaderApp(tmp_path)
    monkeypatch.setattr(app.instagram_session, 'load', lambda: {'cookies': []})
    def load(items, session, cancel, on_date):
        assert len(items) == 2
        on_date('abc', '2025-07-31')
        on_date('unknown', '')
        return 'Consulta encerrada.'
    monkeypatch.setattr(gui, 'load_publication_dates', load)
    try:
        app.profile_url.set('example')
        app.profile_cutoff.set('31/07/2025')
        app.profile_items = [
            {'id': 'abc', 'url': 'https://www.instagram.com/reel/abc/', 'title': 'A', 'date': '', 'sources': ['reels']},
            {'id': 'unknown', 'url': 'https://www.instagram.com/reel/unknown/', 'title': 'B', 'date': '', 'sources': ['reels']},
        ]
        app.render_profile()
        app.select_profile_until()
        app.worker.join(timeout=2)
        app.drain()
        assert app.profile_tree.selection() == ('0',)
        assert app.profile_tree.item('0', 'values')[2] == '31/07/2025'
        assert '1 sem data' in app.status.get()
        assert not app.store.rows()
    finally:
        app.worker_cancel.set()
        if app.worker:
            app.worker.join(timeout=2)
        app.app_lock.close()
        app.destroy()


@pytest.mark.parametrize('code', ['rate_limited', 'local_throttle'])
def test_combined_feed_failure_marks_reels_as_not_queried(tmp_path, code):
    from instantdownloader.gui import InstantDownloaderApp
    from instantdownloader.core.profiles import DiscoveryResult
    app = InstantDownloaderApp(tmp_path)
    try:
        app.profile_scopes = ('posts', 'reels')
        result = DiscoveryResult([], False, 'Stopped', exhausted={'posts': False, 'reels': False},
                                 error_code=code, failed_source='posts')
        app.emit('discovery_done', result)
        app.drain()
        message = app.profile_feedback.cget('text')
        assert 'Falha na origem Feed.' in message
        assert 'Reels nao foi consultado' in message
        if code == 'local_throttle':
            assert 'pausa preventiva local' in message
            assert 'O Instagram limitou' not in message
        assert 'Reels: ?' in app.profile_counts.cget('text')
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
        assert kwargs['scope'] == 'all'
        item = DiscoveredVideo('ABC:1', 'https://www.instagram.com/p/ABC/', 'A video', '', '2026-11-02', 1, ('posts',))
        kwargs['on_item'](item)
        item.sources = ('posts', 'reels')
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
        app.store.save_discovery('example', {'id': 'old', 'title': 'Old cached result', 'date': '', 'url': 'https://example.com/old'})
        app.settings['output'].set(str(tmp_path/'media'))
        app.discover()
        pump_until(lambda: bool(app.profile_tree.get_children()) and not app.worker.is_alive())
        assert not app.store.rows()
        assert len(app.profile_items) == 1
        assert 'Reels: 1' in app.profile_counts.cget('text')
        assert 'Unicos: 1' in app.profile_counts.cget('text')
        assert 'Contagens parciais' in app.profile_feedback.cget('text')
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
