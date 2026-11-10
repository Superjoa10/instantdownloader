# Descoberta integral de Reels

Pesquisa e verificacao em 2026-11-06.

## Objetivo

Selecionar uma conta publica e exibir todos os Reels publicamente acessiveis
da aba Reels, sem pedir credenciais do Instagram ao cliente e sem abrir um
navegador. Downloads continuam separados, usando o motor existente.

Nao confundir videos encontrados no feed com a listagem da aba Reels. Um Reel
pode estar nas duas origens; a deduplicacao deve usar seu ID/shortcode.
Conteudo privado, removido ou restrito nao entra na promessa de cobertura.

## Evidencias, nao promessas

### Implementacao local

- `core/profiles.py` usa Instaloader para descoberta; yt-dlp baixa URLs individuais.
- O adaptador de Reels usa uma consulta GraphQL com ID fixo e NodeIterator.
- Uma consulta tem limite de 1 a 1000 publicacoes por origem. Nao existe modo
  integral retomavel com cursor persistido.
- O adaptador transforma cada resultado em `Post.from_shortcode`, podendo
  produzir requisicoes adicionais por Reel. Para listar links isso e dispensavel:
  a futura descoberta deve aproveitar os campos da propria pagina de resultados.
- O modo combinado cria uma descoberta independente por origem. Uma futura
  implementacao deve compartilhar cliente HTTP e controle de requisicoes.
- `complete` significa termino da consulta limitada. `all_collected` exige
  esgotamento declarado das origens; ainda nao prova cobertura absoluta do perfil.

### Teste real desta pesquisa

Perfil: `dietolive.clo`, ja utilizado nos testes anteriores pelo usuario.

1. Instaloader resolveu a pagina publica e retornou o ID `58614670300`.
2. Uma consulta anonima POST a `/api/v1/clips/user/`, rota utilizada pelo
   extrator REST do gallery-dl, retornou HTTP 401.
3. Resposta JSON: `status: fail`, mensagem
   `Please wait a few minutes before you try again.`; sem lista nem paginacao.
4. Nenhuma nova tentativa foi feita apos essa resposta.

Nao houve leitura de cookies pessoais, login, navegador, download de midia ou
chamada a servico pago. O teste empregou uma sessao HTTP nova e headers basicos,
nao o programa gallery-dl completo: portanto, nao prova que toda configuracao
do gallery-dl falha. Tambem nao prova que toda descoberta anonima e impossivel.
Prova apenas que essa alternativa nao resolveu a listagem neste teste.

A tentativa inicial no sandbox falhou na resolucao DNS; o resultado HTTP acima
e da execucao autorizada com rede. Nao confundir as duas falhas.

## Alternativas avaliadas

| Caminho | Dados de login do cliente | Avaliacao |
| --- | --- | --- |
| Instaloader/HTTP local | Nao no modo anonimo | Manter como tentativa limitada; nao prometer cobertura integral sem paginacao real validada |
| gallery-dl | Pode operar anonimamente, dependendo da rota/resposta | Outra implementacao, nao outra fonte de dados; rota REST testada nao resolveu |
| API oficial Meta | Autorizacao e tokens | Adequada a integracoes profissionais autorizadas; nao e uma API anonima universal de perfis |
| Provedor gerenciado de descoberta | Nao necessariamente exige login do Instagram | Melhor candidato para a experiencia desejada; requer conta/chave do provedor e validacao de custo/cobertura |
| Playwright com login | Sim | Alternativa opcional, nao atende a preferencia principal; cookies e manutencao continuam existindo |
| Busca na web / sites gratuitos | Geralmente nao | Nao demonstram cobertura integral; nao usar como prova de que encontramos todos |

### Candidato concreto: Apify mantido pela propria Apify

Actor: `apify/instagram-reel-scraper`.

O esquema publicado aceita username/URL/ID e `resultsLimit`, sem campo de
credencial Instagram do cliente. Retorna IDs, URLs e metadados. O servico
tem cobranca por eventos e extras; preco final precisa ser confirmado antes
do piloto. Nao foi executado nesta pesquisa, nao foi criada conta nem consumido
credito. A documentacao comercial nao e prova de cobertura integral.

Para o piloto: uma conta publica, limite de gasto explicito, sem transcricao,
sem download de midia no provedor e sem extras de metricas. Nenhum filtro de
data ou exclusao de fixados. Ler todas as paginas do dataset, nao apenas a
primeira resposta. O limite do dataset e diferente do limite de coleta do Actor.

Nao inferir que o provedor funciona sem sessoes internamente. A garantia
pretendida e nao solicitar dados de login do cliente. O nome do perfil alvo
sera enviado ao terceiro e isso deve ser informado.

## Decisao atual: primeiro local

O usuario decidiu manter Apify apenas como plano C. Aplicativo de uso pessoal
para ele e eventualmente seu irmao; login local passa a ser uma possibilidade.
Nenhum acesso a cookies ou login foi realizado nesta etapa.

### Plano A: sessao local e bibliotecas Python

Validar Instaloader com sessao autorizada e reutilizada. O projeto ja possui
importacao explicita de cookies e suporte a arquivo de sessao; faltam teste
autenticado real e um fluxo simples de conexao. Nao pedir senha em campos do
app nem gravar credenciais em configuracoes. Uma sessao expirada deve levar
a reconexao explicita, nunca a tentativas repetidas de login.

Comecar por um perfil conhecido, confirmar pelo menos duas paginas distintas
e depois o fim da origem. Login pode resolver uma restricao de acesso, mas nao
conserta consultas antigas nem garante cobertura. Preservar parciais e parar
em bloqueio/desafio. Nao recriar clientes para zerar o controle de requisicoes.

### Plano B: navegador local dedicado

Playwright Python, sem MCP ou agente de IA como dependencia do aplicativo.
Um perfil de navegador exclusivo do InstantDownloader, fora do repositorio,
permite que o usuario entre diretamente no Instagram e resolva 2FA manualmente.
Reutilizar esse perfil enquanto a sessao for valida. Nao usar o diretorio
padrao do Chrome pessoal nem compartilhar sessoes entre usuario e irmao.

O prototipo acompanha respostas estruturadas de listagem produzidas pela
navegacao normal da aba Reels e faz rolagem gradual. Links do DOM sao uma
alternativa parcial, nao evidencia de completude: a pagina pode virtualizar
itens. Deduplicar por shortcode, preservar resultados incrementalmente e
distinguir fim confirmado de timeout/nenhum progresso. Nao concluir que
acabou apenas porque a pagina parou de crescer por alguns segundos.

Transferir cookies para HTTP direto e uma otimizacao a testar, nao uma garantia
de reproduzir o navegador: tokens, headers e contexto podem diferir. Comecar
com janela visivel para diagnostico; nao prometer funcionamento sem janela.

### Protecao da sessao e aceite

- Estado autenticado e uma credencial, mesmo sem guardar a senha.
- Armazenar fora do Git, sem logs de cookies, traces ou corpos autenticados.
- Restringir acesso local e oferecer desconectar/remover a sessao dedicada.
- Nao apagar cookies do navegador pessoal ao desconectar o aplicativo.
- Validar Reels antigos, fixados, colaboracoes e Reels ausentes do feed.
- Sem autenticacao real ainda validada, estes planos sao propostas, nao recursos
  comprovados. Importar uma sessao exige escolha explicita do usuario.

Fontes adicionais consultadas:
- https://instaloader.github.io/troubleshooting.html
- https://playwright.dev/python/docs/auth
- https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context

## Plano C: piloto externo, somente se retomado

Se os caminhos locais nao atenderem e o usuario decidir retomar esta opcao,
validar o provedor antes de integrar ou prometer coleta integral.

1. Comparar IDs retornados para um perfil pequeno com inventario conhecido,
   incluindo Reels ausentes do feed e fixados.
2. Testar um perfil com varias paginas, conferir videos antigos e colaboracoes.
3. Medir custo por consulta, latencia e motivo de parada. Repetir para avaliar
   estabilidade sem requisicoes agressivas.
4. Reprovar como solucao integral se o provedor nao permitir distinguir fim
   da coleta de limite, erro ou interrupcao por orcamento.
5. So entao integrar um adaptador separado ao nucleo compartilhado CLI/Tkinter.

Se usarmos chave propria do produto, mante-la num backend, nunca embutida no
desktop ou GitHub. Isso acrescenta hospedagem, autenticacao do aplicativo,
cotas e operacao. Para piloto local, uma chave do operador em variavel de
ambiente evita criar esse backend cedo demais. Nunca procurar/usar chaves
existentes ou iniciar cobrancas automaticamente.

## Contrato e experiencia propostos (ainda nao implementados)

- Resultados progressivos: ID, URL canonica, titulo, miniatura opcional, data,
  origem e identificador da execucao.
- Estados distintos: coletando, fim da fonte, limite de itens, limite de custo,
  acesso indisponivel, cancelado e erro. Zero resultados em erro nao significa
  zero Reels na conta.
- Mostrar `N Reels encontrados` enquanto nao houver prova de esgotamento.
  Nao apresentar `N de N` usando apenas o total retornado pelo provedor.
- Persistir IDs e checkpoint por perfil/provedor. Cursor expirado exige
  recomecar com deduplicacao, sem perder itens ja encontrados.
- Identificar cursor repetido e pagina sem progresso para evitar loops.
- Modo `Todos acessiveis` sem corte silencioso em 10/1000. Limites de tempo,
  custo e requisicoes permanecem explicitos e geram resultado parcial.
- Cancelamento preserva itens. Descoberta nao inicia downloads automaticamente.
- Na UI, selecionar conta -> descobrir Reels -> revisar contagem/cobertura ->
  selecionar -> adicionar a fila existente.
- Download por yt-dlp continua local. Uma URL descoberta nao garante que seu
  download anonimo funcionara; relatar essa falha separadamente.

## Fontes primarias

- Instaloader, implementacao de `Profile.get_reels`:
  https://github.com/instaloader/instaloader/blob/master/instaloader/structures.py
- gallery-dl, extrator Instagram e `InstagramRestAPI.user_clips`:
  https://github.com/mikf/gallery-dl/blob/master/gallery_dl/extractor/instagram.py
- Apify, Actor mantido pela Apify:
  https://apify.com/apify/instagram-reel-scraper
- Esquema de entrada:
  https://apify.com/apify/instagram-reel-scraper/input-schema
- Colecao da API Instagram publicada pela Meta:
  https://www.postman.com/meta/instagram/collection/6yqw8pt/instagram-api

As referencias de codigo apontam para branches mutaveis; conclusoes descrevem
o material consultado na data acima, nao um contrato permanente da plataforma.
