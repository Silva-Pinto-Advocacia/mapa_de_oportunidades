# O sistema de WhatsApp da Jusmart: onde ele está e o que falta para ser o nosso Atende Direito

Análise do código em `Silva-Pinto-Advocacia/Jusmart`, commit `14d4d14`
(PR #56, *Modo espectador*). Tudo abaixo foi conferido no código — quando
digo "não existe", quer dizer que a busca no repositório inteiro não achou.

---

## 1. Em uma frase

O que existe hoje é um **espelho**: a plataforma escuta a linha do WhatsApp e
grava tudo, mas **não envia uma única mensagem**. O atendimento continua
acontecendo no Atende Direito. Para virar a nossa versão dele, a peça que falta
não é uma tela — é a metade de saída do canal, e tudo que ela arrasta junto.

---

## 2. O que já está pronto, e está bem feito

A recepção é a parte difícil de acertar e ela está acertada.

**A porta de entrada** — `app/whatsapp/webhook.py`

| Garantia | Como |
|---|---|
| Nada se perde | O evento cru vai para `webhooks_whatsapp` **antes** de qualquer processamento. A Cloud API não reentrega sob demanda. |
| Ninguém escreve na caixa alheia | Assinatura `x-hub-signature-256` conferida por HMAC-SHA256. Em produção, evento sem segredo configurado é recusado. |
| Reentrega não duplica | `mensagens.id_no_canal` é `UNIQUE`. A segunda chegada não mexe em contador nenhum. |
| A Meta nunca entra em tempestade de reentrega | A resposta é sempre `200`. Erro nosso vira anotação, não status de erro. |
| A mensagem acha o escritório dela | `canais_whatsapp` traduz `phone_number_id` → tenant. O número é `UNIQUE` na plataforma inteira — o banco recusa antes de a aplicação ter chance de errar. |

**A tradução** — `app/whatsapp/traducao.py` cobre 14 tipos de mensagem (texto,
áudio, imagem, vídeo, documento, figurinha, localização, contato, botão,
interativo, reação, pedido do catálogo, não suportado, desconhecido) e nunca
devolve vazio.

**O modelo de dados** — `migrations/tenant/002_inbox.sql`: `contatos`,
`conversas`, `mensagens`, com chaves estrangeiras de verdade e a coluna `canal`
já prevendo Instagram e chat do site. O nome do fornecedor saiu do schema (as
tabelas do sistema de origem se chamavam `ad_*`, de Atende Direito) — decisão
certa, e é o que permite trocar de provedor sem migration.

**O Sentinela** — `app/comercial/sentinela.py`. Lê as conversas que entram
sozinhas e classifica em cinco faixas de urgência, ordenadas por quanto se
perde ao não agir hoje. É a peça mais madura do módulo, e a única que já é
melhor que a do sistema de origem: lá ele puxava relatório da API do Atende
Direito, aqui é retrato do escritório inteiro, contínuo.

**O diagnóstico** — `/admin/whatsapp` com quatro contadores, cada um apontando
qual dos cinco passos de configuração falhou. `docs/whatsapp.md` documenta o
roteiro. Isso resolve o "conectei e não aparece nada", que é onde essas
integrações costumam morrer.

---

## 3. A distância até "nossa versão do Atende Direito"

Seis camadas. A primeira é a que trava todas as outras.

### 3.1 Envio — o buraco central

**Não há uma única chamada a `graph.facebook.com` no repositório.** Zero envio.

O inbox é honesto sobre isso, e a honestidade está no código:

> `app/inbox/rotas.py` — *"Nada aqui finge enviar. Um botão 'enviar' que não
> envia é a pior mentira que um inbox pode contar."*

`POST /inbox/{id}/responder` grava a resposta no histórico e manda copiar para
a área de transferência. Quem atende escreve no sistema e cola no celular.

O que falta:

- Cliente HTTP da Cloud API (`POST /{phone-number-id}/messages`).
- **Token de envio por escritório.** A infraestrutura para isso **já existe e
  está pronta**: `tenant_secrets` guarda credenciais cifradas por tenant
  (`control/secrets.py`, `MASTER_KEY`). Só não há nenhuma chave de WhatsApp
  gravada lá — hoje ela guarda o token do banco do escritório.
- Fila persistente de saída com retry. Envio síncrono dentro do request não
  serve (ver §4.1).
- Idempotência na saída, para o retry não mandar a mesma mensagem duas vezes.
- Tratamento dos erros da Meta que têm significado de negócio: `131047` (fora
  da janela de 24h), `131026` (não entregável), limite de taxa.

**Boa notícia:** o rastro de entrega já funciona. `mensagens.status` existe e
`atualizar_status()` casa o evento de status com a mensagem por `id_no_canal`.
No dia em que o envio for nosso, o "enviado → entregue → lido" acende sozinho,
sem código novo.

### 3.2 Mídia — guardamos o endereço, nunca buscamos o arquivo

`traducao.py` guarda o `midia_id` de cada áudio, foto, vídeo e documento. E
para por aí: **não existe download nem armazenamento**. A busca por
`boto3`/`S3`/`R2`/storage no repositório inteiro não retorna nada.

Na prática: o cliente manda um áudio, a conversa mostra `[áudio]`, e ninguém
ouve. Num público que fala por áudio — e o próprio schema diz que *"contrato
fechado trocou ~7 áudios; perdido, menos de 1"* — isso não é detalhe, é o
conteúdo da venda.

O que falta: baixar (`GET /{media-id}`, que devolve URL assinada com validade
de ~5 minutos), guardar em object storage, servir na tela, e transcrever áudio.

### 3.3 Tempo real — não existe

A arquitetura descreve o módulo 1 como *"atendimento-beta (Cloud API, SSE)"*.
No código não há SSE, WebSocket nem `StreamingResponse`. O inbox é HTML
renderizado no servidor com redirect `303`.

Quem atende precisa apertar F5 para ver se chegou mensagem. Para um painel de
consulta, tudo bem. Para a tela onde a pessoa passa o dia, não.

### 3.4 Operação de equipe — o que o Atende Direito de fato vende

Esta é a camada mais subestimada, e o `sentinela.py` já reconhece o buraco:

> *"Não inventa dono. O sistema de origem destacava 'lead órfão, sem
> atendente'; aqui não existe nada ligando conversa a atendente, e fingir que
> existe seria apontar cobrança para a pessoa errada."*

Confirmado: `atendente_id` existe em `contratos` (comercial), **não existe em
`conversas`**. Sem isso não há fila, não há "assumir", não há métrica por
pessoa — e duas atendentes respondem a mesma conversa sem saber.

Falta, em ordem de dor:

1. `conversas.atendente_id` + estados (aberta / em atendimento / resolvida).
2. Transferência entre atendentes, com histórico de quem passou para quem.
3. Nota interna **na conversa**. Hoje `POST /inbox/{id}/anotar` grava no
   *contato* — a decisão está justificada ("a anotação vale na próxima"), mas
   não substitui o recado sobre este atendimento.
4. Etiquetas e respostas rápidas.

### 3.5 Templates (HSM) — a janela é calculada, mas não há saída dela

A janela de 24 horas é levada a sério: `conversas.ultima_msg_cliente_em` existe
só para ela, o Sentinela tem uma faixa inteira chamada `janela_fechando`, e a
tela avisa antes do campo de resposta.

Mas **não há cadastro nem envio de template aprovado**. O sistema diz "a
resposta livre já se perdeu, vale um modelo aprovado" — e não tem nenhum.

Duas das cinco faixas do Sentinela (`janela_fechada` e `esfriando`) terminam
numa ação que o produto não consegue executar.

Falta: sincronizar os templates da WABA, enviar com variáveis, e medir custo
por conversa (a arquitetura já prevê o metering no plano).

### 3.6 Conexão do número — manual hoje, e a Fase 2 é longa

`canais_whatsapp` faz a tradução número→escritório e recusa duplicidade. A
parte de dentro está pronta.

A de fora não: hoje alguém copia o `phone_number_id` do painel da Meta e cola
em `/admin`. Para "plataforma" em vez de "ferramenta", é preciso Embedded
Signup — e a própria arquitetura já dimensiona: virar Meta Tech Provider exige
app verificado, *business verification* e **4 a 8 semanas de processo**.

Isso não bloqueia nada agora, mas é o item de maior *lead time* da lista
inteira. Se for para acontecer, o relógio começa antes do código.

---

## 4. Os riscos que eu levantaria antes de começar

### 4.1 Sair do espelho inverte a natureza do risco

Está escrito no topo do `webhook.py`, e é a frase mais importante do módulo:

> *"Nada do que existe hoje muda de lugar, e nenhum atendimento depende deste
> código estar de pé. (...) é o único desenho em que uma falha nossa não deixa
> cliente sem resposta."*

Hoje, se a Jusmart cair, ninguém percebe. **No dia em que o envio for nosso,
uma falha nossa é cliente sem resposta.** Isso muda o que o sistema precisa
ter: fila persistente, retry, alarme quando a fila para, e alguém olhando.

Não é motivo para não fazer — é motivo para não fazer o envio síncrono dentro
do request do webhook.

### 4.2 O período de convivência é onde nasce a mensagem duplicada

Hoje as respostas saem pelo Atende Direito e chegam aqui como
`tipo='fora_do_sistema'`, sem texto — só o aviso de que saíram. Enquanto os
dois sistemas estiverem ligados no mesmo número e ambos puderem enviar, o
cliente pode receber duas vezes.

O corte precisa ser por número, e precisa ser combinado: um número sai do
Atende Direito e entra na Jusmart de uma vez, não os dois em paralelo.

### 4.3 O `payload` cru é o ponto de concentração de dado sensível

`webhooks_whatsapp` guarda o JSON completo de todo evento — telefone e texto de
todo mundo, **de todos os escritórios, no mesmo banco central**, sem prazo de
expurgo. É a única tabela do produto que cruza tenants e ainda contém conteúdo
de conversa.

A decisão de gravar o cru está certa e não deve mudar. O que falta é retenção:
depois de processado, o cru não precisa viver para sempre. Sugiro definir prazo
(30–90 dias) e um job de expurgo antes de o volume crescer.

### 4.4 O webhook faz trabalho demais dentro do request

`_processar()` abre o banco do escritório e grava, tudo dentro da chamada da
Meta. A arquitetura já prevê o desenho certo — *"→ enfileira → worker
processa"* — e o código ainda não enfileira. Sob volume (ou com o Turso lento),
isso é o primeiro gargalo, e o sintoma é a Meta reentregando.

---

## 5. O caminho que eu recomendaria

Em ordem, e cada etapa entrega valor sozinha:

| # | Etapa | Por que nesta ordem |
|---|---|---|
| 1 | **Fila + worker na recepção** | Tira o trabalho do request antes de dobrar o volume. Pré-requisito de tudo. |
| 2 | **Envio de texto**, token por tenant em `tenant_secrets`, fila de saída com retry | É o que transforma espelho em atendimento. O rastro de entrega acende sozinho. |
| 3 | **Atendente na conversa** + estados + transferência | Sem isso, dois respondem o mesmo lead. Barato e resolve a dor operacional. |
| 4 | **Mídia**: download, storage, transcrição de áudio | O conteúdo da venda está aqui. Depende de escolher storage (nada hoje). |
| 5 | **Tempo real** (SSE) | Depois que a tela virou o lugar onde se trabalha, o F5 vira insuportável. |
| 6 | **Templates aprovados** | Fecha as duas faixas do Sentinela que hoje não têm ação possível. |
| 7 | **Embedded Signup** | Maior *lead time*. Começar o processo com a Meta em paralelo à etapa 1. |

**A etapa 2 é a que muda o produto de categoria.** As etapas 1 e 3 são baratas
e eu faria antes por segurança e por dor operacional, respectivamente.

---

## 6. O que este documento não cobre

- Não li a "tarefa Atende Direito" onde o sistema foi desenvolvido — a análise
  é do código que está no `main` do Jusmart. Se houver trabalho em andamento
  fora dele, parte do que listei como faltando pode já existir.
- Não avaliei custo por conversa da Cloud API nem o modelo de cobrança do
  plano.
- Não avaliei o Atende Direito como produto — comparei com o que o próprio
  código da Jusmart documenta sobre ele.
