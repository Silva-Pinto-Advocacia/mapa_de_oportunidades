# `/sentinela/wa` — o nosso Atende Direito: onde ele está hoje

Análise de `Silva-Pinto-Advocacia/silvapinto-comercial`, commit `4fcc2f7`.
O sistema vive quase todo em `app/routes/sentinela_ops.py` (3.805 linhas).

Tudo abaixo foi conferido no código. Onde digo "não roda" ou "está bloqueado",
apontei a linha.

---

## 1. Antes de tudo: são três sistemas, não um

Isso confunde qualquer um que abra o repositório, então vale fixar:

| Sistema | Onde | O que faz |
|---|---|---|
| **`/atendimento/*`** | `atendimento_webhook.py`, `atendimento_inbox.py` | Modo sombra: **só recebe**. É o webhook que a Meta de fato alcança. Tem SSE. |
| **`/sentinela/wa/*`** | `sentinela_ops.py` | **O nosso Atende Direito.** Envia, atende, tem a Bia. É deste que trata esta análise. |
| **Jusmart** | outro repositório | A versão multi-tenant, extraída daqui. Hoje ainda é só espelho (não envia). |

O `/atendimento` alimenta as tabelas `ad_*`; o `/sentinela/wa` lê e escreve
nas mesmas tabelas. Os dois convivem, e essa convivência é onde estão os
achados da §4.

---

## 2. O que já está de pé — e é muito

Não é protótipo. É um produto de atendimento com quase tudo que se cobra por:

**Canal**
- Envio de texto, mídia por URL e **upload de arquivo** (sobe pro `/media` da
  Meta em multipart montado à mão, devolve `media_id`, reenvia por id) — até 25 MB.
- Recepção de todos os tipos, com download de mídia sob demanda (`/midia`).
- **Transcrição de áudio** por Whisper (`/transcrever`), guardada em `sp_transcricao`.
- Templates da WABA: listar e criar (`/templates`, `/templates/criar`).

**A Bia**
- `sentinela_bia.py` roda o prompt real do Atende Direito (`bia_recepcao_v4.md`,
  verbatim) — Claude como cérebro primário, OpenAI `gpt-4.1` como reserva
  opcional com os mesmos parâmetros do original (temp 0.22, freq 1.34, pres 1.21).
- Dois modos: **sugestão** (a atendente vê e decide) e **automático**
  (`SENTINELA_AUTO=true`), com laço que responde sozinho, quebrado em balões e
  com pausa de 1,4 s entre eles.
- "Assumir" pausa a Bia naquela conversa.

**Operação de equipe** — é aqui que ele passa longe do Jusmart
- Atendentes por conversa, atribuição em lote, presença/online, "quem sou".
- Setor (Comercial/Jurídico), estágio, etiquetas, notas, renomear contato.
- Follow-ups (criar, cumprir, adiar), retomadas, fila-fora (silenciar até data),
  arquivar, excluir.
- Atalhos (respostas rápidas) com contador de uso.
- **SLA medido em segundos úteis**, com faixas, tempo médio de resposta do dia
  e lembretes de cadência.

**Além do atendimento**
- Campanhas: segmentos, gerar, submeter, disparar; importação de mailing.
- Nutrição de leads de longo prazo: trilhas, etapas, opt-out, log.
- CNJ/DataJud: consultar processo, traduzir movimentos do juridiquês, enviar.
- ZapSign (assinatura) e link do portal do cliente.
- Detecção de concurso por IA, banco de decisões e prova social para enviar.

A lista de coisas que **faltavam no Jusmart** — atendente na conversa, mídia,
templates, envio — **está toda pronta aqui**.

---

## 3. O que separa isto de estar em produção: uma linha

As três rotas de envio têm o mesmo portão:

```python
if para not in _allowlist():
    raise HTTPException(403, "destino fora da allowlist de teste")
```

E o laço automático só percorre `_allowlist()`:

```python
for n in sorted(_allowlist()):
```

`SENTINELA_ALLOWLIST` é uma variável de ambiente com os números de teste. **O
sistema inteiro só fala com quem está nessa lista.** Tirar o portão é o gesto
que vira a chave — e é por isso que os achados abaixo importam agora, não depois.

---

## 4. Os achados que eu levaria a sério

Em ordem do que eu resolveria primeiro.

### 4.1 A Bia volta a falar por cima da atendente depois de todo deploy

`PAUSADOS` é um `set()` em memória (linha 35). "Assumir a conversa" grava ali,
e só ali.

Todo restart do serviço — deploy, hibernação, queda — **esvazia a lista**. Com
`SENTINELA_AUTO=true`, a Bia volta a responder sozinha conversas que alguém já
tinha assumido, sem aviso. A atendente descobre lendo o histórico.

Correção: uma tabela `sp_pausado`, no mesmo padrão das outras 25.

### 4.2 O webhook do Sentinela é inalcançável pela Meta — e o echo se perde com ele

`app/middleware.py` tem `PUBLIC_PATHS = {"/atendimento", "/login", "/static",
..., "/api/", ...}`. **`/sentinela/wa/webhook` não está lá** — então a Meta,
que chega sem sessão, leva `302` para `/login`.

Quem recebe de verdade é `/atendimento/webhook`, que está público e confere HMAC.
Na prática funciona, porque os dois escrevem nas mesmas tabelas `ad_*`.

Mas o `wh_receber` do Sentinela trata uma coisa que o outro não trata:

```python
for m in (v.get("message_echoes") or []):
```

**Esse trecho nunca roda.** É justamente ele que capturaria o *texto* das
mensagens enviadas por fora do sistema. Sem ele, do que a equipe manda pelo
Atende Direito só chega o status — a conversa fica com metade do diálogo, que
é exatamente o problema que a ponte tenta remediar por outro caminho.

### 4.3 O webhook do Sentinela não confere assinatura

`WHATSAPP_APP_SECRET` aparece no `/status` como `app_secret_ok`, mas **não é
usado para validar nada** em `sentinela_ops.py`. O `wh_receber` aceita qualquer
corpo.

Hoje isso não expõe nada, porque a rota é inalcançável (§4.2). Mas o passo
natural — pôr `/sentinela/wa/webhook` em `PUBLIC_PATHS` para receber os echoes
— transforma o achado 4.2 no achado 4.3, e aí qualquer um que descubra o
endereço escreve na caixa de entrada.

Os dois se resolvem juntos, e o código de HMAC já existe pronto em
`atendimento_webhook.py:159-166`. É copiar.

### 4.4 A mídia some depois de ~30 dias

`/midia` baixa da Meta na hora e guarda em `_MIDIA_CACHE` — dicionário em
memória, 60 itens, 30 minutos, `clear()` quando estoura. **Nada é persistido.**

A Meta guarda a mídia por cerca de 30 dias. Passado isso, o áudio que o cliente
mandou e o documento que ele enviou não existem mais em lugar nenhum — e num
atendimento por WhatsApp, é comum o áudio *ser* a prova do que foi combinado.

O destino já está configurado e ocioso: `render.yaml` tem `R2_BUCKET`,
`R2_ACCESS_KEY_ID`, `R2_ENDPOINT`. Falta gravar lá.

### 4.5 O laço automático depende de o processo estar acordado — e ele hiberna

Duas coisas se somam:

- `_loop_auto` é uma `threading.Thread` do processo, iniciada no import.
- `render.yaml` diz `plan: free`, e serviço free no Render hiberna com
  inatividade.

Consequência: **fora do horário de movimento, a Bia automática simplesmente não
roda** — a thread dorme com o processo. E o primeiro webhook depois da
hibernação pega cold start, que a Meta lê como falha e reentrega.

Vale conferir se o serviço no Render está mesmo no plano free (o `render.yaml`
pode estar desatualizado em relação ao painel). Se estiver, é o item de melhor
relação custo/benefício da lista inteira.

### 4.6 Um segundo worker duplicaria toda resposta automática

`startCommand: uvicorn app.main:app` — sem `--workers`, então hoje é um
processo só e o desenho funciona. Mas é uma dependência não escrita: no dia em
que alguém acrescentar `--workers 2` para aguentar mais gente, passam a existir
dois `_loop_auto`, e **cada cliente recebe a resposta da Bia duas vezes**.

O `PAUSADOS` (§4.1) quebra do mesmo jeito e pelo mesmo motivo.

Não é para resolver agora — é para estar escrito antes de alguém mexer no
`startCommand` achando que é só performance.

### 4.7 A ponte é engenhosa e é uma muleta

`/api/sentinela/ponte` recebe conversas raspadas da aba aberta do Atende
Direito (CORS restrito a `app.atendedireito.com.br`, protegida por
`PONTE_CHAVE`, lotes de 80). Resolve o problema real de trazer o que a equipe
manda por lá.

Mas depende de alguém manter uma aba aberta num navegador. Resolver o §4.2 —
receber os echoes direto da Meta — a aposenta.

### 4.8 O schema não tem versão

`_ensure_tabelas()` roda no import: ~25 `CREATE TABLE IF NOT EXISTS` mais
`ALTER TABLE` dentro de `try/except`. Funciona e é pragmático.

O custo aparece depois: não há como saber em que estado o banco de produção
está sem abrir e olhar. Com 25 tabelas já, e a migração para o Jusmart no
horizonte, isso vira dívida rápido.

---

## 4-B. O que só aparece quando o portão sai

Os achados acima são de correção: existem hoje e valem mesmo em teste. Os três
abaixo estão dormentes porque a allowlist segura o volume — e acordam junto com
a virada.

### 4.9 Sete rotas `async` fazem I/O bloqueante — e o servidor é um processo só

`/enviar`, `/enviar-midia`, `/enviar-upload`, `/decisao/enviar`,
`/andamento/enviar`, `/nutricao/enviar` e `/transcrever` são todas
`async def`, e todas chamam `urllib.request.urlopen` direto. Não há um
`run_in_executor` nem um `to_thread` no arquivo inteiro.

Em FastAPI, rota `async` roda **no event loop**. Chamada bloqueante ali trava
o processo — e com `uvicorn` sem `--workers`, o processo é o sistema.

Os tempos-limite dizem o tamanho do problema:

| Rota | Timeout | O que trava junto |
|---|---|---|
| `/enviar` | 30 s | tudo |
| `/enviar-upload` (até 25 MB) | 120 s | tudo |
| `/transcrever` | ~60 s | tudo |

Uma atendente subindo um PDF de 20 MB pode congelar o painel inteiro por dois
minutos: ninguém carrega página, nenhum outro envio sai, e **o webhook da Meta
não é atendido** — que ela lê como falha e responde reentregando.

Com 3 números de teste isso quase nunca acontece. Com o escritório inteiro
atendendo, acontece todo dia.

Correção: trocar `async def` por `def` nessas rotas. O FastAPI passa a rodá-las
num threadpool e o event loop fica livre. É uma palavra por rota.

### 4.10 Nenhum envio tem retry — e não há fila

Busca por `retry`, `backoff`, `retentar` no arquivo: **zero ocorrências.**

Hoje, se a chamada ao Graph falhar, a rota devolve `502` e a mensagem
simplesmente não foi. Em modo manual isso é tolerável: a atendente vê o erro na
tela e clica de novo.

Mas o laço automático engole a falha:

```python
for balao in _split_baloes(txt):
    try:
        _enviar_wa(n, balao)
    except Exception:
        break
```

Um `break` silencioso. Se o segundo balão de três falhar, **o cliente recebe
uma resposta pela metade e ninguém fica sabendo** — nem a atendente, nem o log.
Numa oscilação de rede da Meta, isso vira conversa truncada em série.

Enquanto for manual, dá para viver sem fila. Automático em produção, não: o
mínimo é registrar a falha em algum lugar que alguém olhe.

### 4.11 A allowlist não é só a trava — é a lista de trabalho da Bia

Este é o mais fácil de não enxergar:

```python
def _loop_auto():
    ...
    for n in sorted(_allowlist()):
```

O laço automático **percorre a allowlist**. Ela não é uma trava colocada por
cima de um sistema que funcionaria sem ela — é a fonte de quais conversas a Bia
acompanha.

Consequência prática: apagar `SENTINELA_ALLOWLIST` não libera a Bia para todos.
**Desliga a Bia**, e o modo automático deixa de existir em silêncio.

Para valer em produção, o laço tem de passar a percorrer as conversas ativas —
e aí o desenho encosta no limite: a cada 8 segundos, para cada conversa, uma
consulta de histórico e possivelmente uma chamada de IA. Com 3 números é
barato; com 300 conversas abertas é um laço que não fecha o ciclo antes do
próximo começar.

Isto não é um ajuste — é a única parte que precisa ser repensada, e é o que eu
faria por último, com o modo manual já rodando em produção e provado.

---

## 5. O que eu faria, em ordem

| # | O quê | Por quê agora |
|---|---|---|
| 1 | **`PAUSADOS` numa tabela** | A Bia falar por cima de uma atendente é dano com cliente na frente. É a correção mais barata da lista. |
| 2 | **Confirmar o plano no Render** | Se estiver free, a Bia automática não roda fora de pico e ninguém sabe disso. |
| 3 | **Webhook do Sentinela: público + HMAC, juntos** | Destrava os echoes (conversa inteira) e aposenta a ponte. Os dois na mesma mudança, nunca só o primeiro. |
| 4 | **Persistir mídia no R2** | O bucket já existe. Cada dia que passa são áudios de 30 dias atrás sumindo. |
| 5 | **Anotar a dependência de worker único** | Um comentário no `render.yaml` e no `_loop_auto`. Custa dois minutos e evita um incidente. |
| 6 | **Tirar o `async` das 7 rotas de I/O** (§4.9) | Uma palavra por rota. Sem isso, o primeiro upload grande congela o painel do escritório inteiro. |
| 7 | **Registrar falha de envio** (§4.10) | O `break` silencioso do laço automático entrega resposta pela metade sem ninguém saber. |
| 8 | **Ampliar a allowlist por etapas, em modo manual** | Com 1–7 feitos, dá para crescer com clientes reais sem susto. |
| 9 | **Repensar o laço automático** (§4.11) | Só depois do manual provado em produção. É reprojeto, não ajuste. |

O item 3 é o que muda o produto: hoje o sistema enxerga metade da conversa.
Os itens 6 e 7 são baratos e são o que separa "funciona no teste" de "aguenta
o escritório".

### O veredito, direto

**Com as três correções da §4.1–4.3, dá para rodar em modo manual, ampliando a
allowlist aos poucos — desde que 6 e 7 entrem junto.** São mudanças pequenas, e
sem elas o primeiro upload grande de um dia movimentado derruba o painel para
todo mundo.

**Não dá para ligar o modo automático para a base inteira**, e não por falta de
cuidado: `SENTINELA_AUTO` com a allowlist aberta simplesmente não faz o que
parece (§4.11). Isso é a fase seguinte, com o manual já provado.

---

## 6. Ressalvas

- Analisei o `main` do `silvapinto-comercial`. Se o trabalho recente está em
  outra branch, parte dos achados pode já estar resolvida — me diga qual e eu
  reviso.
- Não consegui verificar o ambiente de produção (variáveis do Render,
  `SENTINELA_AUTO`, tamanho real da allowlist). As conclusões sobre plano free
  e worker único vêm do `render.yaml` versionado.
- Não avaliei a qualidade das respostas da Bia — isso se mede lendo conversa,
  não código.
