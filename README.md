# InstantDownloader

CLI e aplicativo desktop em CustomTkinter para videos, planilhas Excel e descoberta experimental de perfis do Instagram. Python 3.10+; validado no macOS. Windows ainda requer validacao real.

## Abrir o aplicativo

Na pasta do projeto, com a `.venv` existente:

```bash
.venv/bin/python instantdownloader_app.py
```

Windows:

```powershell
.venv\Scripts\python.exe instantdownloader_app.py
```

Tambem: `instantdownloader gui` ou `instantdownloader-gui`, depois da instalacao.

## Instalar

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[gui,profiles,dev]"
```

No PowerShell, use `.venv\Scripts\Activate.ps1`. Para apenas CLI, instale com `python -m pip install -e .`; descoberta de perfis exige o extra `[profiles]`.

FFmpeg e FFprobe precisam estar instalados e disponiveis no PATH. No Mac: `brew install ffmpeg`. A CLI aceita `--ffmpeg-location` como pasta ou executavel. FFmpeg ainda nao e distribuido junto do programa.

## Interface

- **Adicionar / URL:** analisar, visualizar miniatura e resolucoes, escolher pasta/qualidade e adicionar a fila ou baixar agora.
- **Adicionar / Excel:** validar e importar a planilha para a mesma fila. Nenhum download inicia durante a importacao.
- **Adicionar / Perfil:** consultar posts ou Reels, revisar a lista, selecionar videos e adicionar a fila. Consulta padrao de 10 posts, configuravel ate 1000. Nao baixa fotos; identifica videos de carrosseis mistos.
- **Fila:** iniciar ou retomar pendentes, acompanhar download/conversao, cancelar pendentes ou parar o item ativo. Parar preserva os proximos itens. O item ativo cancelado pode ser repetido no Historico.
- **Historico:** consultar resultados e erros, repetir itens, abrir pasta e exportar CSV.
- **Configuracoes:** aparencia clara/escura/sistema, compatibilidade MP4, cookies explicitos, sessao opcional para perfis, pasta de relatorios e preferencias salvas.

`Restaurar padroes` recupera pasta e qualidade salvas. Cada tarefa mantem uma copia de suas configuracoes: mudar a tela nao altera downloads ja enfileirados.

## CLI

```bash
instantdownloader analyze "URL"
instantdownloader analyze "URL" --json
instantdownloader download "URL" --output-dir downloads --quality 720p --compatible-mp4
instantdownloader download "URL_DO_CARROSSEL" --playlist-index 2
instantdownloader batch links.xlsx --report reports/lista.csv
```

`--quality 720p` limita o lado menor a 720 pixels, inclusive videos verticais. Seleciona a melhor opcao dentro do limite; nao aumenta a resolucao e nao ultrapassa silenciosamente o limite. Dimensoes desconhecidas nao satisfazem esse limite. `best` nao limita resolucao. Seletores brutos do yt-dlp continuam aceitos.

Nomes incluem titulo, ID e qualidade para evitar colisoes. MP4 compativel usa H.264/yuv420p e AAC; arquivos ja compativeis nao sao recodificados. Quando necessaria, a conversao gera `.h264.mp4` e preserva o original.

Downloads diretos geram CSV automaticamente, ou no caminho de `--report`. O batch atualiza o CSV a cada resultado. O historico legado `download_archive.txt` continua disponivel na CLI direta: ele e por video, nao por qualidade. `--no-archive` ignora esse historico, mas nao sobrescreve arquivos existentes.

### Fila persistente compartilhada

```bash
instantdownloader queue add "URL" --output-dir downloads --quality 1080p --compatible-mp4
instantdownloader queue list --json
instantdownloader queue run --report-dir reports
instantdownloader queue retry 3 5
```

O desktop e `queue run` nao executam simultaneamente. A fila persistente e sequencial e distingue destino, qualidade e compatibilidade. A CLI direta pode usar concorrencia ate 2 no batch; com arquivo de historico, usa 1 para evitar disputas.

### Perfis (experimental)

```bash
instantdownloader profile "@perfil" --limit 10 --scope posts --manifest perfil.json
instantdownloader profile "https://www.instagram.com/perfil/" --scope reels --limit 50 --enqueue
instantdownloader queue run
```

`--limit` conta posts examinados, nao videos. `complete: true` significa que a consulta limitada terminou, nao que todo o perfil foi coletado. Resultados sao salvos progressivamente; em falhas, a lista parcial e preservada. Novas consultas comecam novamente pelo feed, aproveitando a deduplicacao; ainda nao ha cursor persistido para continuar de uma pagina exata, filtro por data ou coleta ilimitada.

Autenticacao opcional usa uma sessao existente do Instaloader: `--session-file CAMINHO --login USUARIO`. Nao forneca senhas. Na interface, essas opcoes ficam em Configuracoes. Cookies de navegador usados pelo download nao sao automaticamente compartilhados com o Instaloader.

Perfis privados exigem acesso autorizado. O Instagram pode limitar consultas, invalidar sessoes ou mudar a API. HTTP 429 interrompe a coleta; nao ha tentativa de contornar bloqueios. Um teste publico pequeno nao garante coleta integral de todos os perfis.

## Excel

Coluna obrigatoria: `url`. Opcionais: `filename`, `folder`, `quality`, `playlist_index`. Indices de carrossel sao 1-based e contam somente videos, ignorando fotos.

URLs invalidas, selecoes duplicadas e cabecalhos repetidos geram erros com as linhas envolvidas; a planilha inteira e validada antes de enfileirar. Para exportacoes alternativas da mesma URL, importe separadamente com outra configuracao.

## Dados e relatorios

SQLite e preferencias ficam em:

- macOS: `~/Library/Application Support/InstantDownloader/`
- Windows: `%LOCALAPPDATA%\InstantDownloader\`
- Linux: `$XDG_DATA_HOME/instantdownloader/` ou `~/.local/share/instantdownloader/`

`INSTANTDOWNLOADER_HOME` permite escolher outro diretorio, inclusive para testes isolados. O desktop sugere `~/Downloads/InstantDownloader` para midia. Cada execucao da fila tem seu proprio CSV, atualizado atomicamente, com itens pendentes, estados, caminhos absolutos, resolucao e erros. Cookies e conteudo de sessoes nao sao exportados. Metadados que poderiam virar formulas ao abrir no Excel sao escapados.

## Verificar

```bash
.venv/bin/python -m pytest -q
INSTANTDOWNLOADER_GUI_TESTS=1 .venv/bin/python -m pytest tests/test_gui.py -q
.venv/bin/python scripts/visual_audit.py
```

O teste grafico exige desktop. A auditoria no macOS percorre temas e tamanhos e captura somente a janela do app quando existe permissao de Gravacao de Tela; sem ela, grava apenas metricas em `portfolio_assets/audit/layout.json`.

Veja `docs/VALIDATION.md` para os testes reais e limitacoes desta entrega.

## Melhorias planejadas

Veja [docs/MELHORIAS.md](docs/MELHORIAS.md) para a proposta de skills para Codex e Claude, automacoes pela CLI com URL/Excel/perfil e os requisitos para coleta integral retomavel. A proposta esta documentada; as skills ainda nao foram criadas nem instaladas.
