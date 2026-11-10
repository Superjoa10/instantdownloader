import os
import json
import time
from contextlib import contextmanager
from threading import Event
from unittest.mock import Mock

import pytest

from instantdownloader.core.instagram_session import InstagramSession, SessionError, instagram_cookies


def run_connect(session, cancel, **kwargs):
    confirm = Event()
    confirm.set()
    kwargs.setdefault('timeout', 12)
    return session.connect(cancel, confirm, **kwargs)


def cookies():
    return [{'name': name, 'value': value, 'domain': '.instagram.com', 'path': '/',
             'expires': time.time()+3600, 'httpOnly': True, 'secure': True, 'sameSite': 'Lax'}
            for name, value in [('sessionid', 'secret'), ('csrftoken', 'csrf'), ('ds_user_id', '123')]]


def viewer_html(user_id='123', username='Owner', module_id=None):
    module = ['PolarisViewer', [], {'id': module_id or user_id, 'data': {'id': user_id, 'username': username}}]
    return '<script type="application/json">' + json.dumps({'require': [['wrapper', {}, [module]]]}) + '</script>'


def browser(session, monkeypatch):
    from types import SimpleNamespace
    from instantdownloader.core import instagram_session
    clock = {'now': 0.0}
    monkeypatch.setattr(instagram_session, 'time', SimpleNamespace(time=time.time, monotonic=lambda: clock['now']))
    context = Mock()
    context.cookies.return_value = cookies()
    response = context.request.get.return_value
    response.status = 200
    response.text.return_value = viewer_html()
    page = context.new_page.return_value
    page.url = 'https://www.instagram.com/'
    page.is_closed.return_value = False
    page.evaluate.return_value = 'complete'
    page.wait_for_timeout.side_effect = lambda ms: clock.update(now=clock['now']+ms/1000)
    context.test_clock = clock

    @contextmanager
    def opened(visible):
        context.test_open = True
        try:
            yield context
        finally:
            context.test_open = False

    monkeypatch.setattr(session, '_browser', opened)
    return context


def test_no_verification_or_capture_until_manual_confirmation(tmp_path, monkeypatch):
    session = InstagramSession(tmp_path)
    context = browser(session, monkeypatch)
    confirm, cancel = Event(), Event()
    def wait(ms):
        context.test_clock['now'] += ms/1000
        if context.test_clock['now'] >= 300:
            context.request.get.assert_not_called()
            assert not session.pending_path.exists()
            assert context.test_open
            cancel.set()
    context.new_page.return_value.wait_for_timeout.side_effect = wait
    with pytest.raises(SessionError, match='cancelada'):
        session.connect(cancel, confirm)
    assert not context.test_open


def test_identity_failure_keeps_browser_open_and_requires_new_click(tmp_path, monkeypatch):
    session = InstagramSession(tmp_path)
    context = browser(session, monkeypatch)
    success = context.request.get.return_value
    redirect = Mock(status=302, headers={'location': '/accounts/onetap/?secret=token'})
    context.request.get.side_effect = [redirect, success]
    confirm, cancel = Event(), Event()
    confirm.set()
    failures = []
    def error(message):
        assert context.test_open
        assert not confirm.is_set()
        assert session.pending_path.exists()
        assert not session.saved
        assert 'save_login_prompt' in message
        assert 'secret=token' not in message
        failures.append(context.test_clock['now'])
    def wait(ms):
        context.test_clock['now'] += ms/1000
        if failures and len(failures) == 1 and context.test_clock['now'] < failures[0]+3:
            assert context.request.get.call_count == 1
        elif failures and context.test_clock['now'] == failures[0]+3:
            confirm.set()
    context.new_page.return_value.wait_for_timeout.side_effect = wait
    assert session.connect(cancel, confirm, timeout=20, on_attempt_error=error) == 'owner'
    assert context.request.get.call_count == 2
    assert len(failures) == 1
    assert not context.test_open
    assert session.saved
    assert not session.pending_path.exists()


def test_cancel_after_failed_attempt_preserves_pending_cookies(tmp_path, monkeypatch):
    session = InstagramSession(tmp_path)
    context = browser(session, monkeypatch)
    context.request.get.return_value.status = 403
    confirm, cancel = Event(), Event()
    confirm.set()
    def error(_):
        assert context.test_open
        cancel.set()
    with pytest.raises(SessionError, match='cancelada'):
        session.connect(cancel, confirm, on_attempt_error=error)
    assert not context.test_open
    assert session.pending_path.exists()
    context.request.get.assert_called_once()


@pytest.mark.parametrize(('url', 'expected'), [
    ('https://i.instagram.com/api/v1/accounts/current_user/?secret=token', ('instagram_mobile_api', 'identity_endpoint')),
    ('https://www.instagram.com/accounts/onetap/?secret=token', ('instagram_web', 'save_login_prompt')),
    ('https://www.instagram.com/accounts/login/?secret=token', ('instagram_web', 'login')),
    ('https://www.instagram.com/challenge/secret/', ('instagram_web', 'challenge')),
    ('https://www.instagram.com.evil.test/?secret=token', ('other_host', 'other_destination')),
])
def test_redirect_diagnostics_do_not_include_arbitrary_urls(url, expected):
    from instantdownloader.core.instagram_session import redirect_description
    assert redirect_description(url) == expected


def test_only_live_instagram_auth_cookies_are_saved(tmp_path):
    session = InstagramSession(tmp_path)
    extra = [dict(cookies()[0], domain='instagram.com.evil.test'),
             dict(cookies()[0], domain='.example.com'),
             dict(cookies()[0], name='unrelated'), dict(cookies()[0], expires=1)]
    assert instagram_cookies(extra) == []
    session.save('Owner', cookies()+extra)
    assert session.load()['username'] == 'owner'
    assert len(session.load()['cookies']) == 3
    if os.name != 'nt':
        assert session.path.stat().st_mode & 0o777 == 0o600
        assert session.directory.stat().st_mode & 0o777 == 0o700
    other = tmp_path/'personal-browser'
    other.mkdir()
    session.delete()
    session.delete()
    assert not session.saved
    assert other.exists()


def test_bad_state_does_not_expose_credentials(tmp_path):
    session = InstagramSession(tmp_path)
    session.save('owner', cookies())
    session.path.write_text('{secret')
    with pytest.raises(SessionError) as exc:
        session.load()
    assert 'secret' not in str(exc.value)


def test_connect_only_saves_after_server_identity(tmp_path, monkeypatch):
    session = InstagramSession(tmp_path)
    context = browser(session, monkeypatch)
    assert run_connect(session, Event()) == 'owner'
    assert session.saved
    context.request.get.assert_called_once()
    context.request.get.return_value.dispose.assert_called_once()


@pytest.mark.parametrize('status', [401, 403, 429, 500])
def test_failed_verification_does_not_save_or_retry(tmp_path, monkeypatch, status):
    session = InstagramSession(tmp_path)
    context = browser(session, monkeypatch)
    context.request.get.return_value.status = status
    with pytest.raises(SessionError):
        run_connect(session, Event())
    assert not session.saved
    context.request.get.assert_called_once()


def test_identity_mismatch_is_not_a_connection(tmp_path, monkeypatch):
    session = InstagramSession(tmp_path)
    context = browser(session, monkeypatch)
    context.request.get.return_value.text.return_value = viewer_html(user_id='999')
    with pytest.raises(SessionError):
        run_connect(session, Event())
    assert not session.saved
    assert session.pending_path.exists()
    with pytest.raises(SessionError, match='pendente'):
        session.load()


def test_login_waits_for_complete_document_and_stability(tmp_path, monkeypatch):
    session = InstagramSession(tmp_path)
    context = browser(session, monkeypatch)
    page = context.new_page.return_value
    readiness = iter(['loading', 'interactive', 'complete'])
    page.evaluate.side_effect = lambda _: next(readiness, 'complete')
    original = context.request.get.return_value
    times = []
    def get(*args, **kwargs):
        times.append(context.test_clock['now'])
        return original
    context.request.get.side_effect = get
    run_connect(session, Event())
    assert times == [5.0]


def test_loading_page_never_triggers_identity_request(tmp_path, monkeypatch):
    session = InstagramSession(tmp_path)
    context = browser(session, monkeypatch)
    context.new_page.return_value.evaluate.return_value = 'loading'
    with pytest.raises(SessionError, match='Tempo'):
        run_connect(session, Event(), timeout=6)
    context.request.get.assert_not_called()
    assert not session.pending_path.exists()


def test_navigation_restarts_stability_wait(tmp_path, monkeypatch):
    session = InstagramSession(tmp_path)
    context = browser(session, monkeypatch)
    page = context.new_page.return_value
    def wait(ms):
        context.test_clock['now'] += ms/1000
        if context.test_clock['now'] == 2:
            page.url = 'https://www.instagram.com/accounts/onetap/'
    page.wait_for_timeout.side_effect = wait
    run_connect(session, Event())
    assert context.test_clock['now'] == 6


def test_cancel_during_settling_does_not_capture_or_verify(tmp_path, monkeypatch):
    session = InstagramSession(tmp_path)
    context = browser(session, monkeypatch)
    cancel = Event()
    context.new_page.return_value.wait_for_timeout.side_effect = lambda _: cancel.set()
    with pytest.raises(SessionError, match='cancelada'):
        run_connect(session, cancel)
    context.request.get.assert_not_called()
    assert not session.pending_path.exists()


def test_redirect_loop_is_bounded(tmp_path, monkeypatch):
    session = InstagramSession(tmp_path)
    context = browser(session, monkeypatch)
    context.request.get.return_value.status = 302
    context.request.get.return_value.headers = {'location': '/api/v1/accounts/current_user/?edit=true'}
    with pytest.raises(SessionError, match='redirecionada'):
        run_connect(session, Event())
    context.request.get.assert_called_once()


@pytest.mark.parametrize('location', ['/accounts/login/?next=private-token', '/challenge/secret',
                                      'https://example.com/secret', 'http://www.instagram.com/api/v1/accounts/current_user/'])
def test_redirect_preserves_pending_without_following_login_or_external(tmp_path, monkeypatch, location):
    session = InstagramSession(tmp_path)
    context = browser(session, monkeypatch)
    response = context.request.get.return_value
    response.status = 302
    response.headers = {'location': location}
    with pytest.raises(SessionError, match='HTTP 302') as exc:
        run_connect(session, Event())
    assert 'secret' not in str(exc.value)
    assert 'private-token' not in str(exc.value)
    assert 'Cookies preservados' in str(exc.value)
    context.request.get.assert_called_once()
    assert not session.saved
    assert session.pending_path.exists()
    assert len(session._browser_cookies()) == 3
    with pytest.raises(SessionError, match='pendente'):
        session.load()
    session.delete()
    assert not session.pending_path.exists()


def test_redirect_does_not_confirm_identity_and_pending_can_be_verified(tmp_path, monkeypatch):
    session = InstagramSession(tmp_path)
    context = browser(session, monkeypatch)
    good = context.request.get.return_value
    redirect = Mock(status=302, headers={'location': '/api/v1/accounts/current_user/?edit=false'})
    context.request.get.side_effect = [redirect]
    with pytest.raises(SessionError, match='redirecionada'):
        run_connect(session, Event())
    redirect.dispose.assert_called_once()
    assert session.pending_path.exists()
    context.request.get.side_effect = None
    assert session.test(Event()) == 'owner'
    assert not session.pending_path.exists()
    assert session.load()['username'] == 'owner'


@pytest.mark.parametrize('html', [
    '<html>Login</html>',
    '<script type="application/json">{"user":{"id":"123","username":"author"}}</script>',
    '<script type="application/json">{"PolarisViewer":{"id":"123","username":"author"}}</script>',
    '<script type="application/json">invalid json</script>',
    '<script type="application/json">["PolarisViewer",[],{"id":"123","data":null}]</script>',
])
def test_homepage_requires_named_authenticated_viewer(html):
    from instantdownloader.core.instagram_session import viewer_identity
    with pytest.raises(SessionError):
        viewer_identity(html, '123')


def test_homepage_viewer_checks_both_ids_and_unique_username():
    from instantdownloader.core.instagram_session import viewer_identity
    assert viewer_identity(viewer_html()+viewer_html(), '123') == 'owner'
    with pytest.raises(SessionError):
        viewer_identity(viewer_html(user_id='999'), '123')
    with pytest.raises(SessionError):
        viewer_identity(viewer_html(module_id='999'), '123')
    with pytest.raises(SessionError):
        viewer_identity(viewer_html()+viewer_html(username='other'), '123')
    with pytest.raises(SessionError):
        viewer_identity(viewer_html(), '0')


def test_identity_uses_homepage_not_broken_current_user_route(tmp_path, monkeypatch):
    session = InstagramSession(tmp_path)
    context = browser(session, monkeypatch)
    assert session._identity(context) == 'owner'
    context.request.get.assert_called_once_with('https://www.instagram.com/', timeout=15000, max_redirects=0)
    context.request.get.return_value.dispose.assert_called_once()


def test_cancellation_preserves_previous_session(tmp_path, monkeypatch):
    session = InstagramSession(tmp_path)
    session.save('old', cookies())
    context = browser(session, monkeypatch)
    event = Event()
    event.set()
    with pytest.raises(SessionError, match='cancelada'):
        run_connect(session, event)
    assert session.load()['username'] == 'old'
    context.request.get.assert_not_called()


def test_timeout_and_browser_failure_are_sanitized(tmp_path, monkeypatch):
    session = InstagramSession(tmp_path)
    context = browser(session, monkeypatch)
    with pytest.raises(SessionError, match='Tempo'):
        run_connect(session, Event(), timeout=0)
    context.new_page.side_effect = RuntimeError('secret-cookie')
    with pytest.raises(SessionError) as exc:
        run_connect(session, Event())
    assert 'secret-cookie' not in str(exc.value)
    assert not session.saved


def test_connection_test_uses_existing_session(tmp_path, monkeypatch):
    session = InstagramSession(tmp_path)
    session.save('old', cookies())
    browser(session, monkeypatch)
    assert session.test(Event()) == 'owner'
    assert session.load()['username'] == 'owner'


def test_atomic_write_failure_preserves_session(tmp_path, monkeypatch):
    session = InstagramSession(tmp_path)
    session.save('old', cookies())
    monkeypatch.setattr(os, 'replace', Mock(side_effect=OSError('disk')))
    with pytest.raises(OSError):
        session.save('new', cookies())
    assert session.load()['username'] == 'old'
    assert list(session.directory.iterdir()) == [session.path]


def test_diagnostics_include_stage_and_http_without_secrets(tmp_path, monkeypatch):
    import io
    import logging
    from instantdownloader.core.instagram_session import LOG
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    level = LOG.level
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    try:
        session = InstagramSession(tmp_path)
        context = browser(session, monkeypatch)
        context.request.get.return_value.status = 403
        with pytest.raises(SessionError, match='HTTP 403') as exc:
            run_connect(session, Event())
        assert 'verify_identity' in str(exc.value)
        output = stream.getvalue()
        assert 'stage=identity_response http_status=403' in output
        assert 'stage=verify_identity' in output
        assert 'secret' not in output
        assert 'owner' not in output.lower()
        assert not session.saved
        stream.seek(0)
        stream.truncate()
        context.request.get.side_effect = RuntimeError('secret cookie in provider error')
        with pytest.raises(SessionError):
            run_connect(session, Event())
        assert 'error_type=RuntimeError' in stream.getvalue()
        assert 'secret cookie' not in stream.getvalue()
    finally:
        LOG.removeHandler(handler)
        LOG.setLevel(level)
