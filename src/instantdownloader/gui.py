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
from .core.publication_dates import publication_day, cutoff_day, load_publication_dates, filter_publications
from .core.instagram_session import InstagramSession, SessionError, LOG as SESSION_LOG, configure_session_logging
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
        self.session_confirm = threading.Event()
        self.instagram_session = InstagramSession(self.home)
        self.last_session_error = ''
        self.closing = False
        self.info = None
        self.analyzed_url = None
        self.profile_items = []
        self.profile_result = None
        self.profile_scopes = ('posts', 'reels')
        self.thumbnail_image = None
        self.last_progress_at = 0.0
        defaults = {'output': str(Path.home()/'Downloads/InstantDownloader'), 'quality': 'Melhor disponivel',
                    'browser': 'chrome', 'theme': 'Light', 'session_file': '', 'login': '', 'profile_browser': 'Nao usar',
                    'report_dir': str(self.home/'reports')}
        self.settings = {k: ctk.StringVar(value=self.prefs.get(k, v)) for k, v in defaults.items()}
        self.settings['cookies'] = ctk.BooleanVar(value=self.prefs.get('cookies', False))
        self.settings['compatible'] = ctk.BooleanVar(value=self.prefs.get('compatible', True))
        self.url, self.excel, self.profile_url = ctk.StringVar(), ctk.StringVar(), ctk.StringVar()
        self.limit, self.scope = ctk.StringVar(value='10'), ctk.StringVar(value='Feed + Reels')
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
        row = ctk.CTkFrame(page, fg_color='transparent')
        row.grid(row=2, column=0, sticky='ew', pady=(8, 8))
        row.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(row, textvariable=self.profile_url, height=36).grid(row=0, column=0, sticky='ew')
        self.menu(row, self.scope, ['Feed', 'Reels', 'Feed + Reels'], 135).grid(row=0, column=1, padx=8)
        self.menu(row, self.limit, ['10', '25', '50', '100', '250', '500', '1000'], 65).grid(row=0, column=2)
        self.button(row, 'Descobrir', self.discover, width=100).grid(row=0, column=3, padx=(8, 0))
        self.profile_tree = self.tree(page, ('title', 'source', 'date'), ('Video', 'Origem', 'Data'), (330, 105, 105))
        self.profile_tree.heading('title', anchor='w')
        self.profile_tree.heading('date', text='Data (UTC)')
        self.profile_tree.column('source', anchor='center')
        self.profile_tree.column('date', anchor='center', width=125, minwidth=125)
        self.profile_tree.empty_label.place_configure(y=20)
        self.profile_tree.master.grid(row=3, column=0, sticky='nsew')
        row = ctk.CTkFrame(page, fg_color='transparent')
        row.grid(row=4, column=0, sticky='ew', pady=(10, 0))
        self.profile_select_button = self.button(row, 'Selecionar todos', lambda: self.profile_tree.selection_set(self.profile_tree.get_children()), True, width=125)
        self.profile_select_button.pack(side='left')
        self.profile_add_button = self.button(row, 'Adicionar selecionados', self.add_profile, width=170)
        self.profile_add_button.pack(side='left', padx=8)
        self.profile_stop_button = self.button(row, 'Parar consulta', self.worker_cancel.set, True, width=110)
        self.profile_stop_button.pack(side='right')
        for button in (self.profile_select_button, self.profile_add_button, self.profile_stop_button):
            button.configure(state='disabled')
        self.profile_feedback = self.label(page, 'Limite por origem: publicacoes examinadas, incluindo fotos.', 12, muted=True, wraplength=680, justify='left')
        self.profile_feedback.grid(row=5, column=0, sticky='ew', pady=(8, 0))
        self.profile_counts = self.label(page, 'Feed: 0 | Reels: 0 | Unicos: 0 | Selecionados: 0', 12, muted=True)
        self.profile_counts.grid(row=6, column=0, sticky='w', pady=(4, 0))
        self.profile_tree.bind('<<TreeviewSelect>>', lambda _: self.update_profile_counts())
        self.profile_actions = ctk.CTkFrame(page, fg_color='transparent')
        self.profile_actions.grid(row=7, column=0, sticky='w', pady=(8, 0))
        self.profile_session_button = self.button(self.profile_actions, 'Configurar sessao', lambda: self.show('Configuracoes'), width=145)
        self.profile_session_button.pack(side='left')
        self.button(self.profile_actions, 'Detalhes', self.profile_error_details, True, width=80).pack(side='left', padx=8)
        self.profile_actions.grid_remove()
        self.profile_cutoff = ctk.StringVar()
        filters = ctk.CTkFrame(page, fg_color='transparent')
        filters.grid(row=8, column=0, sticky='ew', pady=(8, 0))
        filters.grid_columnconfigure(1, weight=1)
        self.label(filters, 'Ate (DD/MM/AAAA)', 12).grid(row=0, column=0, padx=(0, 8))
        ctk.CTkEntry(filters, textvariable=self.profile_cutoff, placeholder_text='DD/MM/AAAA',
                     width=130, height=32).grid(row=0, column=1, sticky='ew')
        self.button(filters, 'Selecionar ate', self.select_profile_until, width=115).grid(row=0, column=2, padx=8)
        self.button(filters, 'Carregar datas', self.load_profile_dates, True, width=115).grid(row=0, column=3)

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
        self.label(page, 'Conexao com Instagram', 18, True).pack(anchor='w')
        self.session_status = ctk.StringVar(value=(
            'Cookies capturados; verificacao pendente. Teste a conexao ou reconecte.' if self.instagram_session.pending_path.exists() else
            'Sessao salva; teste a conexao.' if self.instagram_session.saved else 'Instagram desconectado.'))
        self.session_label = ctk.CTkLabel(page, textvariable=self.session_status, anchor='w', justify='left', text_color=TEXT, wraplength=620)
        self.session_label.pack(fill='x', pady=(8, 8))
        page.bind('<Configure>', lambda e: self.session_label.configure(wraplength=max(180, e.width-28)), add='+')
        row = ctk.CTkFrame(page, fg_color='transparent')
        row.pack(fill='x')
        row.grid_columnconfigure((0, 1, 2), weight=1)
        self.session_buttons = []
        for col, (title, action) in enumerate([
                ('Reconectar Instagram' if self.instagram_session.captured else 'Conectar Instagram', lambda: self.session_action('connect')),
                ('Testar conexao', lambda: self.session_action('test')),
                ('Apagar sessao', lambda: self.session_action('delete'))]):
            button = self.button(row, title, action, col != 0, width=100)
            button.grid(row=0, column=col, sticky='ew', padx=(0, 6))
            self.session_buttons.append(button)
        self.session_confirm_button = self.button(page, 'Conclui o login - verificar', self.confirm_instagram_login)
        self.session_confirm_button.pack(anchor='w', pady=(8, 8))
        self.session_confirm_button.configure(state='disabled')
        self.label(page, 'Perfil para testar Reels', muted=True).pack(anchor='w', pady=(12, 4))
        ctk.CTkEntry(page, textvariable=self.profile_url, placeholder_text='@perfil ou URL', height=36).pack(fill='x')
        test = self.button(page, 'Testar ate 20 Reels', lambda: self.session_action('reels'), True)
        test.pack(anchor='w', pady=8)
        self.session_buttons.append(test)
        self.session_cancel_button = self.button(page, 'Cancelar operacao', self.worker_cancel.set, True)
        self.session_cancel_button.pack(anchor='w', pady=(0, 16))
        self.session_cancel_button.configure(state='disabled')
        self.session_error_button = self.button(page, 'Ver ultimo erro', self.show_session_error, True)
        self.session_error_button.pack(anchor='w', pady=(0, 12))
        self.session_error_button.configure(state='disabled')
        self.label(page, 'Aparencia', 18, True).pack(anchor='w')
        self.appearance_choice = ctk.StringVar(value=next(k for k, v in APPEARANCE.items() if v == self.settings['theme'].get()))
        self.segments(page, list(APPEARANCE), variable=self.appearance_choice, command=self.change_theme).pack(anchor='w', pady=(8, 20))
        self.label(page, 'Downloads', 18, True).pack(anchor='w')
        ctk.CTkSwitch(page, text='MP4 compativel (H.264 / AAC)', variable=self.settings['compatible'], progress_color=ACCENT).pack(anchor='w', pady=12)
        ctk.CTkSwitch(page, text='Usar cookies do navegador para baixar', variable=self.settings['cookies'], progress_color=ACCENT).pack(anchor='w', pady=8)
        self.menu(page, self.settings['browser'], ['chrome', 'firefox', 'edge', 'safari']).pack(anchor='w', pady=(0, 16))
        self.label(page, 'Pasta de relatorios', bold=True).pack(anchor='w')
        self.path_field(page, 'report_dir', lambda: self.choose_directory('report_dir'))
        self.button(page, 'Conexao avancada (opcional)', self.toggle_advanced_session, True).pack(anchor='w', pady=(20, 4))
        self.advanced_session = ctk.CTkFrame(page, fg_color='transparent')
        outer = page
        page = self.advanced_session
        self.label(page, 'Opcional. Arquivo de sessao do Instaloader e usuario associado.', muted=True, wraplength=620).pack(anchor='w')
        self.label(page, 'Usuario da sessao', size=12, muted=True).pack(anchor='w', pady=(12, 0))
        ctk.CTkEntry(page, textvariable=self.settings['login'], placeholder_text='Usuario da sessao', height=36).pack(fill='x', pady=8)
        self.path_field(page, 'session_file', self.choose_session)
        self.label(page, 'Ou usar sessao do navegador para consultar perfis', size=12, muted=True).pack(anchor='w', pady=(12, 4))
        self.menu(page, self.settings['profile_browser'], ['Nao usar', 'chrome', 'firefox', 'edge', 'safari'], 180).pack(anchor='w')
        self.label(page, 'Informe acima seu usuario do Instagram. O navegador selecionado substitui o arquivo de sessao nesta consulta. A leitura de cookies pede confirmacao.', muted=True, wraplength=620, justify='left').pack(anchor='w', pady=8)
        page = outer
        self.label(page, 'Cookies e sessoes nao sao incluidos nos relatorios.\nFila sequencial; pausa automatica ao detectar limite de acesso.', muted=True, wraplength=620, justify='left').pack(anchor='w', pady=18)
        self.button(page, 'Salvar preferencias', self.save_settings, width=175).pack(anchor='w', pady=(0, 16))

    def toggle_advanced_session(self):
        if self.advanced_session.winfo_manager():
            self.advanced_session.pack_forget()
        else:
            self.advanced_session.pack(fill='x', pady=8)

    def session_action(self, action):
        if (self.worker and self.worker.is_alive()) or self.runner.running:
            self.status.set('Aguarde a tarefa atual antes de alterar ou testar a sessao.')
            return
        if action == 'connect' and not messagebox.askyesno('Conectar Instagram',
                'Vamos abrir um navegador exclusivo do aplicativo. Entre diretamente no Instagram.\n\n'
                'Permitir salvar os cookies de sessao neste computador para consultar perfis? '
                'Eles dao acesso a sua conta. Nao lemos seu Chrome pessoal nem salvamos sua senha. '
                'Voce pode apagar a sessao nas configuracoes.', parent=self):
            return
        if action == 'delete' and not messagebox.askyesno('Apagar sessao local',
                'Remover os cookies salvos pelo InstantDownloader? Seu navegador pessoal nao sera alterado. '
                'Isso nao revoga outras sessoes no Instagram.', parent=self):
            return
        profile = None
        if action == 'reels':
            try:
                profile = normalize_profile(self.profile_url.get())
            except ValueError:
                self.session_status.set('Informe um perfil valido para testar a listagem de Reels.')
                return

        self.session_confirm.clear()

        def task():
            started = time.monotonic()
            SESSION_LOG.info('ui_operation=%s result=started', action)
            try:
                if action == 'delete':
                    self.instagram_session.delete()
                    message = 'Sessao local apagada. Instagram desconectado.'
                elif action == 'connect':
                    username = self.instagram_session.connect(self.worker_cancel, self.session_confirm,
                        on_progress=lambda message: self.emit('session_progress', message),
                        on_ready=lambda: self.emit('session_login_ready', None),
                        on_attempt_error=lambda message: self.emit('session_attempt_error', message))
                    message = f'Conectado como @{username}. Sessao salva localmente. Listagem de Reels ainda nao testada.'
                elif action == 'test':
                    username = self.instagram_session.test(self.worker_cancel)
                    message = f'Login confirmado: @{username}. Listagem e downloads nao foram testados.'
                else:
                    local = self.instagram_session.load()
                    result = discover_profile(profile, limit=20, scope='reels',
                        cancel_event=self.worker_cancel, local_session=local)
                    state = ('Fim da origem informado pelo Instagram.' if result.all_collected else
                             'Limite do teste atingido; podem existir mais Reels.' if result.complete else
                             f'Consulta incompleta ({result.error_code}). Total desconhecido.\n{result.message}')
                    message = (f'Perfil: @{profile}\nReels encontrados: {len(result.items)} | '
                               f'Publicacoes examinadas: {result.inspected.get("reels", 0)} | '
                               f'Paginas: {result.pages.get("reels", 0)}\n{state}')
                    SESSION_LOG.info('ui_operation=reels complete=%s items=%d inspected=%d pages=%d error_code=%s elapsed_seconds=%.1f',
                                     result.complete, len(result.items), result.inspected.get('reels', 0),
                                     result.pages.get('reels', 0), result.error_code, time.monotonic()-started)
                    if not result.complete:
                        self.emit('session_error', f'{message}\nTempo: {time.monotonic()-started:.1f} s')
                        return
                self.emit('session_result', f'{message}\nTempo: {time.monotonic()-started:.1f} s')
            except SessionError as exc:
                SESSION_LOG.warning('ui_operation=%s result=failed error_type=SessionError elapsed_seconds=%.1f', action, time.monotonic()-started)
                self.emit('session_result' if self.worker_cancel.is_set() else 'session_error', str(exc))
            except Exception as exc:
                SESSION_LOG.error('ui_operation=%s result=failed error_type=%s elapsed_seconds=%.1f', action, type(exc).__name__, time.monotonic()-started)
                self.emit('session_error', 'Operacao nao concluida. Confira os logs do terminal.')

        if self.work(task):
            for button in self.session_buttons:
                button.configure(state='disabled')
            self.session_cancel_button.configure(state='normal')
            self.session_status.set('Entre no Instagram e conclua as telas seguintes. Depois clique em Conclui o login - verificar.' if action == 'connect' else 'Verificando...')

    def confirm_instagram_login(self):
        if self.session_confirm_button.cget('state') == 'disabled' or self.worker_cancel.is_set():
            return
        self.session_confirm_button.configure(state='disabled')
        self.session_status.set('Aguardando a pagina estabilizar e verificando a identidade...')
        self.session_confirm.set()

    def show_session_error(self):
        if self.last_session_error:
            messagebox.showerror('Falha na conexao com Instagram', self.last_session_error, parent=self)

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
            scope = {'Feed': 'posts', 'Posts': 'posts', 'Reels': 'reels', 'Feed + Reels': 'all'}[self.scope.get()]
            session, login = self.settings['session_file'].get().strip(), self.settings['login'].get().strip()
            browser = self.settings['profile_browser'].get()
            browser = None if browser == 'Nao usar' else browser
            local = self.instagram_session.load() if self.instagram_session.captured else None
            if local:
                session, login, browser = '', '', None
            if browser and not login:
                raise ValueError('Informe seu usuario do Instagram em Configuracoes antes de usar a sessao do navegador.')
        except Exception as exc:
            self.status.set(str(exc))
            return
        if browser and not messagebox.askyesno('Usar sessao do Instagram',
                f'Permitir a leitura de cookies do {browser} para esta consulta?\n'
                'Somente os cookies do Instagram serao usados. Eles ficam em memoria, sem salvar em arquivos ou relatorios.', parent=self):
            return
        def task():
            def on_item(item):
                self.store.save_discovery(profile, asdict(item))
                self.emit('discovery_item', asdict(item))
            result = discover_profile(profile, limit=limit, scope=scope, cancel_event=self.worker_cancel,
                on_item=on_item, session_file=Path(session) if session and not browser else None,
                login=login or None, browser=browser, local_session=local)
            SESSION_LOG.info('ui_operation=profile scope=%s complete=%s error_code=%s failed_source=%s items=%d inspected_posts=%d inspected_reels=%d pages_reels=%d',
                             scope, result.complete, result.error_code, result.failed_source, len(result.items),
                             result.inspected.get('posts', 0), result.inspected.get('reels', 0), result.pages.get('reels', 0))
            self.emit('discovery_done', result)
        if self.work(task):
            self.profile_items = []
            self.profile_result = None
            self.profile_scopes = ('posts', 'reels') if scope == 'all' else (scope,)
            self.profile_actions.grid_remove()
            self.profile_stop_button.configure(state='normal')
            self.render_profile()
            self.profile_tree.empty_label.configure(text='Consultando perfil...')
            self.profile_feedback.configure(text=f'Consultando @{profile}. Limite: {limit} publicacoes por origem.')
            self.status.set('Descobrindo videos...')

    def render_profile(self):
        selected = self.profile_tree.selection()
        self.profile_tree.delete(*self.profile_tree.get_children())
        for i, item in enumerate(self.profile_items):
            sources = item.get('sources', [])
            origin = 'Feed + Reels' if len(sources) == 2 else 'Reels' if 'reels' in sources else 'Feed' if 'posts' in sources else '-'
            day = publication_day(item.get('date'))
            self.profile_tree.insert('', 'end', iid=str(i), values=(item['title'], origin, day.strftime('%d/%m/%Y') if day else 'Nao informada'))
        self.profile_tree.selection_set([i for i in selected if self.profile_tree.exists(i)])
        self.profile_tree.empty_label.place_forget() if self.profile_items else self.profile_tree.empty_label.place(relx=.5, rely=.5, y=20, anchor='center')
        self.update_profile_counts()

    def update_profile_counts(self):
        def count(source):
            if source not in self.profile_scopes:
                return '-'
            value = sum(source in item.get('sources', []) for item in self.profile_items)
            result = self.profile_result
            if result and not result.exhausted.get(source, False):
                if not result.complete and not value:
                    return '?'
                return f'{value}+'
            return str(value)
        feed, reels = count('posts'), count('reels')
        self.profile_counts.configure(text=f'Feed: {feed} | Reels: {reels} | Unicos: {len(self.profile_items)} | Selecionados: {len(self.profile_tree.selection())}')
        self.profile_select_button.configure(state='normal' if self.profile_items else 'disabled')
        self.profile_add_button.configure(state='normal' if self.profile_tree.selection() else 'disabled')

    def profile_error_details(self):
        if self.profile_result:
            messagebox.showinfo('Detalhes da consulta', self.profile_result.message, parent=self)

    def select_profile_until(self):
        try:
            cutoff = cutoff_day(self.profile_cutoff.get())
        except ValueError as exc:
            self.status.set(str(exc))
            return
        if (self.worker and self.worker.is_alive()) or self.runner.running:
            self.status.set('Aguarde a tarefa atual antes de selecionar por data.')
            return
        if any(not publication_day(item.get('date')) for item in self.profile_items):
            self.load_profile_dates(cutoff)
        else:
            self.apply_profile_cutoff(cutoff)

    def apply_profile_cutoff(self, cutoff):
        selected, missing = filter_publications(self.profile_items, before=cutoff)
        identifiers = {item['id'] for item in selected}
        chosen = [str(index) for index, item in enumerate(self.profile_items) if item['id'] in identifiers]
        self.profile_tree.selection_set(chosen)
        self.update_profile_counts()
        self.status.set(f'{len(chosen)} selecionados ate {cutoff:%d/%m/%Y} (inclusive, UTC). {missing} sem data excluidos da selecao.')

    def load_profile_dates(self, cutoff=None):
        if (self.worker and self.worker.is_alive()) or self.runner.running:
            self.status.set('Aguarde a tarefa atual antes de consultar datas.')
            return
        items = [dict(item) for item in self.profile_items if not publication_day(item.get('date'))]
        if not items:
            self.status.set('Nao ha datas pendentes nesta lista.')
            return
        try:
            session = self.instagram_session.load()
            profile = normalize_profile(self.profile_url.get())
        except (SessionError, ValueError) as exc:
            self.status.set(str(exc))
            return
        def task():
            try:
                def on_date(identifier, value):
                    item = next(item for item in items if item['id'] == identifier)
                    if value:
                        item['date'] = value
                        self.store.save_discovery(profile, item)
                    self.emit('profile_date', (identifier, value))
                message = load_publication_dates(items, session, self.worker_cancel, on_date)
                self.emit('profile_dates_done', (cutoff, message))
            except Exception as exc:
                SESSION_LOG.warning('stage=publication_dates error_type=%s', type(exc).__name__)
                self.emit('profile_dates_done', (cutoff, 'Consulta de datas interrompida. Datas obtidas preservadas.'))
        if self.work(task):
            self.profile_stop_button.configure(state='normal')
            self.status.set(f'Consultando datas de {len(items)} publicacoes. Use Parar consulta para interromper.')

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
            if kind == 'profile_date':
                identifier, value = payload
                for item in self.profile_items:
                    if item['id'] == identifier and value:
                        item['date'] = value
                self.render_profile()
            elif kind == 'profile_dates_done':
                cutoff, message = payload
                self.profile_stop_button.configure(state='disabled')
                if cutoff is not None:
                    self.apply_profile_cutoff(cutoff)
                    self.status.set(message + ' ' + self.status.get())
                else:
                    self.status.set(message)
            elif kind == 'analysis':
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
                for button in self.session_buttons:
                    button.configure(state='normal')
                self.session_cancel_button.configure(state='disabled')
                self.session_confirm_button.configure(state='disabled')
                self.session_buttons[0].configure(text='Reconectar Instagram' if self.instagram_session.captured else 'Conectar Instagram')
            elif kind == 'session_progress':
                self.session_status.set(payload)
            elif kind == 'session_login_ready':
                if not self.closing and not self.worker_cancel.is_set():
                    self.session_confirm_button.configure(state='normal')
            elif kind == 'session_attempt_error':
                self.last_session_error = f'{time.strftime("%H:%M:%S")}\n{payload}'
                self.session_error_button.configure(state='normal')
                self.session_status.set(payload)
                self.status.set('Verificacao pendente. O navegador continua aberto; nenhuma nova tentativa sera automatica.')
                if not self.closing and not self.worker_cancel.is_set():
                    self.session_confirm_button.configure(state='normal')
                    messagebox.showwarning('Verificacao pendente - navegador aberto', payload, parent=self)
            elif kind == 'session_result':
                self.session_status.set(payload)
                self.status.set('Operacao de sessao encerrada. Confira o resultado nas configuracoes.')
            elif kind == 'session_error':
                # Keep the connection failure even if a later test only reports missing state.
                if not self.last_session_error or not payload.startswith(('Sessao ausente', 'Cookies capturados;')):
                    self.last_session_error = f'{time.strftime("%H:%M:%S")}\n{payload}'
                self.session_error_button.configure(state='normal')
                self.session_status.set(payload)
                self.status.set('Falha na operacao do Instagram. Consulte Ver ultimo erro e os logs do terminal.')
                if not self.closing:
                    messagebox.showerror('Operacao do Instagram nao concluida', payload, parent=self)
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
                for index, item in enumerate(self.profile_items):
                    if item['id'] == payload['id']:
                        self.profile_items[index] = payload
                        break
                else:
                    self.profile_items.append(payload)
                self.render_profile()
                self.profile_feedback.configure(text=f'{len(self.profile_items)} videos encontrados. Voce pode parar a consulta.')
            elif kind == 'discovery_done':
                self.profile_result = payload
                self.profile_stop_button.configure(state='disabled')
                self.update_profile_counts()
                if not payload.complete:
                    reasons = {
                        'session_unavailable': 'O Instagram nao disponibilizou esta consulta. Configure uma sessao autorizada e tente novamente.',
                        'browser_session': 'Nao foi possivel usar a sessao do navegador. Entre no Instagram nesse navegador e confira o usuario nas configuracoes.',
                        'rate_limited': 'O Instagram limitou as consultas. Aguarde antes de tentar novamente.',
                        'local_throttle': 'A biblioteca interrompeu a consulta por uma pausa preventiva local. Isso nao confirma um bloqueio HTTP 429 do Instagram.',
                        'cancelled': 'Consulta interrompida. A lista contem apenas os videos encontrados ate agora.',
                        'invalid_input': 'Confira o perfil e as configuracoes de sessao.',
                        'dependency_missing': 'Instale as dependencias de descoberta de perfis.',
                    }
                    message = reasons.get(payload.error_code, 'Nao foi possivel concluir a consulta. Veja os detalhes do erro.')
                    if payload.failed_source in ('posts', 'reels'):
                        origin = 'Feed' if payload.failed_source == 'posts' else 'Reels'
                        message = f'Falha na origem {origin}. {message}'
                        if self.profile_scopes == ('posts', 'reels') and payload.failed_source == 'posts':
                            message += ' Reels nao foi consultado porque a busca parou no Feed.'
                    if payload.items:
                        message += ' Resultados parciais preservados.'
                    if payload.error_code in ('session_unavailable', 'browser_session', 'invalid_input'):
                        self.profile_session_button.pack(side='left')
                    else:
                        self.profile_session_button.pack_forget()
                    self.profile_actions.grid()
                    self.status.set('Consulta incompleta. A contagem total nao foi confirmada.')
                elif not payload.items:
                    message = ('Nenhum video encontrado nas origens consultadas.' if payload.all_collected else
                               'Nenhum video encontrado no limite consultado. Podem existir mais publicacoes.')
                elif payload.all_collected:
                    message = 'Fim das origens consultadas. Revise os videos e adicione os selecionados a fila.'
                else:
                    message = 'Limite atingido. Contagens parciais; podem existir mais videos. Aumente o limite para consultar novamente.'
                self.profile_feedback.configure(text=message)
                if payload.complete:
                    self.status.set('Consulta encerrada. Adicione os selecionados e use Iniciar na Fila.' if payload.items else 'Consulta encerrada sem videos encontrados.')
                if not self.profile_items:
                    self.profile_tree.empty_label.configure(text='Consulta indisponivel' if not payload.complete else 'Nenhum video encontrado nesta consulta.')
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
    configure_session_logging()
    SESSION_LOG.info('app_started diagnostics=enabled credentials_logging=disabled')
    try:
        app = InstantDownloaderApp()
        app.mainloop()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)


if __name__ == '__main__':
    main()
