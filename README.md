# InstantDownloader

Versao **1.0.0**. Veja as alteracoes e limitacoes no [changelog](CHANGELOG.md).

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
python -m playwright install chromium
```

No PowerShell, use `.venv\Scripts\Activate.ps1`. Para apenas CLI, instale com `python -m pip install -e .`; descoberta de perfis exige o extra `[profiles]`.

FFmpeg e FFprobe precisam estar instalados e disponiveis no PATH. No Mac: `brew install ffmpeg`. A CLI aceita `--ffmpeg-location` como pasta ou executavel. FFmpeg ainda nao e distribuido junto do programa.

## Interface

- **Adicionar / URL:** analisar, visualizar miniatura e resolucoes, escolher pasta/qualidade e adicionar a fila ou baixar agora.
- **Adicionar / Excel:** validar e importar a planilha para a mesma fila. Nenhum download inicia durante a importacao.
- **Adicionar / Perfil:** escolher Feed, Reels ou Feed + Reels (padrao da interface), revisar a lista, selecionar videos e adicionar a fila. Mostra contagens por origem, total unico e selecionados. Consulta padrao de 10 publicacoes por origem, configuravel ate 1000. Nao baixa fotos; identifica videos de carrosseis mistos.
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

Nomes incluem titulo, ID e qualidade para evitar colisoes. MP4 compativel usa H.264/yuv420p e AAC; arquivos ja compativeis nao sao recodificados. Quando necessaria, a conversao gera `nome [compatible].mp4`. No fluxo de download, o arquivo de origem e removido somente depois da validacao da conversao, deixando apenas a versao compativel. Falhas/cancelamento antes dessa etapa preservam a origem. Nao ha limpeza retroativa da pasta; arquivos `.part` ainda podem existir durante downloads ou para retomada.

O sufixo antigo `.h264` indicava a copia convertida, nao corrupcao: a extensao continua sendo `.mp4`. Novas conversoes usam `[compatible]` no nome. Originais podem usar VP9, que nem todo player reproduz. A conversao normaliza a faixa de cor para limitada, verifica os codecs e decodifica a saida inteira antes de publica-la. O modo compativel tambem valida arquivos ja compativeis; falhas nao viram downloads concluidos. Arquivos antigos nao sao alterados automaticamente.

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

Para agentes e automacoes, veja o [contrato da CLI](docs/CLI_AUTOMACAO.md) e a
[skill portatil](skills/instantdownloader/SKILL.md). Inclui sessao dedicada,
filtros de data, manifestos revisaveis e resultados JSON.

```bash
instantdownloader profile discover "@perfil" --scope reels --limit 500 --use-session --published-before 2025-08-01 --manifest lista.json --json
instantdownloader download-list lista.json --output-dir downloads --compatible-mp4 --report reports/lote.csv --json-report reports/lote.json --json
```

Listas limitadas/incompletas exigem revisao e --allow-partial antes de baixar ou
enfileirar. Nao ha promessa de coleta integral apenas por aumentar --limit.

```bash
instantdownloader profile "@perfil" --limit 10 --scope posts --manifest perfil.json
instantdownloader profile "https://www.instagram.com/perfil/" --scope reels --limit 50 --use-session --manifest reels.json --json
instantdownloader filter-list reels.json --published-before 2025-08-01 --output filtrada.json --json
```

`--limit` conta publicacoes examinadas por origem, nao videos. `--scope all` consulta Feed e Reels e deduplica por ID; um video nas duas origens conta em ambas, mas aparece e entra na fila uma unica vez. Feed pode conter Reels compartilhados nele; consultar so Feed nao garante encontrar todos os Reels.

O JSON inclui `counts` (posts, reels, unique), `inspected`, `exhausted` por origem e `all_collected`. `complete: true` significa que a consulta limitada terminou; `all_collected: true` exige chegar ao fim de todas as origens solicitadas, sem erro. Sao contagens observadas na consulta, nao um total garantido de conteudo inacessivel/privado. Ao atingir o limite, a interface indica contagens parciais.

Resultados sao salvos progressivamente; em falhas, a lista parcial e preservada. A lista visivel contem somente a consulta atual para nao misturar origens ou cache antigo. Novas consultas comecam novamente pelo feed, aproveitando a deduplicacao da fila; ainda nao ha cursor persistido para continuar de uma pagina exata nem coleta ilimitada. Filtros por data selecionam as publicacoes ja descobertas, sem encerrar a busca pelo primeiro item fora do periodo. Em erro ou bloqueio, a consulta combinada para sem tentar a origem seguinte.

### Conectar Instagram no aplicativo

1. Abra **Configuracoes / Conexao com Instagram / Conectar Instagram** e autorize o armazenamento local da sessao.
2. Entre diretamente no Instagram na janela dedicada, incluindo 2FA e telas de salvar login quando solicitadas. Termine essas etapas e clique em **Conclui o login - verificar**, no InstantDownloader. Nao ha verificacao nem fechamento automatico por tempo enquanto voce interage. **Cancelar operacao** fecha a janela; voce tambem pode fecha-la manualmente.
3. Somente apos esse clique, o app aguarda `document.readyState=complete` e quatro segundos de estabilidade da pagina/sessao. Captura os cookies localmente como pendentes e consulta a identidade. Somente depois de confirmar mostra `Conectado como @usuario` e fecha o navegador. Se a verificacao falhar, mostra um aviso e mantem a janela aberta com cookies preservados: conclua algo pendente no site e clique novamente para verificar, ou cancele. Nao ha repeticao automatica. HTTP 429/403 deve ser respeitado: aguarde antes de uma nova tentativa.
4. **Testar conexao** verifica o login salvo. **Testar ate 20 Reels** usa o perfil informado e mostra quantidade encontrada, publicacoes examinadas, paginas, tempo e resultado parcial/fim informado. Este teste nao baixa arquivos nem altera a fila. Falha de consulta indica total desconhecido, nao zero Reels.
5. Em **Adicionar / Perfil**, a sessao dedicada salva e reutilizada automaticamente. Login confirmado nao garante que a API de Reels esteja disponivel.
6. **Apagar sessao** remove apenas os cookies salvos pelo aplicativo, nao altera seu Chrome pessoal nem revoga outras sessoes no Instagram.

O navegador dedicado e temporario; somente cookies selecionados do Instagram sao persistidos em `instagram-auth/session.json` (confirmados) ou `instagram-auth/pending.json` (pendentes), dentro da pasta de dados local do app, fora do repositorio. Nao salvamos a senha, historico do navegador ou cookies de outros sites. Cookies sao credenciais: os arquivos nao sao criptografados; no macOS/Linux tem permissao 600 e diretorio 700. No Windows, herdam a protecao da pasta do usuario. Nao compartilhe esses arquivos, inclua-os em backups publicos ou envie-os em relatorios. Cada pessoa conecta sua propria conta no proprio computador. **Apagar sessao** remove ambos; descoberta nao utiliza cookies pendentes nem recorre silenciosamente a outra conta.

A sessao dedicada e usada na descoberta de perfis, nao habilita automaticamente cookies nos downloads. As opcoes antigas estao recolhidas em **Conexao avancada (opcional)** e so sao utilizadas quando nao ha sessao dedicada salva. Para trocar a conta dedicada, apague a sessao e conecte novamente.

Erros de conexao abrem uma caixa de dialogo e ficam acessiveis em **Ver ultimo erro** durante a execucao. O terminal registra etapas, status HTTP e tipos de erro, sem valores de cookies, senhas, URLs de navegacao ou corpos de resposta. Para diagnosticar no macOS, feche a instancia anterior e execute na pasta do projeto:

```bash
source .venv/bin/activate
python -u instantdownloader_app.py 2>&1 | tee /tmp/instantdownloader-debug.log
```

Compartilhe apenas as linhas de diagnostico relevantes; nunca envie os arquivos de `instagram-auth`. A identidade e confirmada pelo modulo estruturado `PolarisViewer` da pagina inicial autenticada: os dois IDs do modulo precisam corresponder ao cookie `ds_user_id`, com username unico. Um HTTP 200, um cookie isolado ou o nome de algum autor no feed nao bastam. A antiga rota `accounts/current_user` nao e mais consultada, pois redirecionou para a home mesmo com login no navegador.

Redirecionamentos sao classificados por grupo de host (web/mobile/outro) e tipo de pagina (login/desafio/salvar login/etc.), sem registrar URL ou parametros. Nao seguimos redirects na verificacao da home; eles deixam a sessao pendente, sem fechar o navegador no fluxo interativo. A listagem de Reels continua um teste separado e exige sessao confirmada. Uma mudanca no formato da pagina pode exigir atualizacao do verificador; nunca aceitar uma identidade por suposicao.

Autenticacao opcional da CLI usa uma sessao existente do Instaloader: `--session-file CAMINHO --login USUARIO`. Nao forneca senhas. Cookies de navegador usados pelo download nao sao automaticamente compartilhados com o Instaloader.

Outra opcao explicita para descoberta: `--discovery-browser chrome --login SEU_USUARIO`, com Instagram ja conectado nesse navegador. Nao combine com `--session-file`. Na interface, selecione o navegador em **Configuracoes / Conexao avancada (opcional)**, informe seu usuario (nao o perfil-alvo), volte a Perfil e confirme a leitura quando iniciar a consulta. Somente cookies validos do Instagram sao importados para a sessao em memoria; nao sao salvos ou incluidos em relatorios. Falhas nao causam fallback anonimo. Essa autenticacao nao garante acesso quando o Instagram bloqueia consultas ou muda a API.

Com a **sessao dedicada**, Reels usa Chromium isolado em segundo plano: abre a aba Reels do perfil, acompanha as respostas da propria pagina e rola para carregar mais. Nao depende de um ID fixo de consulta GraphQL nem pede os detalhes de cada video individualmente. Reutiliza os cookies autorizados, sem ler o Chrome pessoal ou pedir outro login a cada consulta. Titulos e datas podem nao vir nessa listagem; nesse caso aparece `Reel CODIGO`, com data vazia.

Depois da descoberta, **Carregar datas** consulta as datas ausentes usando a sessao dedicada, uma publicacao por vez, sem baixar videos. A coluna **Data (UTC)** mostra `DD/MM/AAAA` ou `Nao informada`. Para selecionar apenas publicacoes anteriores ou iguais a um dia, preencha **Ate (DD/MM/AAAA)** e clique **Selecionar ate**: consulta as datas ausentes quando necessario e substitui a selecao atual. O dia informado e inclusivo, em UTC. Itens sem data confirmada ficam fora dessa selecao. Depois use **Adicionar selecionados**; o filtro sozinho nao cria downloads. A data e de publicacao no Instagram, nao da gravacao do arquivo original. Consultas grandes podem demorar; **Parar consulta** preserva datas ja obtidas. Bloqueios interrompem a operacao sem repeticao automatica.

A consulta para ao atingir o limite escolhido, ao fim informado pelo Instagram, por cancelamento/bloqueio ou apos 25 segundos sem nova pagina. Nao ha mais corte fixo em tres minutos enquanto novas paginas chegam; consultas grandes podem demorar e continuam cancelaveis. Resultados parciais sao preservados. Nao ha retomada por cursor entre execucoes nem garantia de acervo integral. O formato das respostas do site ainda pode mudar. Feed continua usando Instaloader; Feed + Reels consulta Feed primeiro e interrompe se essa origem falhar.

Sem sessao dedicada, o caminho legado (inclusive CLI) requer Instaloader 4.16+ e reutiliza seu paginador com o endpoint web. Esse caminho pode retornar uma conexao `null` mesmo com login valido; isso nao e uma lista vazia. A interface mostra `?` quando a contagem e desconhecida e `+` quando parcial; erros tecnicos ficam em **Detalhes**, sem orientar a iniciar uma fila vazia.

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
