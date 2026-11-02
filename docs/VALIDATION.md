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
- Posts e Reels sao consultados separadamente. Fotos nao entram na fila. Stories, destaques, conteudo excluido ou sem acesso autorizado nao fazem parte deste fluxo.
- Uma consulta publica pequena funcionou; grandes perfis, sessoes autenticadas e mudancas da API do Instagram precisam de testes adicionais. Nao foram usados cookies pessoais.
- A sessao para descobrir perfis (Instaloader) e a autenticacao de download (cookies do navegador) sao configuracoes distintas.
- Cancelamento e cooperativo: requisicoes em andamento e algumas etapas internas do yt-dlp podem demorar a devolver controle. Downloads parcialmente recebidos podem permanecer para retomada.
- Conversoes preservam a midia original. Nao ha limpeza automatica de arquivos do usuario.
- A fila guarda o estado atual e permite repetir tarefas; nao representa um registro imutavel de todas as tentativas. CSVs por execucao preservam os resultados de cada execucao.
- O historico legado da CLI direta identifica videos, nao exportacoes por qualidade. A fila persistente faz essa distincao e nao usa esse arquivo legado.
