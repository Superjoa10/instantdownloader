# CLI para automacoes e agentes

## Auditoria e escopo

Revisao de 2026-11-09:

- A CLI antiga nao usava a sessao dedicada salva pelo desktop, nao filtrava
  publicacoes por data e adicionava descobertas a fila antes de filtrar.
- Progresso de provedores podia contaminar stdout; erros gerais eram texto
  mesmo quando o chamador esperava JSON. A fila nao tinha resumo JSON da execucao.
- Foram adicionados filtros compartilhados com o desktop, manifestos revisaveis,
  comandos de sessao, JSON para URL/Excel/fila e checkpoints atomicos.
- Nao foi corrigida a causa remota das falhas do Feed. Feed continua usando
  Instaloader; Reels com --use-session usa o navegador dedicado headless.
- Nao ha coleta ilimitada, retomada por cursor nem garantia de acervo completo.
  O limite continua 1..1000 publicacoes examinadas por origem.

## Inicio

```bash
source .venv/bin/activate
instantdownloader --help
instantdownloader session status --json
```

No Windows, ative com `.venv\Scripts\Activate.ps1`. Os comandos seguintes sao
iguais; exemplos multilinha abaixo usam a continuacao do shell POSIX.

Para instalar: `python -m pip install -e ".[profiles]"`, seguido de
`python -m playwright install chromium`. A CLI nao precisa de Tkinter.
Nao ha instalacao automatica de dependencias durante os comandos de download.

O app e a CLI compartilham o diretorio de dados e a fila SQLite. Para isolar um
pedido, defina `INSTANTDOWNLOADER_HOME` antes de todos os comandos desse pedido.
Uma pasta isolada nao tem a sessao salva da pasta padrao; conecte-a separadamente.
Prefira download-list direto quando nao quiser misturar a solicitacao com a fila
global existente. queue run executa TODOS os pendentes dessa pasta de dados.

## Sessao dedicada

```bash
instantdownloader session status --json
instantdownloader session test --json
instantdownloader session connect
instantdownloader session delete --yes --json
```

- status e local e nao verifica a sessao na rede. state: absent, pending ou saved;
  verified_now=false. Um arquivo salvo pode estar expirado.
- test verifica na rede, sem abrir janela visivel. Sucesso: state=verified.
- connect e somente interativo: abre um navegador dedicado, o usuario faz login
  e pressiona Enter no terminal para verificar. Erros mantem a janela aberta e
  exigem nova confirmacao. Ctrl+C cancela. Nao aceita --json nem stdin nao TTY.
  Tambem e possivel conectar pela interface existente.
- delete exige --yes; remove somente os arquivos locais, nao revoga a sessao no
  Instagram. Cookies sao credenciais locais; nao sao incluidos nos resultados.
- test/connect/delete usam o lock do aplicativo. Feche o desktop antes desses
  comandos. status apenas inspeciona existencia dos arquivos.
- --use-session opta explicitamente pela sessao dedicada na descoberta/datas.
  Nunca importa cookies do Chrome pessoal silenciosamente. Nao combine com
  --session-file, --login ou --discovery-browser.
- Essa sessao NAO e automaticamente aplicada ao download. Para downloads que
  precisam de autenticacao, as opcoes existentes --cookies --browser chrome
  importam cookies do navegador indicado, com autorizacao explicita do usuario.

## Descobrir, Filtrar e Revisar

```bash
instantdownloader profile discover "@perfil" \
  --scope reels --limit 500 --use-session \
  --published-after 2025-01-01 --published-before 2025-08-01 \
  --manifest lista.json --json
```

O formato legado `instantdownloader profile "@perfil" ...` continua aceito;
`profile discover` e um alias. --output e alias de --manifest.
Scopes: posts (Feed), reels, all (Feed + Reels). all consulta Feed primeiro e
interrompe antes de Reels se o Feed falhar. --limit nao e uma contagem garantida
de videos e nao corta pelo primeiro item fora do periodo: publicacoes fixadas
ou fora de ordem nao podem encerrar a busca antecipadamente.

Datas sao AAAA-MM-DD, limites inclusivos e dias em UTC. Publicacao no Instagram
nao e a data em que o arquivo original foi gravado. A mesma funcao de selecao e
usada pelo Tkinter. Filtro de data consulta datas ausentes automaticamente com
--use-session, uma publicacao por vez; --fetch-dates faz isso sem aplicar filtro.
Itens cuja data nao foi confirmada ficam fora de um filtro de data. O processo
nao presume datas pelo codigo do Reel nem usa timestamps de comentarios.

O manifesto e JSON versionado, sem configuracoes de cookies:

- schema_version=1;
- complete: descoberta limitada e, se solicitada, resolucao de todas as datas
  terminaram sem erro;
- discovery_complete: somente a etapa de descoberta;
- all_collected: fim de todas as origens solicitado, sem erro nem datas faltando
  quando requeridas; ainda limitado ao conteudo acessivel pela sessao;
- counts e discovered_count: observados ANTES do filtro; selected_count e items:
  o subconjunto revisavel DEPOIS do filtro;
- date_missing, dates_complete, date_message e filters: estado da resolucao e
  limites usados; filtros excluem datas desconhecidas;
- inspected, pages e exhausted: diagnostico por origem;
- error_code, failed_source e message: resultado e causa;
- enqueued: tarefas efetivamente adicionadas ou reativadas, nao downloads.

Manifestos sao atualizados atomicamente durante a descoberta e apos datas
resolvidas. Se houver interrupcao, podem conter complete=false e
error_code=in_progress; nao sao autorizacao para baixar silenciosamente uma parte.
Sem --manifest, nao ha checkpoint JSON em disco (o cache local continua existindo).

--enqueue enfileira SOMENTE os itens filtrados e apenas depois da descoberta e
das datas. Se nao chegou ao fim ou houve falha, nao enfileira por padrao; retorna
enqueue_refused=true. --allow-partial e uma escolha explicita para aceitar esse
subconjunto, inclusive os efeitos de datas ausentes. Nao o acrescente
automaticamente apenas para obter codigo de saida zero.

## Baixar o Manifesto

Para selecionar novamente uma lista pronta, sem repetir a descoberta:

```bash
instantdownloader filter-list lista.json \
  --published-before 2025-08-01 --output filtrada.json --json
```

Com datas ja preenchidas esse comando e offline. Se faltarem datas, use
--use-session para consulta-las; sem isso devolve subconjunto e erro explicito.
--fetch-dates tambem funciona sem filtro. O arquivo de entrada e preservado;
--output precisa ser outro caminho. Filtrar nao transforma uma descoberta
incompleta em conta completa e nao recupera itens excluidos de um filtro anterior.
Ao receber manifesto marcado como bloqueado/sem sessao, nao faz consultas de
datas adicionais automaticamente; usa apenas os metadados que ja existem.

```bash
instantdownloader download-list lista.json \
  --output-dir downloads --quality 1080p --compatible-mp4 \
  --report reports/lote.csv --json-report reports/lote.json --json
```

Por padrao recusa manifesto incompleto OU limitado (all_collected=false).
Depois de revisar e aceitar um subconjunto, acrescente --allow-partial.
Tambem existe --enqueue para apenas adicionar os itens a fila sem baixar.
O leitor valida todo o manifesto antes de agir, aceita apenas URLs HTTP/HTTPS
sem credenciais e indices de carrossel positivos, deduplica URL/indice e ignora
configuracoes extras do arquivo. Nao usa valores do manifesto como comandos.
Os report paths nao podem sobrescrever o manifesto de entrada.

Um manifesto da versao antiga sem schema_version deve ser regenerado/revisado;
nao e convertido automaticamente em uma lista supostamente completa.

## URL e Excel

```bash
instantdownloader analyze "URL" --json
instantdownloader download "URL" --output-dir downloads --compatible-mp4 \
  --report reports/url.csv --json-report reports/url.json --json
instantdownloader batch links.xlsx --sheet Videos \
  --output-dir downloads --compatible-mp4 \
  --report reports/excel.csv --json-report reports/excel.json --json
```

Excel conserva as colunas url, filename, folder e quality. --playlist-index no
download seleciona um video de carrossel; o manifesto preserva esse indice.
Downloads mantem concorrencia conservadora (1 por padrao, maximo 2); com archive,
o lote e sequencial. Parada por rate limit preserva pendentes no resultado, sem
proseguir indefinidamente. MP4 compativel mantem so a versao validada apos sucesso.

Resultados JSON de download incluem schema_version, complete, counts por status,
report (CSV absoluto) e results com status, caminhos e erros por item.
--json-report salva esse resultado atomicamente; nos lotes e atualizado durante
a execucao. CSV e JSON precisam de caminhos distintos. Um lote vazio e um no-op
bem sucedido com counts={} e results=[].

## Fila Persistente

```bash
instantdownloader queue add "URL" --output-dir downloads --compatible-mp4 --json
instantdownloader queue list --json
instantdownloader queue run --report-dir reports --json-report reports/fila.json --json
instantdownloader queue retry 3 5 --json
instantdownloader queue retry --failed --json
```

queue list preserva o formato legado: ARRAY JSON de tarefas, sem config ou cookies.
queue run informa counts da execucao, processed_ids, pending e report; falhas de
execucoes antigas nao tornam a nova execucao falsa. --failed repete apenas error
e cancelled, nao success nem rate_limited. Para repetir um rate_limited depois
de aguardar, o usuario precisa selecionar explicitamente seu ID.
O desktop e queue run nao executam simultaneamente. A fila nao se inicia
automaticamente ao descobrir ou ao enfileirar um manifesto.

## Contrato de Processo

Use --json em cada comando de automacao. Flags vem depois do comando, por
exemplo `queue list --json`, nao `--json queue list`.

- stdout: um unico documento JSON, inclusive em erros de validacao. Profile
  sempre produz JSON, mesmo no formato legado sem --json.
- stderr: progresso/diagnosticos; nunca interpretar como resultado estruturado.
- Objetos novos usam schema_version=1. analyze preserva o objeto legado de
  metadados (sem raw); queue list preserva o array legado.
- Codigos com --json: 0=operacao solicitada concluida; 2=entrada invalida ou
  falha geral; 3=lote/consulta incompleta ou enfileiramento parcial recusado;
  4=sessao necessaria/indisponivel; 5=limite ou pausa preventiva; 130=Ctrl+C.
- Comandos legados sem --json preservam 2 como falha geral e 130 para Ctrl+C.
  Um erro especifico pode ter codigo 4/5 antes da classificacao como parcial.
- Codigo 0 em descoberta LIMITADA nao significa conta inteira. Inspecione
  all_collected. Codigo 0 em --enqueue nao significa videos baixados.
- Erros em --json incluem complete=false, error_code e message. Campos
  especificos do resultado podem nao existir em erros de parsing/preflight.
- --help continua texto e usa o comportamento normal do argparse.

As automacoes nao respondem perguntas, nao abrem login escondido e nao tentam
contornar bloqueios. session connect e a unica etapa intencionalmente interativa.
Downloads, manifests e relatorios sao efeitos locais; nao ha upload/publicacao.

## Skill Portatil

`skills/instantdownloader/SKILL.md` e a instrucao compartilhada para agentes.
Ela esta no repositorio, nao foi instalada ou ativada globalmente no Codex/Claude.
Nao altera configuracao global de nenhum agente. Instalacao/descoberta em cada
cliente e validacao no Windows continuam trabalhos separados.
