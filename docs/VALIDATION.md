# Validacao da implementacao

Data: 2026-11-02. Ambiente real: macOS, Python 3.12, Tk 9.0, CustomTkinter 5.2.2, Pillow 12.3.0, Instaloader 4.16, yt-dlp 2026.07.04, FFmpeg/FFprobe no PATH.

## Testes executados

Resultado final: 226 testes de nucleo/CLI passando, mais 3 testes graficos passando (229 no total). `pip check` nao encontrou dependencias inconsistentes.

- Suite automatizada: download/colecoes, indices de videos de carrosseis mistos, seletores de qualidade verticais/horizontais, arquivos ausentes/vazios, historico, conversao/cancelamento, rate limits, batch, Excel, relatorios atomicos, URLs, thumbnails, descoberta parcial, persistencia, locks e CLI.
- Testes graficos reais: navegacao nas quatro telas; alternancia de temas; adicionar/cancelar tarefa; preferencias e restauracao; janela minima de 920x720 com titulo, autor e caminho longos.
- Inicializacao real das quatro telas com janela de 1080x800.
- Fluxo grafico integrado com descoberta simulada: revisao sem download automatico, selecao de video de carrossel, persistencia, conversao sinalizada, resultado no historico e CSV com resolucao.
- Download real de um video sintetico de 1 segundo por HTTP local, exercitando extracao e gravacao do yt-dlp e leitura pelo FFprobe.
- Analise e carregamento de miniatura reais do post publico usado anteriormente.
- Descoberta anonima de um post do perfil publico desse video: consulta limitada concluida, um video encontrado. Nenhum cookie ou sessao do usuario acessado.
- Download real desse post com qualidade 480p e MP4 compativel: resultado 360x640, H.264/yuv420p com audio AAC, validado por FFprobe. Arquivo final de 13.653.471 bytes. A melhor opcao disponivel abaixo do limite era 360p; nao houve ampliacao artificial.
- Midias de teste foram criadas em diretorios temporarios e removidas automaticamente.

## Auditoria de interface

Implementados: hierarquia tipografica, navegacao lateral, espacos consistentes, duas paletas com contraste, tabelas com scroll vertical/horizontal, estados vazios e de erro, textos com quebra, miniatura proporcional sem recorte, captura de configuracao no thread principal, navegacao de botoes por teclado e fechamento cooperativo.

Checagens de layout e interacao passaram nos testes graficos. A captura inicialmente estava indisponivel; na execucao seguinte, a permissao estava disponivel e foram geradas 24 capturas (seis telas, dois temas, dois tamanhos). A inspecao das capturas levou a ajustes no contraste das abas, nas barras de rolagem, nos estados vazios e no indicador do tema selecionado. A miniatura real foi inspecionada sem recorte e com proporcao preservada. Capturas e metricas estao em `portfolio_assets/audit/`. O script consulta a permissao do sistema, sem contorna-la.

## Limitacoes conhecidas

- Windows nao foi executado neste ambiente; empacotamento e distribuicao conjunta de FFmpeg continuam pendentes.
- Perfis: consulta limitada, nao garantia de acervo completo. Nao ha cursor persistido por pagina nem filtro por data. A lista descoberta e persistente; uma nova consulta reexamina o feed e deduplica os videos encontrados.
- Feed e Reels usam consultas distintas; desde 2026-11-03, a opcao Feed + Reels combina os resultados com deduplicacao. Fotos nao entram na fila. Stories, destaques, conteudo excluido ou sem acesso autorizado nao fazem parte deste fluxo.
- Uma consulta publica pequena funcionou; grandes perfis, sessoes autenticadas e mudancas da API do Instagram precisam de testes adicionais. Nao foram usados cookies pessoais.
- A sessao para descobrir perfis (Instaloader) e a autenticacao de download (cookies do navegador) sao configuracoes distintas.
- Cancelamento e cooperativo: requisicoes em andamento e algumas etapas internas do yt-dlp podem demorar a devolver controle. Downloads parcialmente recebidos podem permanecer para retomada.
- Downloads com MP4 compativel removem a origem apos conversao validada; a funcao de conversao isolada continua nao destrutiva. Nao ha limpeza retroativa da pasta do usuario.
- A fila guarda o estado atual e permite repetir tarefas; nao representa um registro imutavel de todas as tentativas. CSVs por execucao preservam os resultados de cada execucao.
- O historico legado da CLI direta identifica videos, nao exportacoes por qualidade. A fila persistente faz essa distincao e nao usa esse arquivo legado.

## Revisao de compatibilidade e Reels (2026-11-03)

- 237 testes de nucleo/CLI passaram, incluindo conversoes reais de video sintetico full-range, com e sem audio, e rejeicao de midia invalida. Os 3 testes graficos tambem passaram (240 no total).
- Auditoria somente de leitura de 71 MP4 locais: nenhum erro na decodificacao completa com FFmpeg. Foram encontrados 24 videos VP9, 33 H.264/yuv420p e 14 H.264/yuvj420p. Isso nao comprova reproducao em todos os celulares/apps; o problema relatado pelo usuario continua dependendo dessa verificacao.
- A conversao anterior podia preservar full-range apesar de pedir yuv420p. A nova conversao normaliza a faixa de cor, valida codecs e decodifica o arquivo completo antes da troca atomica. Falhas preservam o original e a saida anterior. Nomes novos usam `[compatible].mp4`, sem o sufixo intermediario `.h264`.
- Uma copia do exemplo local foi reconvertida e validada: 576x1024, H.264/yuv420p, faixa tv, AAC, 8,366 segundos. Nenhum original foi substituido. A copia foi disponibilizada ao usuario para testar no celular; nao houve validacao fisica em celular.
- Testados Feed + Reels, contagens por origem, deduplicacao, limites por origem, interrupcao por erro/cancelamento, fila CLI e exclusao do cache antigo da lista visivel da consulta atual.
- Consulta real anonima de ate 3 Reels do perfil informado falhou em `reading reels` com `ProfileNotExistsException`. A resolucao inicial do perfil funcionou, mas a consulta de metadados feita por `get_reels()` nao. Nao e prova de perfil inexistente ou de zero Reels. Nenhum cookie pessoal foi usado; sessao autorizada e downloads reais por esse fluxo ainda precisam ser validados.
- Auditoria grafica atualizada: 24 capturas com dados ficticios isolados, dois temas e dois tamanhos, em `portfolio_assets/audit-reels/`. Conferidas as capturas de Perfil com a nova coluna Origem e contagens.

## Correcao do erro de Reels (2026-11-03)

- Investigada a falha da captura do usuario: `get_reels()` do Instaloader 4.16 refazia a busca de metadados do perfil e utilizava o endpoint GraphQL anterior. O adaptador agora reutiliza `NodeIterator` no endpoint web com o ID ja resolvido, sem alterar arquivos da dependencia.
- A pagina publica confirmou o perfil, mas a consulta anonima de Reels retornou uma conexao `null`. O adaptador trata isso como `session_unavailable`, nunca como lista vazia ou perfil inexistente. `failed_source` identifica a origem afetada na CLI.
- Implementada opcao explicita de sessao do navegador para descoberta, separada dos cookies de download. Exige usuario associado, confirmacao na interface e cookies Instagram validos em memoria. Nao exporta cookies e nao tenta anonimamente se a importacao falhar. Nenhum cookie real foi lido nesta validacao; consulta autenticada continua pendente.
- Interface: mensagem curta em portugues, detalhes tecnicos sob demanda, atalho para configurar sessao, contagem `?` quando desconhecida e botoes desabilitados sem itens/selecoes. O rodape nao orienta iniciar download apos falha ou resultado vazio.
- Suite completa com interface: 259 testes passando (253 nucleo/CLI e 6 graficos). Inclui paginacao real do Instaloader com respostas simuladas, rejeicao de respostas nulas/incompletas, filtragem de cookies por dominio/validade, leitura somente com opt-in e recusa de consentimento sem iniciar tarefa.
- Capturas do estado de erro em `portfolio_assets/audit-reels-error/`, usando apenas dados simulados. O novo acesso por navegador ainda precisa de validacao com uma sessao autorizada pelo usuario; nao representa garantia de acesso aos Reels.

## Conexao local dedicada (2026-11-06)

- Suite completa: 274 testes passaram, incluindo 7 testes graficos.
- Implementados consentimento, login em Chromium isolado, verificacao de identidade,
  persistencia atomica de cookies selecionados, teste de login, teste limitado de
  Reels, cancelamento e remocao da sessao. Cookies pessoais nao sao lidos.
- Chromium do Playwright instalado e inicializacao em contexto vazio validada,
  sem acessar o Instagram ou usar credenciais reais.
- Auditoria visual: 24 combinacoes de tela/tema/tamanho em estado temporario;
  capturas de Configuracoes revisadas em claro 920x720 e escuro 1080x800.
- Testes automatizados de autenticacao usam respostas simuladas. Login real,
  endpoint de identidade e descoberta autenticada integral ainda precisam de
  validacao com a conta do usuario. Windows nao foi executado.
- O navegador autentica; a descoberta continua pelo Instaloader. Coleta por
  rolagem no navegador e modo integral retomavel NAO foram implementados aqui.

## Espera apos login e HTTP 302 (2026-11-07)

- Log fornecido pelo usuario confirmou cookies presentes seguidos de HTTP 302
  na verificacao de identidade; destino do redirecionamento nao foi registrado
  naquela execucao. Isso nao prova falha do login nem sua validade.
- Antes de verificar: documento completamente carregado e quatro segundos de
  estabilidade. Mudanca de pagina/sessao reinicia a espera; cancelamento e timeout
  continuam ativos. Logs de etapas nao incluem URL, cookies ou identificadores.
- Redirecionamentos limitados ao mesmo host HTTPS e rota de identidade; login,
  desafio, destino diferente e loops param com diagnostico explicito.
- Cookies capturados ficam em arquivo privado pendente, separado da sessao
  confirmada. Reconectar/testar reutiliza-os; descoberta nao usa estado pendente.
- 286 testes passaram, incluindo interface, espera, cancelamento, redirects e
  retomada de verificacao simulada. Nova tentativa com login real ainda pendente.

## Confirmacao manual de login (2026-11-07)

- Logs enviados pelo usuario mostraram o mesmo 302 na conexao e em testes
  posteriores; nao ha evidencia de que apenas aguardar mais resolva o endpoint.
- O fluxo interativo agora exige o clique em `Conclui o login - verificar`.
  Nao consulta identidade nem captura cookies automaticamente enquanto o usuario
  termina o login. Nao ha timeout automatico nessa espera da interface.
- Falha de verificacao mantem navegador e worker abertos, preserva cookies e
  exige outro clique para tentar novamente. Cancelar/fechar encerra; sucesso
  confirmado salva a sessao e fecha o navegador.
- Diagnostico de redirect distingue hosts conhecidos web/mobile e tipos de
  pagina, incluindo salvar login. Nao registra URLs, parametros ou credenciais.
- 295 testes passaram, incluindo confirmacao manual, ausencia de retry automatico,
  cancelamento e controles Tkinter. Auditoria gerou 24 capturas isoladas; tela
  de configuracoes revisada em tamanho minimo. Login real continua pendente.

## Identidade pela pagina autenticada (2026-11-07)

- A captura do usuario mostrou feed autenticado e redirect da rota antiga para
  a home. Substituida a consulta `accounts/current_user` por uma leitura da home.
- Parser HTML/JSON identifica somente `PolarisViewer`, exige concordancia dos
  IDs do modulo e dos dados com `ds_user_id` e rejeita usernames conflitantes.
  HTML de login, autores do feed e resposta HTTP 200 isolada nao sao aceitos.
- Verificacao real autorizada usando apenas a sessao dedicada: HTTP 200 e
  `result=confirmed source=homepage_viewer`. Nenhum valor de cookie, username
  ou corpo da pagina foi impresso; arquivos de sessao nao foram alterados.
- 302 testes passaram. A confirmacao da sessao foi validada; descoberta de
  Reels autenticada e cobertura integral continuam pendentes de teste separado.

## Reels pela pagina autenticada (2026-11-07)

- Reproduzida a falha reportada: perfil resolvido, mas a consulta fixa do
  Instaloader retornava conexao nula em `reading reels`, mesmo com sessao valida.
  Nao era evidencia de usuario incorreto nem de perfil sem Reels.
- Com sessao dedicada, Reels agora acompanha as respostas nativas da aba do
  perfil em Chromium isolado/headless, com rolagem e sem ID fixo de documento
  GraphQL. Feed e autenticacao legada da CLI nao foram migrados neste ajuste.
- Teste real autorizado em `arealluz`: 20 Reels unicos, 20 itens examinados,
  quatro paginas HTTP 200, consulta limitada concluida em aproximadamente 11 s.
  A origem ainda indicava mais resultados (`all_collected=False`). Nenhum video
  foi baixado, nenhuma tarefa entrou na fila e nenhum cookie foi impresso.
- 329 testes passaram, incluindo interface. Cobertura nova: multiplas paginas,
  deduplicacao, limite no meio da pagina final, fim vazio confirmado, conexao
  nula/malformada, HTTP 401/403/429, cursor repetido, ausencia de novos itens,
  cancelamento, falta de progresso, callback com erro e isolamento de consultas.
- Teste da interface mostra paginas, tempo e motivo da falha; uma consulta
  incompleta indica total desconhecido em vez de sugerir zero Reels no perfil.
- Limites: 30 s para navegar, 25 s sem pagina nova ou 180 s de coleta. Nao ha
  retomada persistente de cursor. O site pode mudar o formato das respostas.
  Validado apenas o teste limitado de listagem, nao acervo integral, download
  desses itens, outros perfis ou Windows. Legendas/datas podem nao ser
  fornecidas pela lista; nesse caso usa titulo pelo codigo e data vazia.

## Diagnostico separado de Feed e Reels (2026-11-07)

- Usuario confirmou que Reels funciona na tela de Perfil, mas Feed e Feed +
  Reels falham. A captura nao permite distinguir a causa exata do Feed; o log
  anterior nao registrava o resultado dessa operacao.
- A interface agora identifica a origem que falhou e informa quando Reels nao
  foi consultado porque a busca combinada parou no Feed. Nao tenta a proxima
  origem automaticamente apos erro ou limite.
- Pausa preventiva do controlador local tem codigo `local_throttle`, separado
  de `rate_limited`; nao e apresentada como prova de HTTP 429 do Instagram.
  Logs de descoberta registram origem, codigo e contagens sem cookies.
- Nenhuma nova consulta real foi feita durante esse diagnostico. A causa da
  falha do Feed ainda precisa do resultado de Detalhes/log da operacao.

## Corte local aos 231 Reels (2026-11-07)

- Log enviado: navegacao as 21:30:05, pagina 74 HTTP 200 com 231 itens as
  21:33:07, encerramento imediatamente depois. Ainda havia progresso e
  `exhausted=False`; o corte foi o prazo local fixo de 180 s, nao inatividade
  nem uma resposta HTTP de bloqueio. A mensagem anterior confundia os motivos.
- Removido o prazo total fixo. Permanecem limite de itens (maximo 1000),
  cancelamento, deteccao de paginas sem progresso e 25 s sem nova pagina.
  Timeout de inatividade agora tem mensagem explicita e log de tempo/paginas.
- Regressao simulada coleta 250 itens por mais de 180 s sem corte prematuro.
  Nenhuma nova coleta real longa foi realizada nesta revisao.
- Feed falhou antes do primeiro item no log, sem evidencia HTTP detalhada.
  Classificacao agora distingue redirect de login/desafio de uma mensagem de
  espera ou HTTP 429. Logs/Detalhes incluem etapa, classe, status reconhecido
  na excecao e motivo permitido, sem URLs/cookies/corpos da resposta.
  Isso melhora o diagnostico; nao comprova nem corrige a causa remota do Feed.

## Datas e selecao por publicacao (2026-11-07)

- Adicionados Carregar datas e Selecionar ate na lista de perfil. Selecao
  inclusiva por dia UTC, substituindo a selecao atual, sem criar tarefas.
  Datas ausentes/invalidas sao excluidas; nao sao inferidas pelo codigo do Reel.
- Consultas sequenciais com sessao dedicada, cancelamento, parada em bloqueio
  e persistencia das datas resolvidas. Leitura de timestamp associado ao codigo
  exato ou elemento time ligado ao permalink da propria publicacao.
- Teste real limitado: post DMxomYbtYe0 retornou 2025-07-31. O primeiro seletor
  nao considerava o prefixo do perfil no permalink; corrigido e retestado.
  Nenhum download foi realizado; nao foi validada uma lista inteira de datas.
- Coluna formatada DD/MM/AAAA; origem/data centralizadas, titulo alinhado ao
  cabecalho. Auditoria em dois tamanhos/temas com dados ficticios; cabecalho
  encurtado e espaco da tabela ajustado apos detectar falta de altura minima.

## Saida unica no modo compativel (2026-11-07)

- Fluxo de download remove somente seu arquivo de origem apos a conversao
  H.264/AAC ter sido validada e a saida final confirmada. O caminho informado
  no resultado/relatorio continua sendo o da versao compativel.
- Origem preservada em falha/cancelamento antes da limpeza. Arquivo ja
  compativel retorna o mesmo caminho e nao e apagado. Funcao isolada de
  conversao mantida nao destrutiva, sem varrer arquivos existentes.
- Testes cobrem exclusao da origem apos sucesso, preservacao de arquivo
  alheio e cancelamento apos conversao sem registrar sucesso no archive.
  Validacoes existentes exercitam FFmpeg com videos sinteticos reais.
- Nenhum arquivo da pasta Downloads do usuario foi alterado nesta revisao.

## Auditoria e automacao pela CLI (2026-11-09)

- Auditados CLI, descoberta, resolucao de datas, enfileiramento, batch e
  contratos de stdout/erros. Corrigidas as lacunas registradas em
  [CLI_AUTOMACAO.md](CLI_AUTOMACAO.md), sem refazer os algoritmos de download.
- Sessao dedicada explicita, filtros inclusivos UTC e consulta de datas agora
  disponiveis sem Tkinter. GUI e CLI usam filter_publications do mesmo nucleo.
- profile discover preserva a forma antiga profile PERFIL. Manifestos v1
  atualizados atomicamente; filter-list permite nova selecao offline quando
  as datas existem. download-list valida a entrada e exige aceite explicito
  para listas limitadas/incompletas. Enfileiramento acontece depois do filtro.
- JSON de URL/Excel/fila/sessao, codigos de saida, relatorios CSV/JSON e
  progresso separado. Falhas de datas por limite/sessao nao sao confundidas
  com sucesso. Interrupcao preserva checkpoints/resultados parciais.
- 397 testes passaram, incluindo os 15 testes graficos. Teste por subprocesso
  real da CLI filtrou arquivo, enfileirou somente o selecionado e listou a fila
  em estado temporario. Excel/URL/descoberta/sessao usam provedores simulados;
  nao houve rede, login novo, download de midia nem alteracao da fila do usuario.
- Cobertura inclui datas inclusivas/UTC/ausentes, validacao antes de consultas,
  metadados de sessao sem cookies no resultado, datas apos descoberta parada,
  manifesta parcial, corrupcao de entrada, deduplicacao por video/indice,
  protecao de arquivos de entrada, rate limit deixando pendentes, repeticao
  seletiva e separacao de stdout/stderr.
- Skill portatil escrita no repositorio, sem instalacao nos clientes. YAML
  validado com o parser padrao do Ruby. O quick_validate.py oficial foi tentado,
  mas os ambientes Python nao tinham PyYAML; nao foi instalado pacote extra
  nem declarada validacao completa desse script. Uso/descoberta da skill no
  Codex e Claude ainda nao testados.
- Limitacoes mantidas: Feed legado, coleta limitada sem cursor persistente,
  autenticacao de download separada e queue run global por diretorio de dados.
  Windows e operacoes reais autenticadas novas nao foram testados nesta rodada.

## Preparacao do release 1.0.0 (2026-11-10)

- Versao atualizada em pyproject.toml e __init__.py; --version disponivel na CLI.
- Suite completa existente: 397 testes passaram, incluindo interface. Teste
  adicional do novo --version validado separadamente na suite direcionada.
- Wheel 1.0.0 gerado sem dependencias/rede; metadata e novos modulos conferidos.
  Pacote sem sessao, banco de dados ou midia local. Nao representa instalador
  nativo de desktop nem validacao de execucao no Windows.
- Changelog descreve funcionalidades e limitacoes. Revisao dos arquivos de
  publicacao nao encontrou padroes aparentes de chaves privadas/tokens; arquivos
  locais de autenticacao, downloads e artefatos continuam excluidos do Git.
