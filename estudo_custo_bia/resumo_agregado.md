# Estudo de custo da Bia — resumo agregado

Semana de 05/10/2026 (seg, 0h de Brasília) a 10/10/2026 ~10h. Só números, categorias e
percentuais: nenhum nome, telefone ou trecho de conversa. O relatório completo, com as
conversas, ficou com o dono do escritório.

Base: 7.230 mensagens, 461 conversas, 1.631 chamadas de IA (sp_ia_uso), 12.687 eventos
da Bia (sp_bia_log), 797 mudanças de etapa. Leitura individual das 293 conversas que
gastaram IA; as 66 afirmações mais graves foram revistas por um verificador cético
(46 confirmadas, 20 exageradas no tom, nenhuma refutada no fato principal).
Câmbio usado: R$ 5,40 por US$.

## 1. Reconciliação do gasto

| | US$/dia | R$/dia |
|---|---|---|
| Medido pelo sistema (sp_ia_uso) | 14,01 | 75,65 |
| **Real estimado** (preço vigente do claude-sonnet-5) | **9,42** | **50,87** |
| Recargas da API nos recibos da Anthropic (1–9/10) | 9,09 | 49,09 |

* **O medidor superestima em 50%.** `PRECO` em `app/services/sentinela_bia.py` passa o
  claude-sonnet-5 de US$ 2/10 para 3/15 por milhão de tokens depois de 31/08. O preço
  vigente continua 2/10, e os recibos confirmam (real ≈ 2/3 do medido).
* **Os R$ 200/dia não vêm da IA.** Assinaturas dos sistemas somam ≈ R$ 120/dia (Claude
  Max ≈ R$ 37/dia, API ≈ R$ 51/dia, demais ≈ R$ 31/dia). O Meta Ads gastou ≈ R$ 259/dia
  em outubro (R$ 8.815 em setembro).
* Origens redes / redes_longo / redes_pesquisa: só 1 linha na semana (registro começou em 10/10).

### Por origem (real, média por dia)

| Origem | Chamadas na semana | US$/dia real | % |
|---|---|---|---|
| laco (Bia sozinha) | 1.187 | 6,71 | 71% |
| assistente | 101 | 1,26 | 13% |
| sugestao ("Bia sugerir") | 95 | 0,69 | 7% |
| bia_responder | 55 | 0,46 | 5% |
| andamento | 51 | 0,15 | 2% |
| resumo, rascunho_tecnico, djen, redes, frente | 142 | 0,14 | 2% |

### Anatomia de uma chamada do laço (média, US$ ~0,028 real)

| Parte | Tokens | % do custo |
|---|---|---|
| Adendo do lead + histórico, SEM cache | ~6.400 | 46% |
| Prompt fixo lido do cache | ~54.900 | 40% |
| Gravação de cache | ~1.000 | 9% |
| Saída | ~144 | 5% |

Anatomia registrada (10/10): prompt de 124.545 caracteres, adendo de 11–12 mil, histórico
de 14–15 mil caracteres. Nenhuma conversa é cara sozinha (máximo US$ 0,73 na semana);
as 50 mais caras somam 47% do gasto atribuído.

## 2. Desperdício por categoria

Custo atribuído às conversas: US$ 37,82 em 5,19 dias (US$ 7,29/dia). Parcela estimada na
leitura de cada conversa; categorias podem se sobrepor.

| Categoria | Conversas | US$/dia | % |
|---|---|---|---|
| Geração descartada pelo filtro (dinheiro_cedo, rascunho_sem_repasse, empilhamento) | 103 | 1,02 | 14,0% |
| Lead sem caso / desqualificado ainda com IA | 61 | 0,76 | 10,4% |
| Repetição de balões e perguntas | 84 | 0,70 | 9,5% |
| Conversa longa sem avanço | 45 | 0,70 | 9,5% |
| Bia depois que a equipe assumiu | 48 | 0,52 | 7,1% |
| Follow-up automático | 68 | 0,42 | 5,8% |
| Madrugada / fora do horário | 20 | 0,20 | 2,8% |
| Resposta a "ok" / figurinha / reação | 22 | 0,15 | 2,0% |
| Jurídico / clientes | 6 | 0,07 | 0,9% |
| Outros | 113 | 0,74 | 10,2% |

Métricas diretas: 220 de 2.280 mensagens da Bia saíram depois de alguém da equipe falar;
72 respostas a "ok"/reação/figurinha; 34 mensagens de madrugada; 98 chamadas do laço (8%)
sem mensagem enviada.

## 3. Equipe

* Papel nas 293 conversas: ausente 129 (81 delas leads que nem responderam/preencheram),
  copiloto 60, ativa 82.
* 31 leads chegaram a "Qualificado pela Bia"; 17 ficaram sem nenhuma mensagem humana;
  nos demais, de 9 min a 17 h até a primeira mensagem de gente.
* SDR principal: 530 mensagens, 1ª resposta mediana 19 min; ativa em 69 conversas,
  copiloto em 49. Carteira de 190 leads com 96 sem contato humano na semana.
* Closer: mediana 51 min; em parte das conversas só aciona "Bia responder".
* Recepção/agenda: copiloto em 11 de 15 conversas.
* "Bia sugerir" (95 chamadas) é seguido de texto próprio da pessoa na maioria dos casos.

## 4. Erros recorrentes da Bia (conversas, aproximado por tema)

| Conversas | Erro |
|---|---|
| 51 | Repete balões (autoridade, "relatório técnico", urgência) |
| 48 | Fala de valor cedo, contra a regra do modo SDR (o filtro descarta e ela tenta de novo) |
| 35 | Promete resultado ("praticamente garantidos", "já revertemos") |
| 29 | Trava no laço de recusa (3×3 gerações, nenhuma enviada) |
| 27 | Ignora sinal do lead (impaciência, dado já informado) |
| 26 | Inventa ou erra dado (nota, ano, lista) |
| 22 | Sem lista de anuláveis do concurso (corte_desconhecido) |
| 21 | Insiste com lead sem caso / sem renda |
| 19 | Assina como pessoa da equipe |
| 10 | Usa decisão de outro concurso como "caso igual" |

## 5. Cortes ordenados por economia (estimativa sobre a semana)

| # | Corte | US$/dia | R$/mês |
|---|---|---|---|
| 0 | Corrigir `PRECO` do claude-sonnet-5 para 2/10 (sem economia; sem isso toda medição sai 50% inflada) | — | — |
| 1 | Histórico e adendo atrás do cache (hoje 46% do custo da chamada sem cache) | 2,70 | 437 |
| 2 | Recusa do filtro: no máximo 1 nova geração, depois texto pronto + chamado à SDR | 1,00 | 162 |
| 3 | Parar a IA em lead desqualificado/sem caso/prescrito (resposta pronta) | 0,80 | 130 |
| 4 | Balões de autoridade/relatório/casos uma vez por conversa (estado no adendo) | 0,70 | 113 |
| 5 | Assistente: fixar prefixo (grava ~10.700 tokens de cache por chamada e lê pouco) | 0,60 | 97 |
| 6 | Follow-up por template fixo; fora do horário, resposta pronta | 0,60 | 97 |
| 7 | Silenciar a Bia após a 1ª mensagem humana; tirar "Bia responder" de SDR/closer | 0,50 | 81 |
| 8 | Não disparar o laço para "ok", reação e figurinha | 0,15 | 24 |

Soma ≈ US$ 7/dia, com sobreposição: na prática 50–70% do gasto atual da API
(≈ R$ 750–1.050/mês). Para comparação: Meta Ads ≈ R$ 8.000/mês e Claude Max R$ 1.100/mês.

## 6. Método e limites

* Antes de 10/10 ~01h UTC, sp_ia_uso não tinha o número do lead: cada chamada laco /
  bia_responder foi ligada à mensagem da Bia enviada de −60 s a +120 s; cada "sugestao" à
  mensagem humana até 15 min depois. 98 chamadas do laço ficaram sem mensagem.
* Parcelas de desperdício e papel da equipe são julgamento de leitura (21 leitores),
  não métrica do sistema; temas de erro contados por palavra-chave nos achados.
* Somente leitura: nenhuma mensagem enviada, nada gravado no banco, sp_config não lido.
