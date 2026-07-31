# `/sentinela/wa` — o nosso Atende Direito: onde ele está hoje

---

## 0. Estado em `ccbf16e` (revisão de 31/07)

Três commits depois da análise original. **Resolvido:**

| Achado | Como ficou |
|---|---|
| §4.1 `PAUSADOS` em memória | `sp_pausado` + `_pausados_carregar()` no boot + `_esta_pausado()` lendo do banco. Resolvido, e de um jeito que ainda sobrevive a multi-worker. |
| §4.3 webhook sem HMAC | `_assinatura_valida()` com `compare_digest`. **Ver ressalva abaixo.** |
| §4.4 mídia não persistida | R2 com SigV4 escrito à mão, `sp_midia`, gravação em thread de fundo, `/midia` busca no R2 antes da Meta, e `/midia-status?testar=1` para conferir. |
| §4.6 dois workers duplicariam | `_sou_o_dono_do_loop()` com lease de 90 s em `sp_config`. Resolvido. |
| — (não estava na lista) | **Opt-out.** `sp_optout`, reconhecimento de "pare/sair/não quero mais", e `_post_graph` levantando `OptOutError` antes de enviar. Não levantei isso e devia ter: é o que evita denúncia na Meta, e denúncia derruba o número. |
| — (não estava na lista) | **Trava dupla no automático**: `SENTINELA_AUTO` **e** `SENTINELA_AUTO_CONFIRMO`. Impede ligar sem querer. |

**Duas ressalvas no que foi feito:**

- `_assinatura_valida()` devolve `True` quando não há segredo configurado. É
  *fail-open*: sem `WHATSAPP_APP_SECRET` no Render, a conferência não acontece
  e ninguém percebe. Como a rota vai virar pública, a variável tem de estar lá
  **antes**.
- O R2 só guarda mídia que **alguém abriu no inbox** — a gravação pendura na
  rota `/midia`. Áudio que ninguém clicou não é baixado, não vai para o R2, e
  some da Meta em ~30 dias do mesmo jeito.

### Continua em aberto — e três disto bloqueiam o teste com leads reais

| # | O quê | Efeito amanhã |
|---|---|---|
| §4.11 | **O laço ainda percorre `_allowlist()`** | **Bloqueador.** Ver abaixo. |
| Regra 5 | Saída sem autor (`_enviar_wa(para, texto)`) | A Bia volta a responder por cima do humano na mensagem seguinte do cliente. |
| §4.2 | `/sentinela/wa/webhook` continua fora de `PUBLIC_PATHS` | **O opt-out automático nunca roda.** Ver abaixo. |
| §4.9 | 6 rotas de envio ainda `async` com `urllib` bloqueante | Um upload grande congela o painel inteiro. |
| §4.10 | Zero retry; `break` silencioso intacto (linha 2498) | Resposta pela metade, sem registro. |
| §4.5 | `plan: free`, `uvicorn` sem `--workers` | O laço hiberna com o serviço. |

**O bloqueador, em uma frase:** nada da regra de contato novo existe — busca
por *virada*, *corte*, *elegível* no arquivo devolve zero. O laço continua em
`for n in sorted(_allowlist())`. Então, amanhã: **esvaziar a allowlist não
libera a Bia para os leads novos, desliga a Bia**; e mantê-la faz a Bia
responder só os números de teste. Nos dois casos o teste com lead real não
acontece. E as sete rotas de envio ainda recusam quem está fora da lista, então
nem o envio manual passa.

**O segundo, que é traiçoeiro:** o opt-out foi implementado dentro do
`wh_receber` de `/sentinela/wa/webhook` — a rota que a Meta **não alcança**
(§4.2). Quem recebe de verdade é `/atendimento/webhook`, que não tem essa
lógica. Ou seja: hoje o "pare de mandar" do cliente só é registrado se um
atendente clicar no botão. Automático, não funciona. Isso e o §4.2 se resolvem
na mesma mudança.

### O que dá para fazer amanhã

**Opção A — teste em modo sugestão (recomendada).** A Bia escreve, o humano lê
e envia. Exercita o prompt, a Bia, o inbox e a operação com lead real, e nada
sai sem alguém clicar. Precisa de duas coisas só:

1. Tirar (ou ampliar) o portão da allowlist nas rotas de envio.
2. `WHATSAPP_APP_SECRET` no Render, por causa do fail-open.

Não depende do laço, nem do autor na saída, nem do webhook. Dá para amanhã.

**Opção B — automático para contatos novos.** Precisa do laço reescrito
(§4.11), do autor na saída (Regra 5) e do webhook público com HMAC (§4.2/§4.3),
porque sem este último o opt-out automático não existe — e no automático é ele
que segura a denúncia na Meta. Não é para amanhã.

---

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

## 4-C. O plano de virada: Bia só para contatos novos

O desenho combinado é por **coorte**, não por lista:

1. O número oficial do escritório vai para a API oficial; a allowlist sai.
2. A Bia atende **só quem chamar a partir da data de virada**.
3. Se o contato for de cliente antigo, a Bia não entra.
4. Se entrar mesmo assim, o contato vai para o Jurídico e a Bia desliga ali.
5. A Bia para assim que um atendente humano interagir.

Isto é melhor que abrir a allowlist: troca uma lista que alguém mantém à mão
por uma regra que o sistema aplica sozinho. Abaixo, o que o código sustenta e
o que não sustenta.

### Regra 2 (cliente antigo não recebe Bia) — já existe, pronta

`_cliente_cruzado(numero)` cruza o telefone com a tabela `cliente` e traz
contratos, processos e último andamento. É exatamente o teste necessário, já
escrito e com cache de 60 s.

**Limite conhecido:** casa pelos **últimos 8 dígitos** do telefone cadastrado.
Cliente antigo que escrever de outro aparelho — número novo, celular do cônjuge
— passa como contato novo. Não há como fechar isso, e é justamente por isso que
a regra 4 existe como rede.

### Regra 4 (Jurídico desliga a Bia) — fácil, e é recuperação, não prevenção

`sp_setor` e a rota `/setor` já existem. Basta a elegibilidade do laço excluir
`setor='Jurídico'`.

Vale nomear o que ela é: quando alguém percebe que a Bia falou com um cliente
antigo, **ela já falou pelo menos uma vez**. A regra 4 conserta; quem previne é
a regra 3. Com o limite dos 8 dígitos acima, contar quantas vezes a regra 4 for
acionada é o melhor termômetro de quão furada está a regra 3.

### Regra 1 (contato novo a partir da data) — funciona, mas não pela coluna óbvia

O caminho intuitivo é `ad_contato.primeiro_contato_em >= data_da_virada`.
**Não use essa coluna.** Ela não quer dizer "quando a pessoa nos escreveu pela
primeira vez" — quer dizer "quando a linha foi criada". Prova no próprio
código, em `_registrar_saida`:

```python
execute("INSERT INTO ad_contato (wa_id, primeiro_contato_em, ultimo_contato_em, origem)
         VALUES (?,?,?,'sentinela')", (para, _agora(), _agora()))
```

Um contato criado por uma mensagem que **nós** enviamos ganha
`primeiro_contato_em = agora`. O mesmo vale para o que entrou pela ponte e pela
importação do Atende Direito, em bloco, muito depois das conversas reais.

O critério seguro é sobre as mensagens, não sobre o contato:

> É novo quem **não tem nenhuma mensagem recebida anterior à data de virada**.

Isso é robusto ao histórico importado, porque a importação preserva o carimbo
real de cada mensagem.

**Uma corrida a vigiar:** se um cliente antigo escrever antes de a ponte ter
trazido o histórico dele, ele parece novo por alguns minutos. Duas defesas — a
regra 3 (cadastro) pega a maioria, e desligar a ponte só **depois** de uma
importação completa fecha o resto.

### Regra 5 (atendente humano desliga a Bia) — esta não funciona hoje

É a que mais importa e é a única que o código não sustenta.

`_registrar_saida` grava toda mensagem de saída do mesmo jeito:

```python
"INSERT OR IGNORE INTO ad_mensagem (conversa_id, wa_message_id, direcao, tipo,
 conteudo, recebido_em, status) VALUES (?,?,'saida','text',?,?,'enviada')"
```

Não há autor. **O sistema não distingue uma mensagem que a Bia mandou de uma
que a Maria mandou** — `_enviar_wa(para, texto)` nem recebe quem está enviando.

O que existe hoje é mais fraco e é outra coisa:

```python
if not hist or hist[-1]["quem"] != "lead":
    continue
```

A Bia não fala enquanto a vez é nossa. Se um atendente responder, ela se cala —
**até o cliente escrever de novo.** Aí ela responde por cima de quem tinha
assumido. Não é "a Bia parou": é "a Bia esperou a vez".

O mecanismo pensado para isso é o `PAUSADOS`, e ele tem dois defeitos: exige
que alguém clique "Assumir" (a regra combinada é automática, sem clique) e vive
em memória (§4.1), então some no deploy seguinte.

**A correção é pequena e precisa:**

1. Uma coluna `enviado_por` em `ad_mensagem`.
2. `_enviar_wa` passa a receber o autor. As rotas já sabem quem é — 
   `_usuario_logado(request)` existe e funciona; o laço automático passa `'bia'`.
3. Elegibilidade: conversa que tenha **qualquer saída com `enviado_por <> 'bia'`**
   está fora da Bia, para sempre.

Essa mesma coluna resolve o §4.1 de brinde (a pausa vira dado, não memória) e é
o que faz a métrica por atendente virar verdade — hoje ela credita à equipe o
que a Bia respondeu.

### O que o plano não cobre, e eu levantaria

**A Bia passa a ser a primeira impressão do escritório.** Hoje ela fala com 3
números de teste; depois da virada, fala com todo mundo que chega no número
oficial, antes de qualquer humano.

Some isso ao §4.10 — sem retry, e com `break` silencioso no meio dos balões — e
o cenário é concreto: uma oscilação da Meta faz o **primeiro contato de um lead
novo com o escritório** ser meia frase, sem ninguém saber. Em teste é um
aborrecimento; na virada é a porta de entrada.

Antes de ligar, eu faria o mínimo do §4.10: registrar a falha em algum lugar
que alguém olhe.

**E o laço vira o gargalo.** Ele é uma thread só, sequencial, com `sleep(1.4)`
entre balões e uma chamada de IA por conversa. Dez contatos novos ao mesmo
tempo viram fila: o décimo espera o nono. Além disso, hoje ele relê o histórico
de cada número a cada 8 segundos — com a allowlist são 3; com o fluxo real do
escritório, é consulta demais no Turso para nada.

Antes de escalar: o laço deve olhar só conversas com mensagem nova desde a
última passada, em vez de reler todas.

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
