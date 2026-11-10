"""Explicit local Instagram authentication; never reads a personal browser."""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path
from threading import Event
from urllib.parse import urljoin, urlsplit

from .profiles import normalize_profile

COOKIE_NAMES = {'sessionid', 'csrftoken', 'ds_user_id', 'mid', 'ig_did', 'rur', 'datr'}
LOG = logging.getLogger('instantdownloader.session')
LOGIN_SETTLE_SECONDS = 4.0


def configure_session_logging() -> None:
    """Log only controlled diagnostics, never provider text, URLs or cookie values."""
    if not LOG.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s [instagram-session] %(message)s'))
        LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    LOG.propagate = False


class SessionError(Exception):
    pass


@contextmanager
def instagram_browser(cookies: list[dict], visible: bool = False):
    """Isolated browser shared by login and authenticated discovery."""
    LOG.info('stage=browser_start visible=%s', visible)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SessionError('Instale o extra profiles e o Chromium do Playwright. Veja o README.') from None
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=not visible)
        except Exception:
            raise SessionError('Chromium indisponivel. Execute: python -m playwright install chromium') from None
        try:
            context = browser.new_context(accept_downloads=False)
            if cookies:
                context.add_cookies(instagram_cookies(cookies))
            yield context
        finally:
            LOG.info('stage=browser_close')
            try:
                browser.close()
            except Exception as exc:
                LOG.warning('stage=browser_close error_type=%s', type(exc).__name__)


def instagram_cookies(cookies: list[dict]) -> list[dict]:
    now = time.time()
    return [c for c in cookies
            if c.get('domain', '').lstrip('.') in {'instagram.com', 'www.instagram.com'}
            and c.get('name') in COOKIE_NAMES
            and (c.get('expires', -1) == -1 or c['expires'] > now)]


def redirect_description(target: str) -> tuple[str, str]:
    """Return fixed labels, never a URL, hostname supplied by a server, or token."""
    parsed = urlsplit(target)
    hosts = {'www.instagram.com': 'instagram_web', 'instagram.com': 'instagram_root',
             'i.instagram.com': 'instagram_mobile_api'}
    host = hosts.get(parsed.netloc, 'other_host') if parsed.scheme == 'https' else 'non_https'
    if host in {'other_host', 'non_https'}:
        return host, 'other_destination'
    for prefix, category in (
        ('/accounts/login', 'login'), ('/challenge', 'challenge'),
        ('/checkpoint', 'challenge'), ('/two_factor', 'two_factor'),
        ('/accounts/onetap', 'save_login_prompt'), ('/accounts/edit', 'account_settings'),
        ('/consent', 'consent'), ('/accounts/suspended', 'account_restricted'),
    ):
        if parsed.path.startswith(prefix):
            return host, category
    if parsed.path.rstrip('/') == '/api/v1/accounts/current_user':
        return host, 'identity_endpoint'
    return host, 'home' if parsed.path == '/' else 'other_instagram_page'


class _ViewerParser(HTMLParser):
    """Read the authenticated viewer module, not profile/feed author objects."""
    def __init__(self, expected_id: str):
        super().__init__()
        self.expected_id = expected_id
        self.in_json = False
        self.parts: list[str] = []
        self.usernames: set[str] = set()

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.in_json = dict(attrs).get('type') == 'application/json'
            self.parts = []

    def handle_data(self, data):
        if self.in_json:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag != 'script' or not self.in_json:
            return
        self.in_json = False
        try:
            document = json.loads(''.join(self.parts))
        except (ValueError, RecursionError):
            return
        finally:
            self.parts = []
        stack = [document]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                if len(node) >= 3 and node[0] == 'PolarisViewer':
                    module = node[2]
                    data = module.get('data') if isinstance(module, dict) else None
                    if not isinstance(data, dict):
                        continue
                    if str(module.get('id')) != self.expected_id or str(data.get('id')) != self.expected_id:
                        raise SessionError('A identidade da pagina nao corresponde a sessao capturada. Reconecte a conta desejada.')
                    try:
                        self.usernames.add(normalize_profile(data['username']))
                    except (KeyError, ValueError, TypeError):
                        raise SessionError('O Instagram retornou uma identidade incompleta.') from None
                else:
                    stack.extend(node)
            elif isinstance(node, dict):
                stack.extend(node.values())


def viewer_identity(html: str, expected_id: str) -> str:
    if not isinstance(expected_id, str) or not expected_id.isdecimal() or int(expected_id) <= 0:
        raise SessionError('Identificador da sessao ausente ou invalido. Reconecte o Instagram.')
    if len(html) > 20_000_000:
        raise SessionError('Pagina de verificacao maior que o limite esperado.')
    parser = _ViewerParser(expected_id)
    parser.feed(html)
    parser.close()
    if len(parser.usernames) != 1:
        raise SessionError('A pagina nao confirmou uma identidade unica de usuario conectado. '
                           'A sessao permanece pendente; nenhum perfil do feed sera usado como identidade.')
    return next(iter(parser.usernames))


class InstagramSession:
    def __init__(self, home: Path):
        self.directory = home / 'instagram-auth'
        self.path = self.directory / 'session.json'
        self.pending_path = self.directory / 'pending.json'

    @property
    def saved(self) -> bool:
        return self.path.is_file()

    @property
    def captured(self) -> bool:
        return self.saved or self.pending_path.is_file()

    def load(self) -> dict:
        if self.pending_path.exists():
            raise SessionError('Cookies capturados; verificacao pendente. Use Testar conexao ou Conectar Instagram para retomar.')
        LOG.info('stage=load saved_file=%s', self.saved)
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            username = normalize_profile(data['username'])
            cookies = instagram_cookies(data['cookies'])
            values = {c['name']: c['value'] for c in cookies}
            if not values.get('sessionid') or not values.get('csrftoken'):
                raise ValueError()
            return {'username': username, 'cookies': cookies}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            LOG.warning('stage=load result=missing_invalid_or_expired')
            raise SessionError('Sessao ausente, invalida ou expirada. Clique em Conectar Instagram.') from None

    def save(self, username: str, cookies: list[dict]) -> None:
        username = normalize_profile(username)
        cookies = instagram_cookies(cookies)
        if not {'sessionid', 'csrftoken'} <= {c['name'] for c in cookies if c.get('value')}:
            raise SessionError('Login incompleto. Nenhuma nova sessao foi salva.')
        self._write_state(self.path, {'username': username, 'cookies': cookies})
        self.pending_path.unlink(missing_ok=True)

    def _write_state(self, path: Path, data: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != 'nt':
            self.directory.chmod(0o700)
        fd, name = tempfile.mkstemp(prefix='.session-', dir=self.directory)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
            LOG.info('stage=save result=ok verified=%s', path == self.path)
        finally:
            Path(name).unlink(missing_ok=True)

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)
        self.pending_path.unlink(missing_ok=True)
        LOG.info('stage=delete result=ok')

    def _browser_cookies(self) -> list[dict]:
        if not self.pending_path.exists():
            return self.load()['cookies']
        try:
            data = json.loads(self.pending_path.read_text(encoding='utf-8'))
            cookies = instagram_cookies(data['cookies'])
            if not {'sessionid', 'csrftoken', 'ds_user_id'} <= {c['name'] for c in cookies if c.get('value')}:
                raise ValueError()
            return cookies
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise SessionError('Cookies pendentes invalidos ou expirados. Reconecte o Instagram.') from None

    @contextmanager
    def _browser(self, visible: bool):
        cookies = []
        if self.captured:
            try:
                cookies = self._browser_cookies()
            except SessionError:
                pass
        with instagram_browser(cookies, visible) as context:
            yield context

    @staticmethod
    def _identity(context) -> str:
        LOG.info('stage=identity_request source=homepage_viewer')
        cookies = instagram_cookies(context.cookies())
        values = {c['name']: c['value'] for c in cookies}
        if not all(values.get(name) for name in ('sessionid', 'csrftoken', 'ds_user_id')):
            raise SessionError('Sessao expirada ou login nao concluido. Reconecte o Instagram.')
        url = 'https://www.instagram.com/'
        response = context.request.get(url, timeout=15000, max_redirects=0)
        try:
            LOG.info('stage=identity_response http_status=%d', response.status)
            if response.status in (301, 302, 303, 307, 308):
                target = urljoin(url, response.headers.get('location', ''))
                host, category = redirect_description(target)
                LOG.info('stage=identity_redirect http_status=%d host_group=%s destination=%s', response.status, host, category)
                raise SessionError(f'Verificacao redirecionada (HTTP {response.status}; {host}; {category}). '
                                   'A pagina autenticada nao foi confirmada; a sessao permanece pendente.')
            if response.status in (401, 403, 429):
                raise SessionError(f'Instagram recusou a verificacao de identidade (HTTP {response.status}). '
                                   'O login no site pode ter funcionado, mas a sessao nao foi confirmada. '
                                   'Aguarde antes de tentar novamente.')
            if response.status != 200:
                raise SessionError(f'Nao foi possivel confirmar o login no Instagram (HTTP {response.status}).')
            username = viewer_identity(response.text(), values['ds_user_id'])
            current = {c['name']: c['value'] for c in instagram_cookies(context.cookies())}
            if (current.get('ds_user_id') != values['ds_user_id'] or
                    not current.get('sessionid') or not current.get('csrftoken')):
                raise SessionError('A sessao mudou durante a verificacao. Confirme novamente a conta desejada.')
            LOG.info('stage=identity_response result=confirmed source=homepage_viewer')
            return username
        finally:
            response.dispose()

    def connect(self, cancel: Event, confirm_event: Event, timeout: float | None = None,
                on_progress: Callable[[str], None] | None = None,
                on_ready: Callable[[], None] | None = None,
                on_attempt_error: Callable[[str], None] | None = None) -> str:
        stage = 'browser_start'
        captured = False
        LOG.info('operation=connect result=started')
        try:
            with self._browser(visible=True) as context:
                stage = 'open_login_page'
                LOG.info('stage=%s', stage)
                page = context.new_page()
                page.goto('https://www.instagram.com/accounts/login/', timeout=30000, wait_until='domcontentloaded')
                stage = 'waiting_for_confirmation'
                LOG.info('stage=%s timeout_seconds=%s', stage, timeout)
                if on_ready:
                    on_ready()
                previous_state = None
                stable_since = None
                stable_key = None
                deadline = time.monotonic() + timeout if timeout is not None else None
                verifying = False
                last_error = None
                while deadline is None or time.monotonic() < deadline:
                    if cancel.is_set():
                        raise SessionError('Conexao cancelada.')
                    if page.is_closed():
                        raise SessionError('Janela fechada pelo usuario. Verificacao nao concluida.')
                    if not verifying:
                        if not confirm_event.is_set():
                            page.wait_for_timeout(500)
                            continue
                        confirm_event.clear()
                        verifying = True
                        stable_since, stable_key = None, None
                        LOG.info('stage=manual_confirmation result=received')
                    current_cookies = instagram_cookies(context.cookies())
                    values = {c['name']: c['value'] for c in current_cookies if c.get('value')}
                    names = set(values)
                    current_url = page.url
                    parsed = urlsplit(current_url)
                    blocked_page = (parsed.scheme != 'https' or parsed.netloc not in {'www.instagram.com', 'instagram.com'}
                                    or any(marker in parsed.path for marker in
                                           ('/accounts/login', '/challenge', '/two_factor', '/checkpoint', '/accounts/suspended')))
                    state = tuple(name in names for name in ('sessionid', 'csrftoken', 'ds_user_id')) + (blocked_page,)
                    if state != previous_state:
                        LOG.info('stage=waiting_for_login session_cookie=%s csrf_cookie=%s user_cookie=%s login_or_challenge_page=%s', *state)
                        previous_state = state
                    if not all(state[:3]) or blocked_page:
                        verifying = False
                        last_error = 'Login ainda nao pronto. Termine as etapas no navegador e clique novamente em Conclui o login - verificar.'
                        LOG.info('stage=manual_confirmation result=login_not_ready browser_kept_open=True')
                        if on_attempt_error:
                            on_attempt_error(last_error)
                        continue
                    if all(state[:3]) and not blocked_page:
                        try:
                            loaded = page.evaluate('document.readyState') == 'complete'
                        except Exception:
                            # A navigation can replace the execution context while polling.
                            loaded = False
                        key = (current_url, values['sessionid'], values['ds_user_id'])
                        if not loaded:
                            stable_since, stable_key = None, None
                        elif stable_since is None or stable_key != key:
                            stable_since, stable_key = time.monotonic(), key
                            LOG.info('stage=settling_page ready_state=complete stable_seconds_required=%s', LOGIN_SETTLE_SECONDS)
                            if on_progress:
                                on_progress('Pagina carregada. Aguardando estabilizar a sessao antes de verificar...')
                        if stable_since is None or time.monotonic()-stable_since < LOGIN_SETTLE_SECONDS:
                            page.wait_for_timeout(500)
                            continue
                        LOG.info('stage=settling_page result=stable')
                        if cancel.is_set():
                            raise SessionError('Conexao cancelada.')
                        stage = 'capture_session'
                        self._write_state(self.pending_path, {'cookies': current_cookies})
                        captured = True
                        stage = 'verify_identity'
                        if on_progress:
                            on_progress('Cookies capturados localmente. Verificando a identidade no Instagram...')
                        try:
                            username = self._identity(context)
                        except Exception as exc:
                            last_error = (str(exc) if isinstance(exc, SessionError) else
                                          f'Consulta de identidade interrompida ({type(exc).__name__}).')
                            LOG.warning('operation=connect result=verification_pending stage=%s error_type=%s browser_kept_open=True', stage, type(exc).__name__)
                            verifying = False
                            confirm_event.clear()
                            if on_attempt_error:
                                on_attempt_error(last_error + '\nCookies preservados. O navegador continua aberto. '
                                                 'Conclua as etapas pendentes e verifique novamente, ou cancele.')
                            continue
                        if cancel.is_set():
                            raise SessionError('Conexao cancelada.')
                        stage = 'save_session'
                        self.save(username, context.cookies())
                        LOG.info('operation=connect result=success')
                        return username
                    stable_since, stable_key = None, None
                    page.wait_for_timeout(500)
                raise SessionError(last_error or 'Tempo de login encerrado. Clique em Conectar Instagram para tentar novamente.')
        except SessionError as exc:
            LOG.warning('operation=connect result=failed stage=%s error_type=SessionError', stage)
            outcome = ('Cookies preservados localmente; verificacao pendente.' if captured else 'Nenhuma nova sessao foi salva.')
            raise SessionError(f'{exc}\nEtapa: {stage}. {outcome}') from None
        except Exception as exc:
            LOG.error('operation=connect result=failed stage=%s error_type=%s', stage, type(exc).__name__)
            outcome = ('Cookies preservados localmente; verificacao pendente.' if captured else 'Nenhuma nova sessao foi salva.')
            raise SessionError(f'Conexao interrompida na etapa {stage} ({type(exc).__name__}). '
                               f'{outcome} Consulte os logs do terminal.') from None

    def test(self, cancel: Event) -> str:
        LOG.info('operation=test result=started')
        self._browser_cookies()
        if cancel.is_set():
            raise SessionError('Teste cancelado.')
        try:
            with self._browser(visible=False) as context:
                username = self._identity(context)
                if cancel.is_set():
                    raise SessionError('Teste cancelado.')
                self.save(username, context.cookies())
                LOG.info('operation=test result=success')
                return username
        except SessionError:
            LOG.warning('operation=test result=failed error_type=SessionError')
            raise
        except Exception as exc:
            LOG.error('operation=test result=failed error_type=%s', type(exc).__name__)
            raise SessionError('Nao foi possivel testar a sessao. Confira sua conexao e tente novamente.') from None
