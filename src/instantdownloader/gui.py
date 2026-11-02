from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from .core.config import make_config
from .core.downloader import analyze_url
from .core.excel import read_jobs_from_excel
from .core.models import DownloadJob
from .core.profiles import discover_profile, normalize_profile
from .core.queue import QueueRunner, export_tasks
from .core.store import AppLock, Store, canonical_url, data_dir
from .core.thumbnail import load_thumbnail

ACCENT = ('#087F70', '#25B7A4')
HOVER = ('#07695D', '#199889')
BG = ('#F6F8F8', '#171B1C')
SURFACE = ('#FFFFFF', '#222829')
TEXT = ('#1C3030', '#E7F2EF')
MUTED = ('#536967', '#A3B8B4')
STATES = {'pending': 'Pendente', 'downloading': 'Baixando', 'converting': 'Convertendo',
          'success': 'Concluido', 'skipped': 'Ja existente', 'error': 'Falhou',
          'rate_limited': 'Limite atingido', 'cancelled': 'Cancelado'}
QUALITY = {'Melhor disponivel': 'best', 'Ate 1080p': '1080p', 'Ate 720p': '720p', 'Ate 480p': '480p'}
APPEARANCE = {'Claro': 'Light', 'Escuro': 'Dark', 'Sistema': 'System'}


class InstantDownloaderApp(ctk.CTk):
    def __init__(self, home: Path | None = None):
        self.home = home or data_dir()
        self.app_lock = AppLock(self.home)
        self.store = Store(self.home / 'queue.sqlite3')
        self.store.recover()
        self.prefs = self.store.preference('settings', {})
        ctk.set_appearance_mode(self.prefs.get('theme', 'Light'))
        super().__init__()
        self.title('Instant Downloader')
        self.geometry('1080x800')
        self.minsize(920, 720)
        self.configure(fg_color=BG)
        self.events = queue.Queue()
        self.runner = QueueRunner(self.store, self.emit)
        self.worker = None
        self.worker_cancel = threading.Event()
        self.closing = False
        self.info = None
        self.analyzed_url = None
        self.profile_items = []
        self.thumbnail_image = None
        self.last_progress_at = 0.0
        defaults = {'output': str(Path.home()/'Downloads/InstantDownloader'), 'quality': 'Melhor disponivel',
                    'browser': 'chrome', 'theme': 'Light', 'session_file': '', 'login': '',
                    'report_dir': str(self.home/'reports')}
        self.settings = {k: ctk.StringVar(value=self.prefs.get(k, v)) for k, v in defaults.items()}
        self.settings['cookies'] = ctk.BooleanVar(value=self.prefs.get('cookies', False))
        self.settings['compatible'] = ctk.BooleanVar(value=self.prefs.get('compatible', True))
        self.url, self.excel, self.profile_url = ctk.StringVar(), ctk.StringVar(), ctk.StringVar()
        self.limit, self.scope = ctk.StringVar(value='10'), ctk.StringVar(value='Posts')
        self.status = ctk.StringVar(value='Pronto para adicionar videos.')
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._sidebar()
        body = ctk.CTkFrame(self, fg_color='transparent')
        body.grid(row=0, column=1, sticky='nsew', padx=24, pady=22)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)
        self.heading = self.label(body, 'Adicionar downloads', 25, True)
        self.heading.grid(row=0, column=0, sticky='w', pady=(0, 18))
        self.pages = {}
        for name in self.nav:
            frame = ctk.CTkFrame(body, fg_color='transparent')
            frame.grid(row=1, column=0, sticky='nsew')
            self.pages[name] = frame
        self._add_page()
        self.trees = {}
        self._queue_page('Fila')
        self._queue_page('Historico')
        self._settings_page()
        footer = ctk.CTkFrame(body, fg_color='transparent')
        footer.grid(row=2, column=0, sticky='ew', pady=(16, 0))
        self.progress = ctk.CTkProgressBar(footer, height=5, progress_color=ACCENT)
        self.progress.pack(fill='x')
        self.progress.set(0)
        status_label = ctk.CTkLabel(footer, textvariable=self.status, anchor='w', text_color=MUTED, wraplength=700)
        status_label.pack(fill='x', pady=(7, 0))
        footer.bind('<Configure>', lambda e: status_label.configure(wraplength=max(200, e.width-4)))
        self._tree_style()
        self.show('Adicionar')
        self.refresh()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(100, self.drain)

    def label(self, parent, text, size=13, bold=False, muted=False, **kw):
        return ctk.CTkLabel(parent, text=text, font=ctk.CTkFont(size=size, weight='bold' if bold else 'normal'),
                           text_color=MUTED if muted else TEXT, anchor='w', **kw)

    def button(self, parent, text, command, secondary=False, **kw):
        button = ctk.CTkButton(parent, text=text, command=command, height=36, corner_radius=6,
            fg_color=SURFACE if secondary else ACCENT, hover_color=('#E3ECE9', '#34433F') if secondary else HOVER,
            text_color=TEXT if secondary else ('#FFFFFF', '#082F29'), **kw)
        button.bind('<Return>', lambda _: button.invoke())
        button.bind('<space>', lambda _: button.invoke())
        button.bind('<FocusIn>', lambda _: button.configure(border_width=2, border_color=ACCENT))
        button.bind('<FocusOut>', lambda _: button.configure(border_width=0))
        # CustomTkinter 5.2 draws buttons on canvases, whose labels are not tab stops by default.
        button._text_label.configure(takefocus=1)
        return button

    def _sidebar(self):
        side = ctk.CTkFrame(self, width=174, corner_radius=0, fg_color=SURFACE)
        side.grid(row=0, column=0, sticky='ns')
        side.grid_propagate(False)
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(6, weight=1)
        self.label(side, 'instant', 26, True).grid(row=0, column=0, sticky='w', padx=20, pady=(28, 0))
        self.label(side, 'DOWNLOADER', 11, muted=True).grid(row=1, column=0, sticky='w', padx=20, pady=(0, 30))
        self.nav = {}
        for i, name in enumerate(('Adicionar', 'Fila', 'Historico', 'Configuracoes'), 2):
            b = self.button(side, name, lambda n=name: self.show(n), True, width=146, anchor='w')
            b.grid(row=i, column=0, padx=12, pady=4)
            self.nav[name] = b
        self.label(side, 'ARQUIVOS LOCAIS\nFila persistente', 11, muted=True, justify='left').grid(row=7, column=0, sticky='w', padx=20, pady=24)

    def show(self, name):
        self.pages[name].tkraise()
        self.heading.configure(text={'Adicionar': 'Adicionar downloads', 'Fila': 'Sua fila', 'Historico': 'Historico', 'Configuracoes': 'Configuracoes'}[name])
        for key, b in self.nav.items():
            b.configure(fg_color=ACCENT if key == name else SURFACE, text_color=('#FFFFFF', '#082F29') if key == name else TEXT)

    def _add_page(self):
        page = self.pages['Adicionar']
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        self.source = self.segments(page, ['URL', 'Excel', 'Perfil'], command=self.select_source, height=36)
        self.source.grid(row=0, column=0, sticky='ew', pady=(0, 18))
        self.sources = {}
        for name in ('URL', 'Excel', 'Perfil'):
            frame = ctk.CTkFrame(page, fg_color='transparent')
            frame.grid(row=1, column=0, sticky='nsew')
            self.sources[name] = frame
        self._url_source()
        self._excel_source()
        self._profile_source()
        opts = ctk.CTkFrame(page, fg_color='transparent')
        opts.grid(row=2, column=0, sticky='ew', pady=(18, 0))
        opts.grid_columnconfigure(0, weight=1)
        self.label(opts, 'PASTA DE SAIDA', 11, muted=True).grid(row=0, column=0, sticky='w')
        self.label(opts, 'QUALIDADE', 11, muted=True).grid(row=0, column=2, sticky='w', padx=(18, 0))
        ctk.CTkEntry(opts, textvariable=self.settings['output'], height=36).grid(row=1, column=0, sticky='ew', pady=6)
        self.button(opts, 'Escolher', lambda: self.choose_directory('output'), True, width=85).grid(row=1, column=1, padx=(8, 0))
        self.quality_menu = self.menu(opts, self.settings['quality'], list(QUALITY), 165)
        self.quality_menu.grid(row=1, column=2, padx=(18, 0))
        self.button(opts, 'Restaurar padroes', self.restore_defaults, True, width=145).grid(row=2, column=0, sticky='w', pady=(4, 0))
        self.source.set('URL')
        self.sources['URL'].tkraise()

    def select_source(self, name):
        self.sources[name].tkraise()
        self.quality_menu.configure(values=list(QUALITY))

    def menu(self, parent, variable, values, width=120):
        return ctk.CTkOptionMenu(parent, variable=variable, values=values, width=width, height=36,
            fg_color=ACCENT, button_color=ACCENT, button_hover_color=HOVER, text_color=('#FFFFFF', '#082F29'))

    def segments(self, parent, values, **kwargs):
        return ctk.CTkSegmentedButton(parent, values=values, text_color=TEXT,
            fg_color=('#E9EFEC', '#2B3431'), unselected_color=('#E9EFEC', '#2B3431'),
            unselected_hover_color=('#DFE7E2', '#394740'), selected_color=('#C7EBDD', '#174E42'),
            selected_hover_color=('#B4DDCF', '#226752'), corner_radius=6, **kwargs)

    def _url_source(self):
        page = self.sources['URL']
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)
        self.label(page, 'Link do video', bold=True).grid(row=0, column=0, sticky='w')
        row = ctk.CTkFrame(page, fg_color='transparent')
        row.grid(row=1, column=0, sticky='ew', pady=(6, 18))
        row.grid_columnconfigure(0, weight=1)
        entry = ctk.CTkEntry(row, textvariable=self.url, height=40)
        entry.grid(row=0, column=0, sticky='ew')
        entry.bind('<Return>', lambda _: self.analyze())
        self.analyze_button = self.button(row, 'Analisar', self.analyze, width=105)
        self.analyze_button.grid(row=0, column=1, padx=(10, 0))
        preview = ctk.CTkFrame(page, fg_color=SURFACE, corner_radius=8)
        preview.grid(row=2, column=0, sticky='nsew')
        preview.grid_columnconfigure(1, weight=1)
        preview.grid_rowconfigure(0, weight=1)
        self.thumb = ctk.CTkLabel(preview, text='Sem previa', width=200, height=250, fg_color=('#E8EFED', '#2D3835'), corner_radius=6, text_color=MUTED)
        self.thumb.grid(row=0, column=0, padx=16, pady=16, sticky='ns')
        detail = ctk.CTkFrame(preview, fg_color='transparent')
        detail.grid(row=0, column=1, sticky='nsew', padx=(0, 18), pady=20)
        detail.grid_columnconfigure(0, weight=1)
        detail.grid_rowconfigure(3, weight=1)
        self.preview_title = self.label(detail, 'Seu proximo download', 20, True, wraplength=350, justify='left')
        self.preview_title.grid(row=0, column=0, sticky='ew')
        self.preview_author = self.label(detail, 'Analise um link para ver a previa.', muted=True, wraplength=350, justify='left')
        self.preview_author.grid(row=1, column=0, sticky='ew', pady=(10, 16))
        self.preview_meta = self.label(detail, '', wraplength=350, justify='left')
        self.preview_meta.grid(row=2, column=0, sticky='ew')
        actions = ctk.CTkFrame(detail, fg_color='transparent')
        actions.grid(row=4, column=0, sticky='ew')
        self.button(actions, 'Adicionar a fila', self.add_url, width=140).pack(anchor='w', pady=(0, 8))
        self.button(actions, 'Baixar agora', lambda: self.add_url(True), True, width=140).pack(anchor='w')
        detail.bind('<Configure>', lambda e: [w.configure(wraplength=max(190, e.width-8)) for w in (self.preview_title, self.preview_author, self.preview_meta)])

    def _excel_source(self):
        page = self.sources['Excel']
        self.label(page, 'Importar planilha', 20, True).pack(anchor='w', pady=(4, 12))
        self.label(page, 'Colunas: url, filename, folder, quality', muted=True).pack(anchor='w', pady=(0, 16))
        ctk.CTkEntry(page, textvariable=self.excel, height=38).pack(fill='x')
        row = ctk.CTkFrame(page, fg_color='transparent')
        row.pack(fill='x', pady=12)
        self.button(row, 'Escolher Excel', self.choose_excel, True).pack(side='left')
        self.button(row, 'Importar para fila', self.import_excel).pack(side='left', padx=10)
        self.excel_feedback = self.label(page, '', wraplength=650, justify='left', muted=True)
        self.excel_feedback.pack(anchor='w', pady=16)

    def _profile_source(self):
        page = self.sources['Perfil']
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(3, weight=1)
        self.label(page, 'Perfil do Instagram', 20, True).grid(row=0, column=0, sticky='w')
        self.label(page, 'EXPERIMENTAL  /  A listagem pode exigir uma sessao autenticada.', 11, muted=True).grid(row=1, column=0, sticky='w', pady=(4, 12))
        row = ctk.CTkFrame(page, fg_color='transparent')
        row.grid(row=2, column=0, sticky='ew', pady=(0, 12))
        row.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(row, textvariable=self.profile_url, height=36).grid(row=0, column=0, sticky='ew')
        self.menu(row, self.scope, ['Posts', 'Reels'], 90).grid(row=0, column=1, padx=8)
        self.menu(row, self.limit, ['10', '25', '50', '100', '250', '500', '1000'], 65).grid(row=0, column=2)
        self.button(row, 'Descobrir', self.discover, width=100).grid(row=0, column=3, padx=(8, 0))
        self.profile_tree = self.tree(page, ('title', 'date'), ('Video', 'Data'), (420, 115))
        self.profile_tree.master.grid(row=3, column=0, sticky='nsew')
        row = ctk.CTkFrame(page, fg_color='transparent')
        row.grid(row=4, column=0, sticky='ew', pady=(10, 0))
        self.button(row, 'Selecionar todos', lambda: self.profile_tree.selection_set(self.profile_tree.get_children()), True, width=125).pack(side='left')
        self.button(row, 'Adicionar selecionados', self.add_profile, width=170).pack(side='left', padx=8)
        self.button(row, 'Parar consulta', self.worker_cancel.set, True, width=110).pack(side='right')
        self.profile_feedback = self.label(page, 'Limite por consulta: quantidade de posts examinados.', 12, muted=True, wraplength=680, justify='left')
        self.profile_feedback.grid(row=5, column=0, sticky='ew', pady=(8, 0))

    def tree(self, parent, columns, headings, widths):
        frame = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=6)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=columns, show='headings', selectmode='extended', height=7)
        for col, title, width in zip(columns, headings, widths):
            tree.heading(col, text=title)
            tree.column(col, width=width, minwidth=80, stretch=col == columns[0])
        tree.grid(row=0, column=0, sticky='nsew', padx=(1, 0), pady=(1, 0))
        scroll = ctk.CTkScrollbar(frame, orientation='vertical', command=tree.yview,
            fg_color=SURFACE, button_color=('#B8C9C3', '#50665F'), button_hover_color=ACCENT)
        scroll.grid(row=0, column=1, sticky='ns')
        horizontal = ctk.CTkScrollbar(frame, orientation='horizontal', command=tree.xview,
            fg_color=SURFACE, button_color=('#B8C9C3', '#50665F'), button_hover_color=ACCENT)
        horizontal.grid(row=1, column=0, sticky='ew')
        def sync_scrollbar(bar, first, last):
            bar.set(first, last)
            full = float(last) - float(first) >= .999
            bar.configure(button_color=SURFACE if full else ('#B8C9C3', '#50665F'),
                          button_hover_color=SURFACE if full else ACCENT)
        tree.configure(yscrollcommand=lambda a, b: sync_scrollbar(scroll, a, b),
                       xscrollcommand=lambda a, b: sync_scrollbar(horizontal, a, b))
        tree.empty_label = self.label(frame, 'Nenhum item por aqui.', muted=True)
        tree.empty_label.place(relx=.5, rely=.5, anchor='center')
        return tree

    def _queue_page(self, name):
        page = self.pages[name]
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        row = ctk.CTkFrame(page, fg_color='transparent')
        row.grid(row=0, column=0, sticky='ew', pady=(0, 16))
        if name == 'Fila':
            self.start_button = self.button(row, 'Iniciar fila', self.start_queue, width=125)
            self.start_button.pack(side='left')
            self.button(row, 'Parar', self.stop_queue, True, width=80).pack(side='left', padx=8)
            self.button(row, 'Cancelar selecionados', self.cancel_selected, True, width=165).pack(side='left')
        else:
            self.button(row, 'Repetir selecionados', self.retry_selected, width=160).pack(side='left')
            self.button(row, 'Abrir pasta', self.open_selected, True, width=100).pack(side='left', padx=8)
            self.button(row, 'Exportar CSV', self.export_history, True, width=110).pack(side='right')
        tree = self.tree(page, ('title', 'status'), ('Video / origem', 'Estado'), (440, 135))
        tree.master.grid(row=1, column=0, sticky='nsew')
        self.trees[name] = tree
        detail = ctk.CTkTextbox(page, height=112, fg_color=SURFACE, corner_radius=6, text_color=TEXT, wrap='word')
        detail.grid(row=2, column=0, sticky='ew', pady=(12, 0))
        detail.insert('1.0', 'Selecione um item para ver os detalhes.')
        detail.configure(state='disabled')
        tree.bind('<<TreeviewSelect>>', lambda _, t=tree, d=detail: self.show_details(t, d))

    def _settings_page(self):
        page = ctk.CTkScrollableFrame(self.pages['Configuracoes'], fg_color='transparent')
        page.pack(fill='both', expand=True)
        self.label(page, 'Aparencia', 18, True).pack(anchor='w')
        self.appearance_choice = ctk.StringVar(value=next(k for k, v in APPEARANCE.items() if v == self.settings['theme'].get()))
        self.segments(page, list(APPEARANCE), variable=self.appearance_choice, command=self.change_theme).pack(anchor='w', pady=(8, 20))
        self.label(page, 'Downloads', 18, True).pack(anchor='w')
        ctk.CTkSwitch(page, text='MP4 compativel (H.264 / AAC)', variable=self.settings['compatible'], progress_color=ACCENT).pack(anchor='w', pady=12)
        ctk.CTkSwitch(page, text='Usar cookies do navegador para baixar', variable=self.settings['cookies'], progress_color=ACCENT).pack(anchor='w', pady=8)
        self.menu(page, self.settings['browser'], ['chrome', 'firefox', 'edge', 'safari']).pack(anchor='w', pady=(0, 16))
        self.label(page, 'Pasta de relatorios', bold=True).pack(anchor='w')
        self.path_field(page, 'report_dir', lambda: self.choose_directory('report_dir'))
        self.label(page, 'Sessao para descobrir perfis', 18, True).pack(anchor='w', pady=(20, 4))
        self.label(page, 'Opcional. Arquivo de sessao do Instaloader e usuario associado.', muted=True, wraplength=620).pack(anchor='w')
        self.label(page, 'Usuario da sessao', size=12, muted=True).pack(anchor='w', pady=(12, 0))
        ctk.CTkEntry(page, textvariable=self.settings['login'], placeholder_text='Usuario da sessao', height=36).pack(fill='x', pady=8)
        self.path_field(page, 'session_file', self.choose_session)
        self.label(page, 'Cookies e sessoes nao sao incluidos nos relatorios.\nFila sequencial; pausa automatica ao detectar limite de acesso.', muted=True, wraplength=620, justify='left').pack(anchor='w', pady=18)
        self.button(page, 'Salvar preferencias', self.save_settings, width=175).pack(anchor='w', pady=(0, 16))

    def path_field(self, parent, key, command):
        row = ctk.CTkFrame(parent, fg_color='transparent')
        row.pack(fill='x', pady=6)
        ctk.CTkEntry(row, textvariable=self.settings[key], height=36).pack(side='left', fill='x', expand=True)
        self.button(row, 'Escolher', command, True, width=85).pack(side='left', padx=(8, 0))

    def _tree_style(self):
        dark = ctk.get_appearance_mode() == 'Dark'
        style = ttk.Style(self)
        style.theme_use('clam')
        bg, fg, head = ('#222829', '#E7F2EF', '#2D3835') if dark else ('#FFFFFF', '#1C3030', '#EAF0EE')
        style.configure('Treeview', background=bg, fieldbackground=bg, foreground=fg, rowheight=36, borderwidth=0,
            bordercolor=bg, lightcolor=bg, darkcolor=bg, relief='flat', font=('TkDefaultFont', 12))
        style.configure('Treeview.Heading', background=head, foreground=fg, relief='flat', padding=(10, 9), font=('TkDefaultFont', 12, 'bold'))
        style.map('Treeview', background=[('selected', '#087F70')], foreground=[('selected', '#FFFFFF')])
        style.map('Treeview.Heading', background=[('active', head)])

    def change_theme(self, value):
        value = APPEARANCE.get(value, value)
        self.settings['theme'].set(value)
        if hasattr(self, 'appearance_choice'):
            self.appearance_choice.set(next(k for k, v in APPEARANCE.items() if v == value))
        ctk.set_appearance_mode(value)
        self._tree_style()

    def choose_directory(self, key):
        value = filedialog.askdirectory(parent=self)
        if value:
            self.settings[key].set(value)

    def choose_excel(self):
        value = filedialog.askopenfilename(parent=self, filetypes=[('Excel', '*.xlsx *.xlsm')])
        if value:
            self.excel.set(value)

    def choose_session(self):
        value = filedialog.askopenfilename(parent=self, title='Sessao do Instaloader')
        if value:
            self.settings['session_file'].set(value)

    def restore_defaults(self):
        self.settings['output'].set(self.prefs.get('output', str(Path.home()/'Downloads/InstantDownloader')))
        self.settings['quality'].set(self.prefs.get('quality', 'Melhor disponivel'))

    def save_settings(self):
        self.prefs = {key: var.get() for key, var in self.settings.items()}
        self.store.save_preference('settings', self.prefs)
        self.status.set('Preferencias salvas.')

    def config(self):
        if not self.settings['output'].get().strip():
            raise ValueError('Escolha a pasta de saida.')
        config = make_config(output_dir=Path(self.settings['output'].get()).expanduser().resolve(),
            quality=QUALITY[self.settings['quality'].get()], use_cookies=self.settings['cookies'].get(),
            browser=self.settings['browser'].get(), compatible_mp4=self.settings['compatible'].get())
        # The queue tracks exports by quality and destination, unlike a URL-only download archive.
        config.download_archive = None
        return config

    def emit(self, kind, payload):
        if kind == 'transfer':
            now = time.monotonic()
            if now - self.last_progress_at < .15 and payload.get('status') == 'downloading':
                return
            self.last_progress_at = now
        self.events.put((kind, payload))

    def work(self, target):
        if (self.worker and self.worker.is_alive()) or self.runner.running:
            self.status.set('Aguarde a tarefa atual ou pare a fila antes de analisar.')
            return False
        self.worker_cancel.clear()
        self.analyze_button.configure(state='disabled')
        def run():
            try:
                target()
            except Exception as exc:
                self.emit('error', str(exc))
            finally:
                self.emit('worker_done', None)
        self.worker = threading.Thread(target=run, daemon=False)
        self.worker.start()
        return True

    def analyze(self):
        try:
            url, config = canonical_url(self.url.get()), self.config()
        except Exception as exc:
            self.status.set(str(exc))
            return
        def task():
            info = analyze_url(url, config)
            self.emit('analysis', (url, info))
            if info.thumbnail and not self.worker_cancel.is_set():
                try:
                    self.emit('thumbnail', (url, load_thumbnail(info.thumbnail)))
                except Exception:
                    self.emit('thumbnail_missing', url)
        if self.work(task):
            self.status.set('Analisando video...')
            self.thumb.configure(image=None, text='Carregando previa...')

    def add_url(self, start=False):
        try:
            url, cfg = canonical_url(self.url.get()), self.config()
            title = self.info.title if self.info and url == self.analyzed_url else url
            _, added = self.store.add(DownloadJob(url), cfg, title)
            self.refresh()
            self.status.set('Video adicionado a fila.' if added else 'Este download ja esta na fila ou no historico.')
            self.show('Fila')
            if start:
                self.start_queue()
        except Exception as exc:
            self.status.set(str(exc))

    def import_excel(self):
        try:
            path, config = self.excel.get(), self.config()
            if not path:
                raise ValueError('Escolha uma planilha.')
        except Exception as exc:
            self.status.set(str(exc))
            return
        def task():
            jobs = read_jobs_from_excel(path)
            added = sum(self.store.add(job, config)[1] for job in jobs)
            self.emit('imported', f'{added} novos itens; {len(jobs)-added} ja cadastrados.')
        self.work(task)

    def discover(self):
        try:
            profile, limit = normalize_profile(self.profile_url.get()), int(self.limit.get())
            scope = self.scope.get().lower()
            session, login = self.settings['session_file'].get().strip(), self.settings['login'].get().strip()
        except Exception as exc:
            self.status.set(str(exc))
            return
        def task():
            def on_item(item):
                self.store.save_discovery(profile, asdict(item))
                self.emit('discovery_item', asdict(item))
            result = discover_profile(profile, limit=limit, scope=scope, cancel_event=self.worker_cancel,
                on_item=on_item, session_file=Path(session) if session else None, login=login or None)
            self.emit('discovery_done', result.message)
        if self.work(task):
            self.profile_items = self.store.discoveries(profile)
            self.render_profile()
            self.profile_feedback.configure(text=f'Consultando @{profile}. Limite: {limit} posts. Resultados anteriores preservados.')
            self.status.set('Descobrindo videos...')

    def render_profile(self):
        self.profile_tree.delete(*self.profile_tree.get_children())
        for i, item in enumerate(self.profile_items):
            self.profile_tree.insert('', 'end', iid=str(i), values=(item['title'], str(item['date'])[:10]))
        self.profile_tree.empty_label.place_forget() if self.profile_items else self.profile_tree.empty_label.place(relx=.5, rely=.5, anchor='center')

    def add_profile(self):
        try:
            selected = self.profile_tree.selection()
            if not selected:
                raise ValueError('Selecione os videos antes de adicionar.')
            config = self.config()
            added = 0
            for i in selected:
                item = self.profile_items[int(i)]
                added += self.store.add(DownloadJob(url=item['url'], playlist_index=item.get('playlist_index')), config, item['title'])[1]
            self.refresh()
            self.status.set(f'{added} videos adicionados. Revise a fila e clique em Iniciar.')
            self.show('Fila')
        except Exception as exc:
            self.status.set(str(exc))

    def refresh(self):
        rows = self.store.rows()
        for name, tree in self.trees.items():
            selected = tree.selection()
            tree.delete(*tree.get_children())
            for row in rows:
                if (row['status'] in ('pending', 'downloading', 'converting')) == (name == 'Fila'):
                    tree.insert('', 'end', iid=str(row['id']), values=(row['title'], STATES.get(row['status'], row['status'])))
            tree.selection_set([i for i in selected if tree.exists(i)])
            tree.empty_label.place_forget() if tree.get_children() else tree.empty_label.place(relx=.5, rely=.5, anchor='center')
        pending = sum(r['status'] == 'pending' for r in rows)
        self.start_button.configure(text=f'Iniciar fila ({pending})', state='disabled' if self.runner.running or not pending else 'normal')

    def show_details(self, tree, widget):
        row = next((r for r in self.store.rows() if str(r['id']) in tree.selection()), None)
        if row:
            job, config = json.loads(row['job']), json.loads(row['config'])
            text = f"{row['title']}\n{job['url']}\nDestino: {row['output_path'] or config['output_dir']}\n{row['error'] or STATES.get(row['status'], row['status'])}"
            widget.configure(state='normal')
            widget.delete('1.0', 'end')
            widget.insert('1.0', text)
            widget.configure(state='disabled')

    def start_queue(self):
        if self.worker and self.worker.is_alive():
            self.status.set('Aguarde a analise ou descoberta terminar.')
        elif not self.settings['report_dir'].get().strip():
            self.status.set('Escolha a pasta de relatorios nas configuracoes.')
        elif not any(r['status'] == 'pending' for r in self.store.rows()):
            self.status.set('Nenhum item pendente.')
        else:
            self.runner.start(Path(self.settings['report_dir'].get()).expanduser().resolve())
            self.refresh()
            self.show('Fila')

    def stop_queue(self):
        self.runner.stop()
        self.status.set('Parando a tarefa atual; os proximos itens permanecem pendentes.')

    def cancel_selected(self):
        for selected in self.trees['Fila'].selection():
            self.store.cancel(int(selected))
        self.refresh()
        self.status.set('Pendentes selecionados cancelados. Use Parar para interromper o item ativo.')

    def retry_selected(self):
        for selected in self.trees['Historico'].selection():
            self.store.retry(int(selected))
        self.refresh()
        self.show('Fila')

    def open_selected(self):
        row = next((r for r in self.store.rows() if str(r['id']) in self.trees['Historico'].selection()), None)
        if not row:
            return
        path = Path(row['output_path']).parent if row['output_path'] else Path(json.loads(row['config'])['output_dir'])
        if not path.is_dir():
            self.status.set('A pasta nao existe mais.')
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', str(path)])
        elif os.name == 'nt':
            os.startfile(path)
        else:
            subprocess.Popen(['xdg-open', str(path)])

    def export_history(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension='.csv', initialfile='historico.csv', filetypes=[('CSV', '*.csv')])
        if path:
            try:
                export_tasks(self.store, Path(path))
                self.status.set(f'Relatorio: {path}')
            except Exception as exc:
                self.status.set(str(exc))

    def drain(self):
        for _ in range(100):
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'analysis':
                self.analyzed_url, self.info = payload
                title = self.info.title or 'Video sem titulo'
                author = self.info.uploader or 'Autor indisponivel'
                self.preview_title.configure(text=title[:137]+'...' if len(title)>140 else title)
                self.preview_author.configure(text=author[:77]+'...' if len(author)>80 else author)
                resolution = f'{self.info.width} x {self.info.height}' if self.info.width and self.info.height else 'Resolucao indisponivel'
                duration = f'{int(self.info.duration)//60}:{int(self.info.duration)%60:02d}' if self.info.duration else 'Duracao indisponivel'
                self.preview_meta.configure(text=f'{resolution}\n{duration}')
                dimensions = sorted({(f.width, f.height) for f in self.info.formats
                                     if f.width and f.height and f.vcodec != 'none'}, key=lambda d: min(d), reverse=True)
                if dimensions:
                    available = ', '.join(f'{w}x{h}' for w, h in dimensions[:4])
                    self.preview_meta.configure(text=f'{resolution}  /  {duration}\n\nDisponiveis: {available}')
                    options = ['Melhor disponivel'] + [f'Ate {n}p' for n in (1080, 720, 480) if any(min(d) <= n for d in dimensions)]
                    self.quality_menu.configure(values=options)
                    if self.settings['quality'].get() not in options:
                        self.settings['quality'].set('Melhor disponivel')
                self.status.set('Analise concluida. Escolha o destino e adicione a fila.')
                if not self.info.thumbnail:
                    self.thumb.configure(image=None, text='Miniatura indisponivel')
            elif kind == 'thumbnail' and payload[0] == self.analyzed_url:
                image = payload[1]
                scale = min(200/image.width, 260/image.height)
                self.thumbnail_image = ctk.CTkImage(light_image=image, dark_image=image, size=(int(image.width*scale), int(image.height*scale)))
                self.thumb.configure(image=self.thumbnail_image, text='')
            elif kind == 'thumbnail_missing':
                self.thumb.configure(image=None, text='Miniatura indisponivel')
            elif kind == 'worker_done':
                self.analyze_button.configure(state='normal')
            elif kind in ('queue_status', 'error'):
                self.status.set(payload)
                if kind == 'error':
                    self.thumb.configure(image=None, text='Previa indisponivel')
            elif kind == 'refresh':
                self.refresh()
            elif kind == 'transfer':
                if payload['fraction'] is not None:
                    self.progress.set(min(.99, max(0, payload['fraction'])))
                if payload['status'] in ('converting', 'processing'):
                    self.status.set('Preparando MP4 compativel...')
            elif kind == 'queue_done':
                self.after(100, self.refresh)
                self.progress.set(0)
            elif kind == 'report':
                self.status.set(f'Execucao encerrada. Relatorio: {payload}')
            elif kind == 'imported':
                self.excel_feedback.configure(text=payload)
                self.status.set(payload)
                self.refresh()
                self.show('Fila')
            elif kind == 'discovery_item':
                if not any(i['id'] == payload['id'] for i in self.profile_items):
                    self.profile_items.append(payload)
                    self.render_profile()
                self.profile_feedback.configure(text=f'{len(self.profile_items)} videos encontrados. Voce pode parar a consulta.')
            elif kind == 'discovery_done':
                self.profile_feedback.configure(text=payload)
                self.status.set('Consulta encerrada. Revise os resultados antes de adicionar.')
        if self.closing and not self.runner.running and not (self.worker and self.worker.is_alive()):
            self.app_lock.close()
            self.destroy()
        else:
            self.after(100, self.drain)

    def close(self):
        busy = self.runner.running or (self.worker and self.worker.is_alive())
        if busy and not messagebox.askyesno('Encerrar', 'Parar a tarefa atual e fechar? Os itens pendentes permanecem salvos.', parent=self):
            return
        self.closing = True
        self.runner.stop()
        self.worker_cancel.set()
        self.status.set('Encerrando com seguranca...')


def main():
    try:
        app = InstantDownloaderApp()
        app.mainloop()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)


if __name__ == '__main__':
    main()
