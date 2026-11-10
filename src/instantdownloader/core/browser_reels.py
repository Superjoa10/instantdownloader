"""Discover Reels from the dedicated browser's own profile queries."""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .instagram_session import instagram_browser
from .profiles import DiscoveredVideo, DiscoveryResult

LOG = logging.getLogger('instantdownloader.session')
SHORTCODE = re.compile(r'[A-Za-z0-9_-]{1,64}')
CONNECTIONS = {'clips_connection', 'xdt_api__v1__clips__user__connection_v2'}


class ReelsResponseError(Exception):
    pass


def reel_connection(document):
    if not isinstance(document, dict) or document.get('errors'):
        raise ReelsResponseError('O Instagram retornou erro na listagem de Reels.')
    stack, found = [document.get('data')], []
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if key in CONNECTIONS:
                    found.append(value)
                else:
                    stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    if len(found) != 1 or not isinstance(found[0], dict):
        raise ReelsResponseError('A pagina nao disponibilizou uma listagem valida de Reels.')
    connection = found[0]
    edges, page = connection.get('edges'), connection.get('page_info')
    if (not isinstance(edges, list) or not isinstance(page, dict)
            or type(page.get('has_next_page')) is not bool
            or (page['has_next_page'] and (not edges or not isinstance(page.get('end_cursor'), str) or not page['end_cursor']))):
        raise ReelsResponseError('Paginacao de Reels incompleta; o total nao foi confirmado.')
    return edges, page


def reel_item(edge):
    media = edge.get('node', {}).get('media', {})
    code = media.get('code')
    if not isinstance(code, str) or not SHORTCODE.fullmatch(code):
        raise ReelsResponseError('Reel sem identificador valido na listagem.')
    caption = media.get('caption')
    title = caption.get('text') if isinstance(caption, dict) else None
    images = media.get('image_versions2') or {}
    candidates = images.get('candidates') or []
    thumbnail = candidates[0].get('url', '') if candidates else ''
    date = ''
    if isinstance(media.get('taken_at'), (int, float)):
        try:
            date = datetime.fromtimestamp(media['taken_at'], timezone.utc).isoformat()
        except (OverflowError, ValueError, OSError):
            pass
    return DiscoveredVideo(code, f'https://www.instagram.com/reel/{code}/',
                           title or f'Reel {code}', thumbnail, date, sources=('reels',))


class ReelCollector:
    def __init__(self, limit, on_item=None):
        self.limit = limit
        self.on_item = on_item
        self.result = DiscoveryResult([], False, '', inspected={'reels': 0}, exhausted={'reels': False})
        self.seen = set()
        self.cursors = set()
        self.target_id = None
        self.pages = 0
        self.done = False

    def fail(self, code, message):
        self.result.complete = False
        self.result.exhausted['reels'] = False
        self.result.error_code = code
        self.result.failed_source = 'reels'
        self.result.message = message
        self.done = True

    def receive(self, response):
        if self.done:
            return
        parsed = urlsplit(response.url)
        if parsed.netloc != 'www.instagram.com' or parsed.path not in {'/api/graphql', '/api/graphql/', '/graphql/query', '/graphql/query/'}:
            return
        try:
            request = response.request.post_data_json or {}
            name = request.get('fb_api_req_friendly_name', '')
        except Exception:
            return
        if not isinstance(name, str) or not name.startswith('PolarisProfileReelsTabContentQuery'):
            return
        try:
            variables = request.get('variables', {})
            variables = json.loads(variables) if isinstance(variables, str) else variables
            target = variables.get('user_id') or (variables.get('data') or {}).get('target_user_id')
            if not target:
                raise ReelsResponseError('Consulta de Reels sem identificacao do perfil.')
            if self.target_id is None:
                self.target_id = str(target)
            elif str(target) != self.target_id:
                return
            LOG.info('stage=reels_response http_status=%d page=%d', response.status, self.pages+1)
            if response.status in (401, 403, 429):
                self.fail('rate_limited' if response.status == 429 else 'session_unavailable',
                          f'O Instagram recusou a listagem (HTTP {response.status}). Nenhuma repeticao automatica foi feita.')
                return
            if response.status != 200:
                raise ReelsResponseError(f'Falha HTTP {response.status} na listagem de Reels.')
            edges, page = reel_connection(response.json())
            cursor = page.get('end_cursor')
            if page['has_next_page'] and cursor in self.cursors:
                raise ReelsResponseError('O Instagram repetiu uma pagina. Coleta interrompida sem confirmar o total.')
            if cursor:
                self.cursors.add(cursor)
            self.pages += 1
            self.result.pages['reels'] = self.pages
            inspected_before = self.result.inspected['reels']
            unique_before = len(self.seen)
            for edge in edges:
                if self.result.inspected['reels'] >= self.limit:
                    break
                item = reel_item(edge)
                self.result.inspected['reels'] += 1
                if item.id not in self.seen:
                    self.seen.add(item.id)
                    self.result.items.append(item)
                    if self.on_item:
                        self.on_item(item)
            capped = self.result.inspected['reels'] >= self.limit
            # An unconsumed tail of the last page is still a partial collection.
            consumed_page = len(edges) <= self.limit - inspected_before
            if page['has_next_page'] and len(self.seen) == unique_before:
                raise ReelsResponseError('A pagina nao trouxe novos Reels. Coleta interrompida sem confirmar o total.')
            if capped or not page['has_next_page']:
                self.result.complete = True
                self.result.exhausted['reels'] = not page['has_next_page'] and consumed_page
                self.result.message = ('Fim da listagem de Reels.' if self.result.all_collected else
                                       'Limite do teste atingido; podem existir mais Reels.')
                self.done = True
            LOG.info('stage=reels_page pages=%d items=%d inspected=%d exhausted=%s', self.pages,
                     len(self.result.items), self.result.inspected['reels'], self.result.exhausted['reels'])
        except Exception as exc:
            LOG.warning('stage=reels_response error_type=%s', type(exc).__name__)
            self.fail('discovery_failed', str(exc) if isinstance(exc, ReelsResponseError) else
                      'Nao foi possivel interpretar a pagina de Reels. Resultados parciais preservados.')


def discover_browser_reels(profile, limit, local_session, cancel_event=None, on_item=None):
    collector = ReelCollector(limit, on_item)
    try:
        with instagram_browser(local_session['cookies']) as context:
            page = context.new_page()
            page.on('response', collector.receive)
            if cancel_event and cancel_event.is_set():
                collector.fail('cancelled', 'Consulta cancelada.')
                return collector.result
            LOG.info('stage=reels_navigation provider=dedicated_browser')
            response = page.goto(f'https://www.instagram.com/{profile}/reels/',
                                 wait_until='domcontentloaded', timeout=30000)
            if response and response.status >= 400:
                collector.fail('rate_limited' if response.status == 429 else 'session_unavailable',
                               f'A pagina de Reels recusou o acesso (HTTP {response.status}).')
            started = progress_at = scroll_at = time.monotonic()
            pages = collector.pages
            while not collector.done:
                if cancel_event and cancel_event.is_set():
                    collector.fail('cancelled', 'Consulta cancelada. Resultados parciais preservados.')
                    break
                parsed = urlsplit(page.url)
                if parsed.netloc not in {'www.instagram.com', 'instagram.com'} or parsed.path.rstrip('/') != f'/{profile}/reels':
                    collector.fail('session_unavailable', 'A pagina desviou da aba Reels. Confira a sessao e o acesso ao perfil.')
                    break
                now = time.monotonic()
                if pages != collector.pages:
                    pages, progress_at = collector.pages, now
                if now-progress_at > 25:
                    LOG.warning('stage=reels_stop reason=idle_timeout elapsed_seconds=%.1f idle_seconds=%.1f pages=%d items=%d',
                                now-started, now-progress_at, collector.pages, len(collector.result.items))
                    collector.fail('discovery_incomplete', 'Nenhuma nova pagina recebida por 25 segundos. '
                                   'Resultados parciais preservados; o total de Reels nao foi confirmado. '
                                   'Isso nao confirma bloqueio do Instagram.')
                    break
                if collector.pages and now-scroll_at >= 3:
                    page.mouse.wheel(0, 1600)
                    scroll_at = now
                page.wait_for_timeout(250)
            page.remove_listener('response', collector.receive)
    except Exception as exc:
        LOG.warning('stage=reels_navigation error_type=%s', type(exc).__name__)
        collector.fail('discovery_failed', 'Consulta no navegador interrompida. Resultados parciais preservados.')
    return collector.result
