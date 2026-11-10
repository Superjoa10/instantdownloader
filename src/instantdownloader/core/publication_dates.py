"""Publication dates from the selected post, never inferred from its identifier."""
from datetime import datetime, timezone
import logging
import re
from urllib.parse import urlsplit

from .instagram_session import instagram_browser

LOG = logging.getLogger('instantdownloader.session')


def publication_day(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo:
            stamp = stamp.astimezone(timezone.utc)
        return stamp.date()
    except ValueError:
        return None


def cutoff_day(value):
    try:
        return datetime.strptime(value.strip(), '%d/%m/%Y').date()
    except ValueError:
        raise ValueError('Informe uma data valida no formato DD/MM/AAAA.') from None


def filter_publications(items, before=None, after=None):
    """Inclusive UTC-day selection shared by desktop and automation callers."""
    if before is not None and after is not None and after > before:
        raise ValueError('The start date must not be later than the end date.')
    selected, missing = [], 0
    for item in items:
        day = publication_day(item.get('date'))
        if day is None:
            missing += 1
            if before is not None or after is not None:
                continue
        elif (before is not None and day > before) or (after is not None and day < after):
            continue
        selected.append(item)
    return selected, missing


def matching_timestamp(document, code):
    stack = [document]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get('code', node.get('shortcode')) == code:
                stamp = node.get('taken_at', node.get('taken_at_timestamp'))
                if isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and stamp > 0:
                    try:
                        return datetime.fromtimestamp(stamp, timezone.utc).isoformat()
                    except (ValueError, OverflowError, OSError):
                        pass
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return None


def load_publication_dates(items, session, cancel, on_date, on_stop=None):
    """Bounded sequential lookups. Preserve resolved dates on stop or failure."""
    def stopped(code, message):
        if on_stop:
            on_stop(code)
        return message

    with instagram_browser(session['cookies']) as context:
        page = context.new_page()
        for item in items:
            if cancel.is_set():
                return stopped('cancelled', 'Consulta de datas cancelada. Datas obtidas preservadas.')
            if publication_day(item.get('date')):
                continue
            code = item['id'].split(':')[0]
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', code):
                continue
            found, blocked = [], []

            def receive(response):
                url = urlsplit(response.url)
                if url.netloc != 'www.instagram.com' or url.path.rstrip('/') not in ('/graphql/query', '/api/graphql'):
                    return
                if response.status in (401, 403, 429):
                    blocked.append(response.status)
                    return
                if response.status == 200:
                    try:
                        value = matching_timestamp(response.json(), code)
                        if value:
                            found.append(value)
                    except Exception:
                        pass

            page.on('response', receive)
            try:
                response = page.goto(f'https://www.instagram.com/p/{code}/', wait_until='domcontentloaded', timeout=30000)
                if response and response.status >= 400:
                    return stopped('rate_limited' if response.status == 429 else 'session_unavailable',
                                   f'Consulta de datas interrompida: HTTP {response.status}. Datas obtidas preservadas.')
                for _ in range(32):
                    if cancel.is_set() or blocked or found:
                        break
                    parsed = urlsplit(page.url)
                    if parsed.netloc != 'www.instagram.com' or parsed.path.rstrip('/') not in (f'/p/{code}', f'/reel/{code}'):
                        return stopped('session_unavailable', 'Consulta de datas redirecionada. Confira a sessao; datas obtidas preservadas.')
                    # Only the time linked to this exact post, never a comment's time.
                    times = page.locator(f'a[href$="/p/{code}/"] time[datetime], a[href$="/reel/{code}/"] time[datetime]')
                    values = times.evaluate_all('(nodes) => nodes.map(n => n.getAttribute("datetime"))')
                    days = {publication_day(value) for value in values if publication_day(value)}
                    if len(days) == 1:
                        found.append(next(iter(days)).isoformat())
                        break
                    page.wait_for_timeout(250)
                if blocked:
                    return stopped('rate_limited' if blocked[0] == 429 else 'session_unavailable',
                                   f'Consulta de datas interrompida: HTTP {blocked[0]}. Sem repeticao automatica.')
                if cancel.is_set():
                    return stopped('cancelled', 'Consulta de datas cancelada. Datas obtidas preservadas.')
                on_date(item['id'], found[0] if found else '')
                LOG.info('stage=publication_date found=%s', bool(found))
                page.wait_for_timeout(1000)
            finally:
                page.remove_listener('response', receive)
    return 'Consulta de datas encerrada. Itens sem data nao entram na selecao por data.'
