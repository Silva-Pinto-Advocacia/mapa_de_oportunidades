"""
Silva Pinto Advocacia - Painel de Oportunidades v6.2
====================================================
Mudancas:
- Persistencia via Turso (SQLite na nuvem) - dados nao se perdem mais
- Fallback automatico para SQLite local se TURSO_URL nao configurado
- Tier 3: queries melhoradas com Reddit, YouTube, Instagram, TikTok
- Concorrentes monitorados: Pedro Auar, Luanda Naiara, Advogado de Concurso
"""

import os
import json
import re
import logging
import sqlite3
import threading
import traceback
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager
from pathlib import Path

from flask import Flask, request, jsonify, Response
import anthropic

# Config
APP_VERSION = "v7.0.0-rc1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

log.info("=" * 70)
log.info("Silva Pinto Oportunidades %s INICIANDO", APP_VERSION)
log.info("=" * 70)

# Banco: Turso (na nuvem, persistente) ou SQLite local (efemero, /tmp do Render)
TURSO_URL = os.environ.get("TURSO_URL", "").strip()
TURSO_TOKEN = os.environ.get("TURSO_TOKEN", "").strip()
DB_PATH = Path(os.environ.get("DB_PATH", "/tmp/oportunidades.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

USANDO_TURSO = bool(TURSO_URL and TURSO_TOKEN)
if USANDO_TURSO:
    log.info("DB: Turso (HTTP API direta) em %s", TURSO_URL.replace("libsql://", "").replace("https://", ""))
else:
    log.info("DB: SQLite local em %s (sera apagado se Render reiniciar)", DB_PATH)

CRON_SECRET = os.environ.get("CRON_SECRET", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL_NAME = os.environ.get("MODEL_NAME", "claude-sonnet-4-5")
MODEL_TIER1 = os.environ.get("MODEL_TIER1", "claude-sonnet-4-5")
MODEL_TIER23 = os.environ.get("MODEL_TIER23", "claude-haiku-4-5-20251001")
DEMO_MODE = os.environ.get("DEMO_MODE", "0") == "1"

# Notificacao Discord (webhook URL). Se vazio, nao envia.
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

# YouTube Data API (opcional). Se vazio, pula enrichment.
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()

# Reddit API (opcional). Se um dos campos vazio, pula enrichment Reddit.
REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "").strip()
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()

app = Flask(__name__)


CATEGORIAS = {
    "elim_ativas": {
        "tier": 1,
        "label": "Eliminacoes ativas",
        "flag": "QUENTE",
        "queries": [
            "gabarito definitivo concurso publico {mes_ano}",
            "resultado final concurso publico eliminados {mes_ano}",
            "nota de corte concurso publico aprovados {mes_ano}",
            "FGV Cebraspe Vunesp gabarito polemica concurso {mes_ano}",
            # Adicionado v6.4.10: nomeacoes / excedentes / decretos / posses
            "nomeacao excedentes concurso publico decreto {mes_ano}",
            "convocacao posse concurso publico {mes_ano}",
            "minuta decreto nomeacao concurso CNU AFT {mes_ano}",
            "homologacao concurso publico decreto governo {mes_ano}",
        ],
        "descricao": (
            "Detectar EVENTOS QUENTES afetando candidatos de concursos publicos AGORA. "
            "Cobre dois grandes grupos:\n"
            "  (A) ELIMINACOES: gabaritos definitivos publicados nos ultimos 15 dias, "
            "listas de resultados eliminando candidatos, notas de corte recem-divulgadas.\n"
            "  (B) NOMEACOES E POSSES: convocacoes oficiais de aprovados, decretos do "
            "governo nomeando excedentes, homologacao de concursos, posses agendadas, "
            "minutas de decreto em tramitacao. Tudo isso afeta direta e imediatamente o "
            "candidato (precisa apresentar documentos, ir a posse, eventualmente recorrer).\n"
            "ATENCAO CRITICA: trazer APENAS noticias factuais de EVENTOS ESPECIFICOS "
            "(ex: 'Decreto nomeia 900 excedentes da AFT', 'TJ-MG convoca aprovados para "
            "posse'). NAO incluir: artigos doutrinarios, analises tematicas, posts de "
            "blog opinativos, materiais educacionais. NAO incluir publicacoes de edital "
            "novo (isso e categoria radar_volume)."
        ),
        "campos_extras": ["concurso", "banca", "fase_eliminacao", "candidatos_estimados"],
        "max_idade_dias": 15,
    },
    "taf_fases": {
        "tier": 1,
        "label": "Fases pos-prova",
        "flag": "FASE",
        "queries": [
            "TAF concurso eliminados resultado {mes_ano}",
            "psicotecnico concurso inapto recurso {mes_ano}",
            "investigacao social eliminado concurso {mes_ano}",
            "convocacao TAF concurso PMERJ PRF PM PC CBMDF {mes_ano}",
        ],
        "descricao": (
            "Eliminacao em fases POS-PROVA OBJETIVA. As fases sao DISTINTAS, "
            "NAO confunda uma com outra. Use SEMPRE o nome correto da fase no "
            "campo 'fase_eliminacao':\n"
            "  - 'TAF' (teste de aptidao fisica - corrida, barra, abdominal)\n"
            "  - 'Psicotecnico' (avaliacao psicologica)\n"
            "  - 'Investigacao social' (vida pregressa, antecedentes)\n"
            "  - 'Exame medico' (saude fisica)\n"
            "  - 'Heteroidentificacao' (verificacao de auto-declaracao racial)\n"
            "ATENCAO: TAF e FISICO. Psicotecnico e PSICOLOGICO. Sao coisas DIFERENTES. "
            "NAO classifique psicotecnico como TAF. NAO classifique investigacao social "
            "como TAF. Cada fase tem seu nome proprio. Trazer APENAS NOTICIAS FACTUAIS "
            "de eventos especificos (convocacoes, resultados, eliminacoes) dos ultimos "
            "15 dias. NAO incluir artigos doutrinarios ou analises tematicas."
        ),
        "campos_extras": ["concurso", "fase_eliminacao", "tipo_irregularidade"],
        "max_idade_dias": 15,
    },
    "recurso_anulacao": {
        "tier": 1,
        "label": "Questoes passiveis de recurso",
        "flag": "RECURSO",
        "queries": [
            "questao anulada banca concurso {mes_ano}",
            "gabarito definitivo alterado concurso {mes_ano}",
            "recursos deferidos banca concurso {mes_ano}",
            "Estrategia Gran QConcursos questao polemica {mes_ano}",
        ],
        "descricao": (
            "Questoes polemicas de provas RECENTES (ultimos 15 dias) - candidatas a "
            "anulacao. Foco em: questoes ja anuladas pela banca, recursos deferidos, "
            "questoes apontadas como polemicas por professores/cursinhos. "
            "Trazer APENAS NOTICIAS FACTUAIS de eventos especificos. NAO incluir "
            "artigos doutrinarios ou analises tematicas."
        ),
        "campos_extras": ["concurso", "banca", "questao_numero", "afetados_estimados"],
        "max_idade_dias": 15,
    },
    "radar_volume": {
        "tier": 2,
        "label": "Radar de volume - novos concursos",
        "flag": "VOLUME",
        "queries": [
            "concurso publico inscritos abertas {mes_ano}",
            "edital concurso policial militar estado {mes_ano}",
            "concurso publico FGV Cebraspe Vunesp edital aberto {mes_ano}",
            "novo edital PM PC guarda municipal {mes_ano}",
            "pciconcursos concursos abertos {mes_ano}",
        ],
        "descricao": (
            "NOVOS CONCURSOS abertos com inscricoes ainda vigentes "
            "(prova objetiva ainda NAO realizada) - editais publicados nos "
            "ultimos 12 meses. Para cada concurso INFORMAR EXPLICITAMENTE: "
            "vagas, salario inicial, banca, prazo de inscricao, "
            "data prevista da prova."
        ),
        "campos_extras": ["concurso", "cargo", "vagas", "salario", "banca", "prazo_inscricao", "data_prova"],
        "max_idade_dias": 365,
    },
    "jurisprudencia": {
        "tier": 2,
        "label": "Jurisprudencia estrategica",
        "flag": "JURISPRUDENCIA",
        "queries": [
            "STJ decisao concurso publico candidato {ano}",
            "STF sumula concurso publico eliminacao {ano}",
            "TJ liminar concurso publico candidato deferida {ano}",
            "mandado de seguranca concurso STJ {ano}",
            "TAF ilegal decisao judicial {ano}",
        ],
        "descricao": (
            "Decisoes de tribunais (STJ, STF, TJs) FAVORAVEIS aos candidatos "
            "em concursos publicos. APENAS DECISOES DOS ULTIMOS 12 MESES. "
            "Extrair tribunal, tema, numero do processo."
        ),
        "campos_extras": ["tribunal", "tema", "numero_processo", "tese"],
        "max_idade_dias": 365,
    },
    "sentimento": {
        "tier": 3,
        "label": "Sentimento do candidato",
        "flag": "VIRAL",
        "queries": [
            "site:reddit.com/r/concurseiros eliminado",
            "site:reddit.com concurso TAF reprovado injusto",
            "site:reddit.com gabarito errado banca",
            "site:youtube.com fui eliminado concurso desabafo",
            "fui eliminado concurso publico desabafo {ano}",
            "concurso reprovado psicotecnico processo {mes_ano}",
            "candidato eliminado TAF processo judicial {ano}",
            "investigacao social eliminado concurso absurdo",
        ],
        "descricao": (
            "Conteudo de candidatos REVOLTADOS com eliminacao em concursos. "
            "Util para: hooks de Reels (citacoes literais), entender padroes "
            "emocionais, identificar processos de concursos onde candidatos "
            "estao buscando ajuda juridica. Procurar em: Reddit r/concurseiros, "
            "YouTube (videos de desabafo, lives), threads de Twitter/X, "
            "comentarios em portais de noticias sobre concursos. EXTRAIR "
            "sempre que possivel: (a) citacao LITERAL do candidato entre aspas, "
            "(b) qual concurso/banca foi mencionado, (c) padrao emocional "
            "(revolta, decepcao, medo, indignacao). Aceitar posts informais. "
            "Para cada item, prefira link da postagem real (reddit.com/r/X/comments/Y/), "
            "mas link do canal/perfil tambem e aceito."
        ),
        "campos_extras": ["citacao_candidato", "concurso_mencionado", "padrao_emocional"],
        "max_idade_dias": 45,
    },
    "concorrencia": {
        "tier": 3,
        "label": "Movimentos da concorrencia",
        "flag": "CONCORRENCIA",
        "queries": [
            # Concorrentes principais
            "Pedro Auar advogado liminar concurso publico {ano}",
            "Luanda Naiara advogada concurso publico decisao {ano}",
            "Marcus Peterson advogado concurso publico servidor {ano}",
            "Oliva e Souza advocacia concurso publico {ano}",
            "Safe e Lima advogados concurso publico {ano}",
            "Agnaldo Bastos advogado concurso publico {ano}",
            "Peterson e Escobar advogados concurso publico {ano}",
            # Buscas por escritorio em portais juridicos
            "site:jusbrasil.com.br Pedro Auar concurso",
            "site:jusbrasil.com.br Luanda Naiara concurso",
            "site:jusbrasil.com.br Marcus Peterson concurso",
            "site:migalhas.com.br advogado concurso publico liminar {ano}",
            "site:conjur.com.br concurso publico candidato decisao {mes_ano}",
            # Generico - liminar ganha por advogado
            "advogado de concurso liminar deferida candidato {ano}",
            "advocacia especializada concurso publico vitoria {mes_ano}",
            # YouTube dos concorrentes
            "site:youtube.com Pedro Auar concurso",
            "site:youtube.com Luanda Naiara concurso publico",
            "site:youtube.com advogado de concurso eliminacao",
            "site:youtube.com Marcus Peterson concurso",
        ],
        "descricao": (
            "Movimentos dos CONCORRENTES DIRETOS do escritorio Silva Pinto.\n"
            "Escritorios concorrentes monitorados:\n"
            "  1. PEDRO AUAR (perfis @pedroauarconcursos, @pedroauaroab no Instagram, "
            "canal proprio no YouTube)\n"
            "  2. LUANDA NAIARA (@luandanaiaraadv, canal YouTube proprio)\n"
            "  3. ADVOGADO DE CONCURSO / OLIVA E SOUZA (advogadodeconcurso.com / olivaesouza.com.br)\n"
            "  4. MARCUS PETERSON FIRMA DE ADVOGADOS (marcuspeterson.adv.br - MG)\n"
            "  5. PETERSON E ESCOBAR ADVOGADOS (petersoneescobar.adv.br)\n"
            "  6. SAFE E LIMA ADVOGADOS (safeelima.adv.br)\n"
            "  7. AGNALDO BASTOS ADVOCACIA (agnaldobastos.adv.br)\n"
            "\n"
            "Foco: encontrar materia, decisao judicial, video do YouTube, post de blog, "
            "Reel ou postagem ESPECIFICA - nao apenas o link do perfil/site institucional. "
            "Procurar em:\n"
            "  - Sites juridicos: jusbrasil, migalhas, conjur, jota, lex magister\n"
            "  - Decisoes em DJE (Diario da Justica Eletronico) onde nome do "
            "advogado consta como patrono da causa\n"
            "  - Blogs proprios dos escritorios (.adv.br) com novos posts\n"
            "  - YouTube: titulos e descricoes de videos recentes desses canais\n"
            "  - Materias de portais de concurso (estrategia, qconcursos, gran)\n"
            "  - Twitter/X com mencao do nome do advogado\n"
            "\n"
            "Detectar: (a) NOVO concurso em que estao atuando como patronos, "
            "(b) tese ou estrategia inovadora descrita em video/post, "
            "(c) liminar ganha com repercussao na imprensa, "
            "(d) gap de mercado (tipo de caso que eles atendem e o Silva Pinto nao), "
            "(e) novo conteudo de marketing/educacional viralizando. "
            "EXTRAIR: (escritorio_concorrente) qual dos 7 e o autor; "
            "(concurso_tema) qual concurso/tema; "
            "(gap_identificado) o que eles fazem que Silva Pinto ainda nao faz."
        ),
        "campos_extras": ["escritorio_concorrente", "concurso_tema", "gap_identificado"],
        "max_idade_dias": 45,
    },
}


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS oportunidades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    categoria TEXT NOT NULL,
    tier INTEGER NOT NULL,
    flag TEXT,
    titulo TEXT NOT NULL,
    descricao TEXT,
    orgao TEXT,
    estado TEXT,
    concurso TEXT,
    cargo TEXT,
    banca TEXT,
    vagas TEXT,
    salario TEXT,
    prazo_inscricao TEXT,
    data_prova TEXT,
    fase_atual TEXT,
    data_publicacao TEXT,
    extras_json TEXT,
    link TEXT,
    relevancia INTEGER DEFAULT 5,
    etapa_concurso TEXT,
    lido INTEGER DEFAULT 0,
    arquivado INTEGER DEFAULT 0,
    selecionado_marketing INTEGER DEFAULT 0,
    data_coleta TEXT NOT NULL,
    hash_unico TEXT UNIQUE,
    metricas_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_categoria ON oportunidades(categoria);
CREATE INDEX IF NOT EXISTS idx_tier ON oportunidades(tier);
CREATE INDEX IF NOT EXISTS idx_etapa ON oportunidades(etapa_concurso);
CREATE INDEX IF NOT EXISTS idx_data ON oportunidades(data_coleta);
CREATE INDEX IF NOT EXISTS idx_lido ON oportunidades(lido);

CREATE TABLE IF NOT EXISTS execucoes_cron (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    data_execucao TEXT NOT NULL,
    tipo_run TEXT,
    categorias_processadas TEXT,
    itens_novos INTEGER DEFAULT 0,
    duracao_segundos REAL,
    sucesso INTEGER DEFAULT 1,
    erro TEXT
);

CREATE TABLE IF NOT EXISTS notas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    oportunidade_id INTEGER NOT NULL,
    texto TEXT NOT NULL,
    data_criacao TEXT NOT NULL,
    FOREIGN KEY (oportunidade_id) REFERENCES oportunidades(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_notas_op ON notas(oportunidade_id);

-- Hashes de itens que o usuario excluiu manualmente. Bloqueia re-insercao
-- desses itens em coletas futuras (o Claude pode achar a mesma noticia de novo).
CREATE TABLE IF NOT EXISTS excluidos (
    hash_unico TEXT PRIMARY KEY,
    titulo TEXT,
    link TEXT,
    data_exclusao TEXT NOT NULL
);

-- v7: Concursos que o escritorio escolheu monitorar. Cada um vira uma "ficha viva"
-- que agrupa as noticias encaixadas nele.
--   prioridade: 'urgente' | 'importante' | 'naourgente' (semaforo vermelho/dourado/verde)
--   palavras_chave: lista separada por virgula, usada pela IA pra encaixar noticias
CREATE TABLE IF NOT EXISTS concursos_monitorados (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    banca TEXT,
    palavras_chave TEXT,
    prioridade TEXT DEFAULT 'importante',
    vagas TEXT,
    data_criacao TEXT NOT NULL,
    ativo INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_concmon_ativo ON concursos_monitorados(ativo);

-- v7: Concursos novos que apareceram repetidamente mas ainda nao sao monitorados.
-- Viram sugestao na caixa de entrada ("apareceu 3x, quer monitorar?").
CREATE TABLE IF NOT EXISTS sugestoes_concurso (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome_normalizado TEXT UNIQUE,
    nome_exibicao TEXT NOT NULL,
    banca TEXT,
    vezes INTEGER DEFAULT 1,
    ultima_evidencia TEXT,
    primeira_aparicao TEXT NOT NULL,
    ultima_aparicao TEXT NOT NULL,
    status TEXT DEFAULT 'pendente'
);
CREATE INDEX IF NOT EXISTS idx_sugest_status ON sugestoes_concurso(status);
"""


# v7: colunas de triagem adicionadas a tabela oportunidades via migracao.
# Definidas aqui pra referencia:
#   concurso_id INTEGER         -> FK pro concurso monitorado (NULL = nao encaixado)
#   status_triagem TEXT         -> 'pendente' | 'confirmado' | 'transversal' | 'sem_concurso'
#   sugestao_concurso TEXT      -> nome do concurso que a IA sugeriu (pra fila de confirmacao)
#   tipo_transversal TEXT       -> 'jurisprudencia' | 'concorrencia' | 'tendencia' (se transversal)


# Migracao: adiciona coluna metricas_json se a tabela ja existe sem ela
def _ensure_metricas_column():
    """Adiciona coluna metricas_json em bancos antigos. Idempotente."""
    try:
        with db_conn() as conn:
            try:
                conn.execute("SELECT metricas_json FROM oportunidades LIMIT 1")
                return  # ja existe
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE oportunidades ADD COLUMN metricas_json TEXT")
                log.info("DB: coluna metricas_json adicionada")
            except Exception as e:
                msg = str(e).lower()
                if "duplicate" not in msg and "exist" not in msg:
                    log.warning("Falha ao adicionar metricas_json: %s", e)
    except Exception as e:
        log.warning("_ensure_metricas_column erro: %s", e)


def _ensure_selecionado_column():
    """Adiciona coluna selecionado_marketing em bancos antigos. Idempotente.

    Boolean que marca se o item ja foi enviado para o sistema comercial via
    botao Selecionar. Quando 1, o botao no card vem desabilitado e em verde.
    """
    try:
        with db_conn() as conn:
            try:
                conn.execute("SELECT selecionado_marketing FROM oportunidades LIMIT 1")
                return
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE oportunidades ADD COLUMN selecionado_marketing INTEGER DEFAULT 0")
                log.info("DB: coluna selecionado_marketing adicionada")
            except Exception as e:
                msg = str(e).lower()
                if "duplicate" not in msg and "exist" not in msg:
                    log.warning("Falha ao adicionar selecionado_marketing: %s", e)
    except Exception as e:
        log.warning("_ensure_selecionado_column erro: %s", e)


def _ensure_triagem_columns():
    """v7: adiciona colunas de triagem em oportunidades. Idempotente.

    concurso_id        -> FK pro concurso monitorado (NULL = nao encaixado)
    status_triagem     -> 'pendente'|'confirmado'|'transversal'|'sem_concurso'
    sugestao_concurso  -> nome do concurso que a IA sugeriu (fila de confirmacao)
    tipo_transversal   -> 'jurisprudencia'|'concorrencia'|'tendencia'
    """
    colunas = [
        ("concurso_id", "INTEGER"),
        ("status_triagem", "TEXT DEFAULT 'pendente'"),
        ("sugestao_concurso", "TEXT"),
        ("tipo_transversal", "TEXT"),
    ]
    try:
        with db_conn() as conn:
            for nome, tipo in colunas:
                try:
                    conn.execute(f"SELECT {nome} FROM oportunidades LIMIT 1")
                    continue  # ja existe
                except Exception:
                    pass
                try:
                    conn.execute(f"ALTER TABLE oportunidades ADD COLUMN {nome} {tipo}")
                    log.info("DB: coluna %s adicionada", nome)
                except Exception as e:
                    msg = str(e).lower()
                    if "duplicate" not in msg and "exist" not in msg:
                        log.warning("Falha ao adicionar %s: %s", nome, e)
    except Exception as e:
        log.warning("_ensure_triagem_columns erro: %s", e)


class TursoConnWrapper:
    """Wrapper sobre a API HTTP da Turso (Hrana over HTTP - endpoint /v2/pipeline).

    NAO USA biblioteca libsql nem libsql-client. Usa apenas urllib + JSON.
    Funciona em qualquer ambiente porque e HTTPS puro.

    Documentacao: https://docs.turso.tech/sdk/http/quickstart
    """
    class _IntegrityError(Exception):
        pass

    @staticmethod
    def _http_url():
        """Converte libsql://X para https://X/v2/pipeline."""
        url = TURSO_URL
        if url.startswith("libsql://"):
            url = "https://" + url[len("libsql://"):]
        elif url.startswith("wss://"):
            url = "https://" + url[len("wss://"):]
        elif not url.startswith("http"):
            url = "https://" + url
        return url.rstrip("/") + "/v2/pipeline"

    def __init__(self):
        # Conexao logica - cada execute() faz uma chamada HTTP
        # (alternativa: manter uma sessao persistente, mas requests simples sao rapidas)
        self.url = TursoConnWrapper._http_url()
        self.headers = {
            "Authorization": f"Bearer {TURSO_TOKEN}",
            "Content-Type": "application/json",
        }

    def _post(self, payload, timeout=30):
        """POST JSON e retorna response JSON."""
        import urllib.request
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, headers=self.headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _stmt(self, sql, params=()):
        """Monta o objeto stmt no formato Hrana com argumentos posicionais."""
        stmt = {"sql": sql}
        if params:
            args = []
            for p in params:
                if p is None:
                    args.append({"type": "null"})
                elif isinstance(p, bool):
                    args.append({"type": "integer", "value": "1" if p else "0"})
                elif isinstance(p, int):
                    args.append({"type": "integer", "value": str(p)})
                elif isinstance(p, float):
                    args.append({"type": "float", "value": p})
                elif isinstance(p, bytes):
                    import base64
                    args.append({"type": "blob", "base64": base64.b64encode(p).decode("ascii")})
                else:
                    args.append({"type": "text", "value": str(p)})
            stmt["args"] = args
        return stmt

    def execute(self, sql, params=()):
        """Executa SQL via HTTP. Retorna TursoCursor compativel com sqlite3.Cursor."""
        payload = {
            "requests": [
                {"type": "execute", "stmt": self._stmt(sql, params)},
                {"type": "close"},
            ]
        }
        try:
            data = self._post(payload)
        except Exception as e:
            log.error("Turso HTTP execute falhou: %s", e)
            raise

        # Resposta tem formato: {"results":[{"type":"ok","response":{"type":"execute","result":{...}}}, {"type":"ok",...}]}
        results = data.get("results", [])
        if not results:
            return TursoCursor(cols=[], rows=[])

        first = results[0]
        if first.get("type") == "error":
            err = first.get("error", {})
            msg = err.get("message", "erro desconhecido")
            msg_low = msg.lower()
            if "unique" in msg_low or "constraint" in msg_low:
                raise sqlite3.IntegrityError(msg)
            raise Exception(f"Turso error: {msg}")

        result = first.get("response", {}).get("result", {})
        cols_meta = result.get("cols", [])
        cols = [c.get("name", "") for c in cols_meta]
        raw_rows = result.get("rows", [])

        # Cada raw_row e uma lista de objetos {type, value}. Converte para tipos Python nativos.
        rows = []
        for raw_row in raw_rows:
            row = []
            for cell in raw_row:
                if isinstance(cell, dict):
                    t = cell.get("type")
                    v = cell.get("value")
                    if t == "null":
                        row.append(None)
                    elif t == "integer":
                        row.append(int(v) if v is not None else None)
                    elif t == "float":
                        row.append(float(v) if v is not None else None)
                    elif t == "blob":
                        import base64
                        b64 = cell.get("base64", "")
                        row.append(base64.b64decode(b64) if b64 else b"")
                    else:
                        row.append(v)
                else:
                    row.append(cell)
            rows.append(row)

        return TursoCursor(cols=cols, rows=rows)

    def executescript(self, script):
        """Executa multiplos statements separados por ; em UMA chamada HTTP (batch)."""
        statements = [s.strip() for s in script.split(';') if s.strip()]
        if not statements:
            return
        # Monta um pipeline com todos os statements + close
        requests_list = [{"type": "execute", "stmt": {"sql": s}} for s in statements]
        requests_list.append({"type": "close"})
        payload = {"requests": requests_list}
        try:
            data = self._post(payload, timeout=60)
        except Exception as e:
            log.error("Turso executescript falhou: %s", e)
            raise

        # Verifica erros (mas tolera "already exists")
        for r in data.get("results", []):
            if r.get("type") == "error":
                msg = r.get("error", {}).get("message", "").lower()
                if "already exists" in msg:
                    continue
                raise Exception(f"executescript: {r.get('error', {}).get('message')}")

    def commit(self):
        # API HTTP nao tem commit explicito - cada execute ja eh uma transacao implicita
        pass

    def close(self):
        pass


class TursoCursor:
    """Wrapper minimo pra retornar TursoRow. Construido a partir das listas cols+rows."""
    def __init__(self, cols, rows):
        self.columns = list(cols) if cols else []
        self._rows_iter = iter(rows) if rows else iter([])

    def _wrap(self, row):
        if row is None:
            return None
        return TursoRow(self.columns, row)

    def fetchone(self):
        try:
            r = next(self._rows_iter)
            return self._wrap(r)
        except StopIteration:
            return None

    def fetchall(self):
        return [self._wrap(r) for r in self._rows_iter]


class TursoRow:
    """Imita sqlite3.Row: acesso por indice E por nome de coluna (linha['col'])."""
    def __init__(self, columns, values):
        self.columns = columns
        self.values = list(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self.values[key]
        try:
            idx = self.columns.index(key)
        except ValueError:
            raise KeyError(key)
        return self.values[idx]

    def keys(self):
        return list(self.columns)


def _dict_from_row(row):
    """Converte tanto TursoRow quanto sqlite3.Row para dict."""
    if row is None:
        return None
    if isinstance(row, TursoRow):
        return {col: row.values[i] for i, col in enumerate(row.columns)}
    # sqlite3.Row
    return dict(row)


def init_db():
    global USANDO_TURSO
    if USANDO_TURSO:
        try:
            wrapper = TursoConnWrapper()
            try:
                wrapper.executescript(SCHEMA_SQL)
            finally:
                wrapper.close()
            log.info("DB Turso inicializado")
            return
        except Exception as e:
            log.error("Falha ao conectar no Turso: %s. Caindo para SQLite local.", e)
            USANDO_TURSO = False

    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(SCHEMA_SQL)
    log.info("DB SQLite local inicializado em %s", DB_PATH)


@contextmanager
def db_conn():
    """Context manager que retorna conexao Turso ou SQLite, ambos com mesma API."""
    if USANDO_TURSO:
        conn = TursoConnWrapper()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def _normalizar_para_hash(s):
    """Normaliza string para deduplicacao agressiva.

    Aplica:
    - lowercase
    - remove acentos
    - remove pontuacao
    - remove anos (2024, 2025, 2026, 2027)
    - remove datas (xx/xx/xxxx, xx-xx-xxxx)
    - remove palavras vazias do dominio (concurso, edital, gabarito, resultado, novo)
    - colapsa espacos multiplos em um so
    """
    if not s:
        return ""
    import unicodedata
    # lowercase + sem acentos
    s = unicodedata.normalize('NFKD', s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    # remove datas
    s = re.sub(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b', ' ', s)
    # remove anos isolados
    s = re.sub(r'\b20[2-3]\d\b', ' ', s)
    # remove pontuacao
    s = re.sub(r'[^\w\s]', ' ', s)
    # palavras vazias frequentes
    stopwords = {
        "concurso", "concursos", "edital", "editais", "gabarito",
        "gabaritos", "resultado", "resultados", "novo", "nova",
        "publicado", "publicada", "divulgado", "divulgada", "preliminar",
        "definitivo", "oficial", "lista", "listas",
    }
    palavras = [p for p in s.split() if p and p not in stopwords and len(p) > 1]
    return " ".join(palavras)


def hash_for_dedup(titulo, orgao="", link=""):
    """Hash agressivo: usa link normalizado se houver, senao titulo+orgao normalizados.

    Isso captura repeticoes mesmo com pequenas variacoes de titulo
    (ex: 'Concurso PMERJ 2026' e 'PMERJ 2026 Edital' -> mesmo hash).

    Normalizacao de link captura:
      - Diferencas http vs https
      - www. vs sem www
      - Query string (utm_*, ref, etc)
      - Fragment (#secao)
      - Trailing slash
      - Sufixo /amp/ ou /amp (versoes AMP do Google)
      - Duplicacao de barras (//)
    """
    import hashlib
    if link:
        l = link.strip().lower()
        # Tira query string e fragment
        l = re.sub(r'[?#].*$', '', l)
        # Normaliza protocolo
        l = re.sub(r'^https?://', 'https://', l)
        # Tira www.
        l = re.sub(r'^https://www\.', 'https://', l)
        # Tira sufixo /amp ou /amp/
        l = re.sub(r'/amp/?$', '', l)
        # Colapsa barras duplas no path (mantem // do protocolo)
        l = re.sub(r'(?<!:)//+', '/', l)
        # Tira trailing slash
        l = l.rstrip('/')
        base = "L|" + l
    else:
        t_norm = _normalizar_para_hash(titulo)[:120]
        o_norm = _normalizar_para_hash(orgao)[:50]
        base = f"T|{t_norm}|O|{o_norm}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


# ===== VALIDACAO DE LINKS =====

# Whitelist de dominios aceitos. Tudo fora dessa lista e rejeitado.
DOMINIOS_PERMITIDOS = {
    # ====== OFICIAIS / GOVERNO ======
    # (TLDs genericos abaixo cobrem qualquer .gov.br/.jus.br/.mil.br/.mp.br/.leg.br/.edu.br)
    "gov.br", "jus.br", "mil.br",
    "stj.jus.br", "stf.jus.br", "tjsp.jus.br", "tjrj.jus.br",
    "tjmg.jus.br", "tjpr.jus.br", "tjrs.jus.br", "tjba.jus.br", "tjpe.jus.br",
    "tjce.jus.br", "tjes.jus.br", "tjgo.jus.br", "tjam.jus.br", "tjpa.jus.br",
    "trf1.jus.br", "trf2.jus.br", "trf3.jus.br", "trf4.jus.br", "trf5.jus.br",
    "trf6.jus.br", "tst.jus.br", "tcu.gov.br", "cnj.jus.br", "anvisa.gov.br",
    "policiacivil.rj.gov.br", "policiacivil.sp.gov.br", "pf.gov.br", "prf.gov.br",
    "pm.rj.gov.br", "pm.sp.gov.br", "exercito.gov.br", "marinha.mil.br", "fab.mil.br",
    "in.gov.br",  # Imprensa Nacional - DOU
    "planalto.gov.br",
    "agu.gov.br",
    "mpgo.mp.br", "mprj.mp.br", "mpsp.mp.br",

    # ====== PORTAIS JURIDICOS / DECISOES ======
    "jusbrasil.com.br", "migalhas.com.br", "conjur.com.br", "jota.info",
    "lexmagister.com.br", "ambito-juridico.com.br", "direitonet.com.br",
    "consultor-juridico.com.br",

    # ====== CURSOS PREPARATORIOS (todos os grandes) ======
    "estrategiaconcursos.com.br", "estrategia.com.br",
    "grancursosonline.com.br", "gran.com.br",
    "qconcursos.com", "folha.qconcursos.com",  # Folha QConcursos
    "alfaconcursos.com.br", "alfacon.com.br",
    "direcaoconcursos.com.br", "direcaoconcursos.com",
    "tecconcursos.com.br",
    "novaconcursos.com.br",
    "casadoconcurseiro.com.br",
    "cers.com.br",
    "cursoenfase.com.br",
    "cursoclubedaregra.com.br",
    "pontodosconcursos.com.br",
    "focusconcursos.com.br",
    "cursojuridico.com.br",
    "approvaconcursos.com.br", "aprovaconcursos.com.br",
    "preparoconcursos.com.br",
    "estudegratis.com.br", "espacodoconcurseiro.com",

    # ====== PORTAIS DE CONCURSO / NOTICIAS DE CONCURSO ======
    "pciconcursos.com.br",
    "folhadirigida.com.br",
    "jcconcursos.com.br",
    "edital.com.br",
    "concursosnobrasil.com.br", "concursosnobrasil.com",
    "concursosobrasil.com.br",
    "acheconcursos.com.br",
    "pensarcursos.com.br",
    "academiaconcursos.com.br",
    "concursospublicos.gov.br",
    "concursoseeditais.com.br",
    "olhonavaga.com.br",
    "vou-passar.com.br",
    "guiadosconcursos.com.br",
    "ojaiba.com",
    "oesquadraodeelite.com.br",
    "concursosaqui.com.br",
    "concursosabertos.com.br",
    "rateiobarato.com",
    "queropassaremconcursos.com.br",
    "proximosconcursos.com",
    "diariooficialdf.com.br",

    # ====== IMPRENSA DE REFERENCIA ======
    "g1.globo.com", "globo.com", "uol.com.br", "folha.uol.com.br",
    "estadao.com.br", "valor.com.br", "veja.com.br", "exame.com",
    "agenciabrasil.ebc.com.br", "metropoles.com", "correiobraziliense.com.br",
    "gazetadopovo.com.br", "oglobo.globo.com", "extra.globo.com",
    "r7.com", "band.com.br", "cnnbrasil.com.br", "poder360.com.br",
    "noticias.uol.com.br", "economia.uol.com.br", "noticias.r7.com",
    "terra.com.br", "ig.com.br", "infomoney.com.br",
    "diariodepernambuco.com.br", "atribuna.com.br", "agazeta.com.br",
    "otempo.com.br", "estadodeminas.com.br", "em.com.br",

    # ====== BANCAS (sites oficiais) ======
    "cebraspe.org.br", "fgvprojetos.fgv.br", "vunesp.com.br", "ibade.org.br",
    "idcap.org.br", "aocp.com.br", "consulplan.net", "ibfc.org.br",
    "fgv.br", "cesgranrio.org.br", "fundatec.org.br", "fcc.org.br",
    "iades.com.br", "quadrix.org.br", "instituteaocp.org.br",
    "ibam.org.br", "selecon.org.br", "msconcursos.com.br",
    "iesesconcursos.org.br", "iuds.org.br", "ibgp.org.br",

    # ====== ADVOGADOS ESPECIALIZADOS EM CONCURSO (concorrentes monitorados) ======
    "advogadodeconcurso.com",
    "olivaesouza.com.br",
    "marcuspeterson.adv.br",
    "petersoneescobar.adv.br",
    "safeelima.adv.br",
    "agnaldobastos.adv.br",
    "concursoscomvc.com.br",
    "altayrcosta.adv.br",
    "concurseirojuridico.com.br",
    "jbarretoadvogados.com.br",
    "rsadvogadosassociados.com.br",
    # Genericos do dominio .adv.br aceitos via TLD generico abaixo

    # ====== COMUNIDADES / FONTES TIER 3 ======
    "reddit.com", "youtube.com", "youtu.be",
    "twitter.com", "x.com",  # citacoes em tweets viralizando

    # ====== ANTHROPIC (caso o cron self-test apareca) ======
    "anthropic.com",
}


# Sufixos genericos de TLD oficial brasileiro - aceitos automaticamente
TLDS_GENERICOS_PERMITIDOS = (
    ".gov.br",   # qualquer orgao publico
    ".jus.br",   # qualquer ramo da justica
    ".mil.br",   # forcas armadas
    ".mp.br",    # ministerio publico
    ".leg.br",   # legislativo
    ".edu.br",   # ensino superior
    ".adv.br",   # escritorio de advocacia (concorrencia)
)


def _extrair_dominio(url):
    """Extrai dominio raiz da URL. Ex: https://www.foo.com.br/x -> foo.com.br"""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        netloc = urlparse(url).netloc.lower()
        # Tira porta se houver
        netloc = netloc.split(":")[0]
        # Tira www. inicial
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


def _dominio_permitido(url):
    """True se o dominio (ou subdominio) da URL esta na whitelist OU usa TLD oficial."""
    dom = _extrair_dominio(url)
    if not dom:
        return False
    # Match exato
    if dom in DOMINIOS_PERMITIDOS:
        return True
    # Match por sufixo (subdominios explicitos). Ex: noticias.estrategiaconcursos.com.br
    for permitido in DOMINIOS_PERMITIDOS:
        if dom.endswith("." + permitido):
            return True
    # TLDs genericos oficiais (.gov.br, .jus.br, .mil.br, .mp.br, .leg.br, .edu.br)
    # Aceita qualquer subdominio sob esses TLDs porque sao orgaos oficiais.
    for tld in TLDS_GENERICOS_PERMITIDOS:
        if dom.endswith(tld) or dom == tld[1:]:
            return True
    return False


def _normalizar_url_para_match(url):
    """Normaliza URL pra comparar com a lista de citacoes. Tira params, fragment, www, trailing slash."""
    if not url:
        return ""
    u = url.strip().lower()
    u = re.sub(r'[?#].*$', '', u)
    u = u.rstrip('/')
    u = u.replace("://www.", "://")
    return u


def _link_em_citacoes(link, citacoes):
    """True se o link bate com algum link da lista de citacoes (match flexivel)."""
    if not link or not citacoes:
        return False
    target = _normalizar_url_para_match(link)
    if not target:
        return False
    for cit in citacoes:
        cit_norm = _normalizar_url_para_match(cit)
        if not cit_norm:
            continue
        # Match exato OU prefix (item pode ser pagina mais especifica que a citacao)
        if target == cit_norm or target.startswith(cit_norm + "/") or cit_norm.startswith(target + "/"):
            return True
    return False


def render_queries(queries_template, hoje):
    mes_ano = hoje.strftime("%B %Y").lower()
    mes_pt = {
        "january": "janeiro", "february": "fevereiro", "march": "marco",
        "april": "abril", "may": "maio", "june": "junho",
        "july": "julho", "august": "agosto", "september": "setembro",
        "october": "outubro", "november": "novembro", "december": "dezembro",
    }
    for en, pt in mes_pt.items():
        mes_ano = mes_ano.replace(en, pt)
    ano = hoje.strftime("%Y")
    return [q.replace("{mes_ano}", mes_ano).replace("{ano}", ano) for q in queries_template]


FONTES_OFICIAIS = """
FONTES PRIORITARIAS (use de preferencia):
- pciconcursos.com.br, qconcursos.com (concursos)
- estrategiaconcursos.com.br, grancursosonline.com.br (analise tecnica)
- jusbrasil.com.br, migalhas.com.br, conjur.com.br (juridico)
- stj.jus.br, stf.jus.br (sites oficiais de tribunais)
- Sites oficiais de bancas (FGV, Cebraspe, Vunesp, IDECAN, IBADE)
- Sites oficiais de orgaos (PMs, PCs, ministerios, prefeituras)

Para Tier 3 (sentimento/concorrencia): aceite reddit.com, instagram.com,
youtube.com, tiktok.com, telegram, e perfis dos escritorios concorrentes.
"""


def parse_json_robusto(raw):
    """Parser tolerante - Sonnet as vezes retorna texto+JSON misturado."""
    if not raw:
        return None
    # Remove markdown fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
    raw = raw.strip()

    # Tenta direto
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Procura o maior bloco JSON valido
    # Pega do primeiro { ate o ultimo } e tenta parsear pedacos
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        for try_end in (end, raw.rfind("}", start, end)):
            if try_end <= start:
                continue
            candidate = raw[start:try_end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

    # Tenta extrair so o array "itens"
    m = re.search(r'"itens"\s*:\s*\[(.*?)\]\s*}', raw, re.DOTALL)
    if m:
        try:
            return json.loads('{"itens":[' + m.group(1) + ']}')
        except json.JSONDecodeError:
            pass

    return None


# ===== ENRICHMENT: YouTube Data API =====

def _extrair_youtube_video_id(url):
    """Extrai video ID de varios formatos de URL do YouTube."""
    if not url:
        return None
    # youtube.com/watch?v=XXXXXXXXXXX
    m = re.search(r'[?&]v=([A-Za-z0-9_-]{11})', url)
    if m:
        return m.group(1)
    # youtu.be/XXXXXXXXXXX
    m = re.search(r'youtu\.be/([A-Za-z0-9_-]{11})', url)
    if m:
        return m.group(1)
    # /shorts/XXXXXXXXXXX
    m = re.search(r'/shorts/([A-Za-z0-9_-]{11})', url)
    if m:
        return m.group(1)
    return None


def enrich_youtube(link):
    """Pega views, likes, comentarios e data de publicacao de um video do YouTube.

    Retorna dict {views, likes, comments, published_at, title, description_short}
    ou None se nao conseguir.
    """
    if not YOUTUBE_API_KEY or not link:
        return None
    video_id = _extrair_youtube_video_id(link)
    if not video_id:
        return None
    try:
        import urllib.request, urllib.parse
        params = urllib.parse.urlencode({
            "part": "snippet,statistics",
            "id": video_id,
            "key": YOUTUBE_API_KEY,
        })
        url_api = f"https://www.googleapis.com/youtube/v3/videos?{params}"
        req = urllib.request.Request(url_api, headers={"User-Agent": "SilvaPinto/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        items = data.get("items", [])
        if not items:
            return None
        snippet = items[0].get("snippet", {})
        stats = items[0].get("statistics", {})
        return {
            "fonte": "youtube",
            "views": int(stats.get("viewCount", 0)) if stats.get("viewCount") else None,
            "likes": int(stats.get("likeCount", 0)) if stats.get("likeCount") else None,
            "comments": int(stats.get("commentCount", 0)) if stats.get("commentCount") else None,
            "published_at": snippet.get("publishedAt", "")[:10],
            "channel": snippet.get("channelTitle", "")[:80],
        }
    except Exception as e:
        log.warning("YouTube enrich falhou para %s: %s", link, e)
        return None


# ===== ENRICHMENT: Reddit =====

_REDDIT_TOKEN_CACHE = {"token": None, "expires_at": 0}


def _get_reddit_token():
    """Pega/cacheia token OAuth do Reddit (1h validade). Usa client credentials flow."""
    import time as _t
    if not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET:
        return None
    if _REDDIT_TOKEN_CACHE["token"] and _REDDIT_TOKEN_CACHE["expires_at"] > _t.time() + 60:
        return _REDDIT_TOKEN_CACHE["token"]
    try:
        import urllib.request, urllib.parse, base64
        creds = base64.b64encode(f"{REDDIT_CLIENT_ID}:{REDDIT_CLIENT_SECRET}".encode()).decode()
        body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
        req = urllib.request.Request(
            "https://www.reddit.com/api/v1/access_token",
            data=body,
            headers={
                "Authorization": f"Basic {creds}",
                "User-Agent": "SilvaPinto/1.0 (by /u/silvapinto)",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        token = data.get("access_token")
        expires_in = int(data.get("expires_in", 3600))
        _REDDIT_TOKEN_CACHE["token"] = token
        _REDDIT_TOKEN_CACHE["expires_at"] = _t.time() + expires_in
        return token
    except Exception as e:
        log.warning("Reddit token falhou: %s", e)
        return None


def _extrair_reddit_post_id(url):
    """Extrai post id de URL tipo /r/X/comments/POSTID/slug/"""
    if not url:
        return None
    m = re.search(r'/comments/([a-z0-9]{5,12})', url)
    if m:
        return m.group(1)
    return None


def enrich_reddit(link):
    """Pega upvotes, comentarios, subreddit, data de um post do Reddit.

    Retorna dict ou None.
    """
    if not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET or not link:
        return None
    if "reddit.com" not in link.lower():
        return None
    post_id = _extrair_reddit_post_id(link)
    if not post_id:
        return None
    token = _get_reddit_token()
    if not token:
        return None
    try:
        import urllib.request
        url_api = f"https://oauth.reddit.com/api/info?id=t3_{post_id}"
        req = urllib.request.Request(
            url_api,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": "SilvaPinto/1.0 (by /u/silvapinto)",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        children = data.get("data", {}).get("children", [])
        if not children:
            return None
        post_data = children[0].get("data", {})
        from datetime import datetime as _dt
        ts = post_data.get("created_utc", 0)
        pub = _dt.utcfromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""
        return {
            "fonte": "reddit",
            "upvotes": int(post_data.get("ups", 0)),
            "score": int(post_data.get("score", 0)),
            "comments": int(post_data.get("num_comments", 0)),
            "subreddit": post_data.get("subreddit_name_prefixed", "")[:60],
            "published_at": pub,
        }
    except Exception as e:
        log.warning("Reddit enrich falhou para %s: %s", link, e)
        return None


def enrich_metricas(link):
    """Tenta enrich do link em todos os providers. Retorna dict ou None."""
    if not link:
        return None
    link_low = link.lower()
    if "youtube.com" in link_low or "youtu.be" in link_low:
        m = enrich_youtube(link)
        if m:
            return m
    if "reddit.com" in link_low:
        m = enrich_reddit(link)
        if m:
            return m
    return None


# ===== NOTIFICACAO: Discord webhook =====

def notificar_discord(itens_novos):
    """Envia mensagem no Discord com lista de novos itens. Silencioso se DISCORD_WEBHOOK_URL nao configurado."""
    if not DISCORD_WEBHOOK_URL or not itens_novos:
        return
    try:
        import urllib.request

        # Limita a 10 itens por mensagem (Discord tem limite de tamanho)
        limit = 10
        itens_to_send = itens_novos[:limit]
        extra_count = len(itens_novos) - limit

        embeds = []
        for it in itens_to_send:
            rel = it.get("relevancia", 5)
            # Cor do embed segundo a relevancia (BBGGRR -> int)
            cores = {
                10: 0xdc2626, 9: 0xea580c, 8: 0xf97316, 7: 0xeab308,
                6: 0x84cc16, 5: 0x22c55e, 4: 0x10b981, 3: 0x06b6d4,
                2: 0x0ea5e9, 1: 0x2563eb,
            }
            color = cores.get(int(rel), 0x888888)

            titulo = it.get("titulo", "(sem titulo)")[:200]
            desc = it.get("descricao", "")[:300]
            link = it.get("link", "")
            estado = it.get("estado", "")
            banca = it.get("banca", "")
            flag = it.get("flag", "")

            campos_meta = []
            if estado:
                campos_meta.append("\U0001F4CD " + estado)  # round pin
            if banca:
                campos_meta.append("\U0001F3DB\uFE0F " + banca)  # classical building
            if flag:
                campos_meta.append("\U0001F3F7\uFE0F " + flag)  # label
            meta_line = " \u00B7 ".join(campos_meta)

            embed = {
                "title": titulo,
                "description": (meta_line + "\n\n" + desc) if meta_line else desc,
                "color": color,
                "footer": {"text": f"Relevancia {rel}/10"},
            }
            if link:
                embed["url"] = link
            embeds.append(embed)

        content_extra = ""
        if extra_count > 0:
            content_extra = f"\n_(+{extra_count} itens adicionais no painel)_"

        payload = {
            "content": "\U0001F514 **" + str(len(itens_novos)) + " oportunidade(s) nova(s)** no painel Silva Pinto" + content_extra,
            "embeds": embeds,
            "username": "Painel Silva Pinto",
        }

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "SilvaPinto/1.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status >= 300:
                log.warning("Discord webhook retornou status %d", resp.status)
        log.info("Discord notificado: %d itens", len(itens_novos))
    except Exception as e:
        log.warning("Falha ao notificar Discord: %s", e)


def coletar_categoria(api_key, cat_id, hoje):
    cat = CATEGORIAS[cat_id]
    queries = render_queries(cat["queries"], hoje)
    descricao = cat["descricao"]
    flag = cat["flag"]
    tier = cat["tier"]
    extras = cat.get("campos_extras", [])
    max_idade = cat.get("max_idade_dias", 7)

    todos_itens = []
    erro_msg = None

    extras_json_template = ""
    if extras:
        lines = ",\n      ".join(f'"{c}": "valor real ou vazio"' for c in extras)
        extras_json_template = f",\n      {lines}"

    queries_str = "\n".join(f"  {i+1}. {q}" for i, q in enumerate(queries))

    etapa_block = ""
    if cat_id in ("elim_ativas", "taf_fases", "recurso_anulacao", "radar_volume"):
        etapa_block = """,
      "etapa_concurso": "antes_prova" se concurso ainda NAO realizou prova objetiva, "apos_prova" se prova ja foi realizada"""

    data_limite = (hoje - timedelta(days=max_idade)).strftime("%d/%m/%Y")

    # Tier 3 (sentimento + concorrencia) usa prompt MAIS FLEXIVEL.
    # Conteudo de redes sociais/foruns nem sempre tem data clara ou link "verificavel"
    # no sentido formal - mas e a materia-prima de Reels e estrategia.
    if tier == 3:
        prompt = f"""Voce e um pesquisador de inteligencia de mercado do escritorio Silva Pinto Advocacia, especializado em concursos publicos.

CATEGORIA: {cat['label']} (Tier 3 - inteligencia de mercado)
OBJETIVO: {descricao}

== REGRAS PARA TIER 3 ==

Diferente dos outros tiers, aqui voce esta cacando conteudo de REDES SOCIAIS, FORUNS, BLOGS e CANAIS DE YOUTUBE. Esse conteudo nem sempre tem data exata ou URL super formal - ESTA OK.

REGRAS:
1. NUNCA INVENTE perfis, posts ou citacoes que nao apareceram nas buscas. Se a busca nao mostrou nada do perfil X, nao "preencha" inventando.
2. PREFIRA links reais (instagram.com/p/..., youtube.com/watch?v=..., reddit.com/r/...). Se a web_search retornou esse link, use.
3. Se nao tem link especifico do post mas voce viu a mencao na busca, use o link do PERFIL como fonte (ex: https://www.instagram.com/advogadodeconcurso/) e mencione na descricao "post recente" ou "video recente".
4. Datas exatas SAO BEM-VINDAS, mas se o post nao mostra data clara, escreva "recente" ou aproximacao ("ha cerca de 2 semanas").
5. Aceitar conteudo dos ultimos {max_idade} dias - se nao tiver certeza da data, considere recente se a busca classificou como tal.
6. PREFIRA poucos itens BEM concretos a muitos itens vagos. Trazer 1-2 posts especificos com citacao real e melhor que 5 genericos.
7. Se mesmo assim nao encontrar nada nas buscas, retorne {{"itens": []}}.

{FONTES_OFICIAIS}

== TAREFA ==

Realize as buscas a seguir, uma por vez, usando a ferramenta web_search:
{queries_str}

Foque em CONTEUDO RECENTE dos perfis-alvo. Se a busca mostrar:
- Reel/video do concorrente sobre concurso X = item valido
- Post do perfil do concorrente sobre tese juridica = item valido
- Discussao em forum/reddit com candidato desabafando sobre concurso = item valido
- Comentario em video do youtube com citacao de candidato = item valido

== FORMATO DE RESPOSTA ==

Retorne SOMENTE JSON puro, sem markdown, sem explicacoes.

{{
  "itens": [
    {{
      "titulo": "Titulo curto descrevendo o post/video/discussao",
      "descricao": "O que foi dito/postado, com citacao literal entre aspas se possivel",
      "data_publicacao": "DD/MM/AAAA ou aproximacao ('recente', 'ultima semana')",
      "orgao": "vazio (geralmente nao se aplica em tier 3)",
      "estado": "UF se relevante ou vazio",
      "concurso": "Concurso mencionado no post se houver",
      "cargo": "vazio",
      "banca": "vazio",
      "vagas": "vazio",
      "salario": "vazio",
      "prazo_inscricao": "vazio",
      "data_prova": "vazio",
      "fase_atual": "vazio",
      "link": "URL do post se houver, ou URL do perfil/canal",
      "relevancia": 1-10 (alto = ouro para conteudo proprio){extras_json_template}
    }}
  ]
}}"""
    else:
        # Prompt RIGIDO para Tier 1 e 2 (anti-invencao reforcado)
        prompt = f"""Voce e um pesquisador juridico do escritorio Silva Pinto Advocacia, especializado em concursos publicos.

CATEGORIA: {cat['label']} (Tier {tier})
OBJETIVO: {descricao}

== REGRAS CRITICAS - LEIA COM ATENCAO ==

1. NUNCA INVENTE conteudo. Se a busca nao retornou resultados reais, retorne {{"itens": []}}.
2. Cada item DEVE ter um LINK REAL E VERIFICAVEL retornado pela ferramenta web_search.
   Se voce nao tem o URL exato da fonte, NAO INCLUA o item.
3. NUNCA crie URLs ficticios. NUNCA escreva "https://example.com" ou similares.
   COPIE A URL EXATA retornada pela web_search - nao construa URLs por similaridade,
   nao "complete" URLs parciais, nao adivinhe slugs. Se a busca nao retornou URL
   especifica, NAO inclua o item.
4. RESTRICAO DE DATA RIGIDA: apenas conteudo publicado em {data_limite} ou DEPOIS.
   Se o conteudo nao tem data clara OU e mais antigo, NAO INCLUA.
5. Cada item deve ser INDIVIDUAL e VERIFICAVEL: titulo deve permitir busca rapida na fonte.
6. Se nada relevante for encontrado nas buscas, retorne {{"itens": []}} - isso e ACEITAVEL.
   E muito melhor retornar zero itens do que inventar.
7. APENAS NOTICIAS FACTUAIS DE EVENTOS ESPECIFICOS. NAO incluir:
   - Artigos doutrinarios ou de analise tematica (ex: "Uma analise jurisprudencial do TAF")
   - Posts de blog opinativos ou educacionais ("Como recorrer de eliminacao")
   - Material didatico ou de cursinho explicando conceitos juridicos
   - Resumos genericos sem evento concreto datado
   Apenas: "Banca X anulou a questao Y do concurso Z em data W", "TJ-MG concedeu
   liminar a candidato no concurso Y em DD/MM", etc.
8. NAO REPETIR ITENS. Se duas materias falam do mesmo evento (ex: dois portais
   noticiando o mesmo gabarito da PMERJ), inclua APENAS UMA - prefira a fonte mais
   oficial/relevante.

{FONTES_OFICIAIS}

== TAREFA ==

Realize as buscas a seguir, uma por vez, usando a ferramenta web_search:
{queries_str}

Apos as buscas, analise os RESULTADOS REAIS retornados e extraia ate 8 itens relevantes
e RECENTES (publicados a partir de {data_limite}).

== FORMATO DE RESPOSTA ==

Retorne SOMENTE JSON puro, sem markdown, sem explicacoes, sem texto antes ou depois.
NAO escreva "Aqui esta o JSON:" ou frases similares. Apenas o JSON.

{{
  "itens": [
    {{
      "titulo": "Titulo objetivo (REAL, da materia/decisao)",
      "descricao": "Resumo de 2-3 frases com dados concretos da fonte real",
      "data_publicacao": "DD/MM/AAAA da publicacao da materia (OBRIGATORIO)",
      "orgao": "Orgao/instituicao (PCMG, STJ, TJ-SP, etc) ou vazio",
      "estado": "UF (MG, SP, etc) ou Brasil se nacional",
      "concurso": "Nome do concurso (PMERJ 2026, etc) ou vazio",
      "cargo": "Cargo do concurso ou vazio",
      "banca": "Banca examinadora (FGV, Cebraspe, etc) ou vazio",
      "vagas": "Numero de vagas exato ou vazio",
      "salario": "Salario inicial em R$ exato ou vazio",
      "prazo_inscricao": "Data limite de inscricao ou vazio",
      "data_prova": "Data prevista da prova ou vazio",
      "fase_atual": "Fase em que esta (gabarito definitivo, TAF, recursos, etc) ou vazio",
      "link": "URL REAL E COMPLETO da fonte (OBRIGATORIO - sem isso, NAO inclua o item)",
      "relevancia": 1-10{extras_json_template}{etapa_block}
    }}
  ]
}}

REPETINDO AS REGRAS MAIS IMPORTANTES:
- Sem link real verificavel = NAO incluir
- Sem data clara dentro do periodo (>= {data_limite}) = NAO incluir
- Sem dados extraidos da web_search = retornar []
- Inventar = pior do que retornar []"""

    try:
        # Modelo varia por tier: Sonnet pro tier 1 (qualidade), Haiku pro 2/3 (rate limit folgado)
        modelo_categoria = MODEL_TIER1 if tier == 1 else MODEL_TIER23
        log.info("[%s tier%d] iniciando %d buscas (max %d dias) com %s",
                 cat_id, tier, len(queries), max_idade, modelo_categoria)
        client = anthropic.Anthropic(api_key=api_key, timeout=240.0, max_retries=2)
        msg = client.messages.create(
            model=modelo_categoria,
            max_tokens=12000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )

        # Coleta texto da resposta + URLs reais visitadas pela web_search
        text_parts = []
        web_searches_used = 0
        urls_citadas = set()  # URLs realmente vistas pelo web_search (anti-invencao)

        for block in msg.content:
            btype = getattr(block, "type", "")
            if btype == "server_tool_use" and getattr(block, "name", "") == "web_search":
                web_searches_used += 1
            elif btype == "web_search_tool_result":
                # Resultado da busca: lista de paginas. Captura todos URLs.
                content = getattr(block, "content", None)
                if isinstance(content, list):
                    for result in content:
                        url = getattr(result, "url", None)
                        if url:
                            urls_citadas.add(url)
            elif hasattr(block, "text") and block.text:
                text_parts.append(block.text)
                # Tambem extrai URLs de citacoes inline no texto (formato Anthropic)
                citations = getattr(block, "citations", None)
                if isinstance(citations, list):
                    for cit in citations:
                        url = getattr(cit, "url", None)
                        if url:
                            urls_citadas.add(url)
        raw = "".join(text_parts).strip()

        log.info("[%s] web_searches: %d, citacoes: %d, resposta texto: %d chars",
                 cat_id, web_searches_used, len(urls_citadas), len(raw))

        # Parse robusto
        data = parse_json_robusto(raw)
        if data is None:
            log.warning("[%s] JSON nao parseado. Preview: %s", cat_id, raw[:500])
            return [], f"JSON nao parseado (resposta: {len(raw)} chars)"

        itens = data.get("itens", [])
        log.info("[%s] %d itens extraidos do JSON", cat_id, len(itens))

        if not itens:
            log.info("[%s] IA retornou lista vazia (esperado se nada relevante)", cat_id)

        # Contadores de rejeicao pra log final
        rejeitados_dominio = 0
        rejeitados_nao_citado = 0
        rejeitados_artigo = 0
        rejeitados_outro = 0

        # Padroes que indicam artigo doutrinario / analise tematica em vez de noticia factual.
        # Aplicado APENAS no Tier 1 (eliminacoes ativas, fases, recurso) -- jurisprudencia
        # do Tier 2 pode aceitar essas palavras.
        PADROES_ARTIGO = [
            "analise jurisprudencial",
            "analise tematica",
            "uma analise",
            "estudo de caso",
            "como recorrer",
            "como funciona",
            "tudo sobre",
            "guia sobre",
            "guia completo",
            "entenda como",
            "o que e",
            "o que fazer",
            "saiba mais",
            "saiba como",
            "5 dicas",
            "passo a passo",
            "doutrina",
            "tese sobre",
            "reflexoes sobre",
        ]

        def _parece_artigo(texto):
            """Retorna True se o texto parece artigo doutrinario em vez de noticia factual."""
            import unicodedata
            t = unicodedata.normalize('NFKD', (texto or "").lower())
            t = "".join(c for c in t if not unicodedata.combining(c))
            for padrao in PADROES_ARTIGO:
                if padrao in t:
                    return padrao
            return None

        for item in itens:
            if not isinstance(item, dict):
                continue
            titulo = str(item.get("titulo", "")).strip()
            descricao = str(item.get("descricao", "")).strip()
            link = str(item.get("link", "")).strip()

            # Validacoes anti-invencao
            if not titulo:
                log.warning("[%s] item sem titulo, ignorando", cat_id)
                rejeitados_outro += 1
                continue

            # ANTI-ARTIGO: so para Tier 1 (Tier 2 jurisprudencia pode legitimamente trazer analises)
            if tier == 1:
                padrao_encontrado = _parece_artigo(titulo) or _parece_artigo(descricao[:200])
                if padrao_encontrado:
                    log.warning("[%s] tier1 parece artigo (padrao '%s'): %s", cat_id, padrao_encontrado, titulo[:60])
                    rejeitados_artigo += 1
                    continue

            # Tier 1 e 2: link obrigatorio + dominio whitelist + match com citacoes web_search
            # Tier 3: mais flexivel (aceita perfis/canais sem URL exato)
            if tier in (1, 2):
                if not link or not link.startswith("http"):
                    log.warning("[%s] item sem link real, ignorando: %s", cat_id, titulo[:60])
                    rejeitados_outro += 1
                    continue
                if "example.com" in link.lower() or "example.org" in link.lower():
                    log.warning("[%s] link example.com, ignorando: %s", cat_id, titulo[:60])
                    rejeitados_outro += 1
                    continue

                # WHITELIST DE DOMINIO: dominio precisa estar na lista permitida
                if not _dominio_permitido(link):
                    dom = _extrair_dominio(link)
                    log.warning("[%s] dominio fora whitelist (%s), ignorando: %s", cat_id, dom, titulo[:60])
                    rejeitados_dominio += 1
                    continue

                # MATCH COM CITACOES: o link DEVE bater com algo que web_search retornou
                # Isso elimina links inventados (alucinacoes do Claude)
                if urls_citadas and not _link_em_citacoes(link, urls_citadas):
                    log.warning("[%s] link nao bate com citacoes web_search (provavel alucinacao): %s",
                                cat_id, link[:120])
                    rejeitados_nao_citado += 1
                    continue
            else:
                # Tier 3: rejeitar APENAS links obviamente fakes
                if link and ("example.com" in link.lower() or "example.org" in link.lower()):
                    log.warning("[%s] tier3 link fake (example.com), ignorando: %s", cat_id, titulo[:60])
                    rejeitados_outro += 1
                    continue
                # Se tem link, valida dominio (mas nao exige match com citacoes)
                if link and not _dominio_permitido(link):
                    dom = _extrair_dominio(link)
                    log.warning("[%s] tier3 dominio fora whitelist (%s), ignorando: %s", cat_id, dom, titulo[:60])
                    rejeitados_dominio += 1
                    continue
                # Se nao tem link mas tem descricao concreta de pelo menos 50 chars, aceita
                desc = str(item.get("descricao", "")).strip()
                if not link and len(desc) < 50:
                    log.warning("[%s] tier3 item sem link e descricao curta (<50ch), ignorando: %s", cat_id, titulo[:60])
                    rejeitados_outro += 1
                    continue

            # Build extras dict
            extras_dict = {}
            for campo in extras:
                v = item.get(campo)
                if v:
                    extras_dict[campo] = str(v)[:300]

            etapa = str(item.get("etapa_concurso", "")).strip().lower()
            if etapa not in ("antes_prova", "apos_prova"):
                etapa = ""

            todos_itens.append({
                "categoria": cat_id,
                "tier": tier,
                "flag": flag,
                "titulo": titulo[:300],
                "descricao": str(item.get("descricao", "")).strip()[:1200],
                "orgao": str(item.get("orgao", "")).strip()[:100],
                "estado": str(item.get("estado", "")).strip()[:30],
                "concurso": str(item.get("concurso", "")).strip()[:120],
                "cargo": str(item.get("cargo", "")).strip()[:120],
                "banca": str(item.get("banca", "")).strip()[:60],
                "vagas": str(item.get("vagas", "")).strip()[:60],
                "salario": str(item.get("salario", "")).strip()[:60],
                "prazo_inscricao": str(item.get("prazo_inscricao", "")).strip()[:60],
                "data_prova": str(item.get("data_prova", "")).strip()[:60],
                "fase_atual": str(item.get("fase_atual", "")).strip()[:120],
                "data_publicacao": str(item.get("data_publicacao", "")).strip()[:30],
                "extras_json": json.dumps(extras_dict, ensure_ascii=False) if extras_dict else "",
                "link": link[:500],
                "relevancia": int(item.get("relevancia", 5)) if str(item.get("relevancia", 5)).isdigit() else 5,
                "etapa_concurso": etapa,
            })

        log.info("[%s] %d validados | rejeitados: dominio=%d, nao_citado=%d, artigo=%d, outros=%d",
                 cat_id, len(todos_itens), rejeitados_dominio, rejeitados_nao_citado, rejeitados_artigo, rejeitados_outro)

    except Exception as e:
        log.error("[%s] erro: %s\n%s", cat_id, e, traceback.format_exc())
        erro_msg = f"{type(e).__name__}: {str(e)[:200]}"

    return todos_itens, erro_msg


def _normalizar_nome_concurso(s):
    """Normaliza nome de concurso/orgao pra comparacao (lowercase, sem acento, sem ano)."""
    if not s:
        return ""
    import unicodedata
    s = unicodedata.normalize('NFKD', s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r'\b20[2-3]\d\b', ' ', s)       # remove anos
    s = re.sub(r'[^\w\s]', ' ', s)              # remove pontuacao
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


# Categorias do Tier 2/3 que sao TRANSVERSAIS (nao pertencem a um concurso especifico)
CATEGORIAS_TRANSVERSAIS = {
    "jurisprudencia": "jurisprudencia",
    "concorrencia": "concorrencia",
    "sentimento": "tendencia",
    "radar_volume": None,   # radar_volume PODE ser de um concurso (edital novo) - tratar como concurso
}


def _triar_item_por_palavras(item, concursos_monitorados):
    """Tenta encaixar um item num concurso monitorado SO por palavras-chave (sem IA).

    Retorna (concurso_id, confianca) onde confianca in ('alta','media',None).
    - alta: match forte (palavra-chave especifica bateu)
    - media: match fraco (so o orgao/nome generico bateu)
    - None: nao encaixou em nenhum
    """
    # Texto do item pra buscar (titulo + concurso + orgao)
    alvo = _normalizar_nome_concurso(
        f"{item.get('titulo','')} {item.get('concurso','')} {item.get('orgao','')}"
    )
    if not alvo:
        return (None, None)

    melhor_id = None
    melhor_conf = None
    for c in concursos_monitorados:
        # Palavras-chave do concurso
        kws = [k.strip() for k in (c.get("palavras_chave") or "").split(",") if k.strip()]
        nome_norm = _normalizar_nome_concurso(c.get("nome", ""))
        # Match por palavra-chave especifica = alta confianca
        for kw in kws:
            kw_norm = _normalizar_nome_concurso(kw)
            if kw_norm and len(kw_norm) >= 3 and kw_norm in alvo:
                return (c["id"], "alta")
        # Match pelo nome do concurso (primeiras 2-3 palavras) = media confianca
        nome_tokens = nome_norm.split()
        if nome_tokens:
            # tenta casar os tokens significativos do nome (ignora palavras curtas)
            sig = [t for t in nome_tokens if len(t) >= 4]
            if sig and all(t in alvo for t in sig[:2]):
                melhor_id = c["id"]
                melhor_conf = "media"
    return (melhor_id, melhor_conf)


def triar_itens(item_ids=None):
    """Roda triagem sobre itens do banco (por padrao, os de status 'pendente').

    Para cada item pendente:
      - Se a categoria e transversal (jurisprudencia/concorrencia/sentimento):
          marca status='transversal' + tipo_transversal
      - Senao, tenta encaixar num concurso monitorado por palavras-chave:
          * match alta confianca -> status='confirmado', concurso_id setado (auto-encaixe)
          * match media confianca -> status='pendente' + sugestao_concurso (vai pra fila)
          * sem match -> status='sem_concurso' + registra em sugestoes_concurso

    NOTA: por decisao do usuario, encaixes vao pra fila de confirmacao (status pendente
    com sugestao). Apenas matches de altissima confianca por palavra-chave exata sao
    auto-confirmados. Retorna estatisticas.
    """
    agora = datetime.now(timezone.utc).isoformat()
    stats = {"transversal": 0, "auto_confirmado": 0, "sugerido": 0, "sem_concurso": 0}

    with db_conn() as conn:
        # Carrega concursos monitorados ativos
        rows = conn.execute(
            "SELECT * FROM concursos_monitorados WHERE ativo = 1"
        ).fetchall()
        concursos = [_dict_from_row(r) for r in rows]

        # Seleciona itens a triar
        if item_ids:
            placeholders = ",".join(["?"] * len(item_ids))
            q = f"SELECT * FROM oportunidades WHERE id IN ({placeholders})"
            itrows = conn.execute(q, item_ids).fetchall()
        else:
            itrows = conn.execute(
                "SELECT * FROM oportunidades WHERE status_triagem = 'pendente' OR status_triagem IS NULL"
            ).fetchall()

        for r in itrows:
            item = _dict_from_row(r)
            cat = item.get("categoria", "")
            iid = item["id"]

            # 1. Transversal?
            if cat in CATEGORIAS_TRANSVERSAIS and CATEGORIAS_TRANSVERSAIS[cat] is not None:
                tt = CATEGORIAS_TRANSVERSAIS[cat]
                conn.execute(
                    "UPDATE oportunidades SET status_triagem='transversal', tipo_transversal=? WHERE id=?",
                    (tt, iid)
                )
                stats["transversal"] += 1
                continue

            # 2. Tenta encaixar por palavras-chave
            cid, conf = _triar_item_por_palavras(item, concursos)
            if cid and conf == "alta":
                conn.execute(
                    "UPDATE oportunidades SET status_triagem='confirmado', concurso_id=?, sugestao_concurso=NULL WHERE id=?",
                    (cid, iid)
                )
                stats["auto_confirmado"] += 1
            elif cid and conf == "media":
                # Vai pra fila de confirmacao com sugestao
                nome_sug = next((c["nome"] for c in concursos if c["id"] == cid), "")
                conn.execute(
                    "UPDATE oportunidades SET status_triagem='pendente', sugestao_concurso=? WHERE id=?",
                    (nome_sug, iid)
                )
                stats["sugerido"] += 1
            else:
                # Sem concurso: marca e registra como possivel sugestao de monitoramento
                conn.execute(
                    "UPDATE oportunidades SET status_triagem='sem_concurso' WHERE id=?",
                    (iid,)
                )
                stats["sem_concurso"] += 1
                _registrar_sugestao_concurso(conn, item, agora)

    log.info("v7 triagem: %s", stats)
    return stats


def _registrar_sugestao_concurso(conn, item, agora):
    """Registra/incrementa um concurso candidato a monitoramento (apareceu sem encaixe)."""
    nome_exib = (item.get("concurso") or item.get("orgao") or "").strip()
    if not nome_exib or len(nome_exib) < 3:
        return
    nome_norm = _normalizar_nome_concurso(nome_exib)
    if not nome_norm:
        return
    banca = (item.get("banca") or "").strip()
    evidencia = (item.get("titulo") or "")[:200]
    try:
        existing = conn.execute(
            "SELECT id, vezes FROM sugestoes_concurso WHERE nome_normalizado = ?",
            (nome_norm,)
        ).fetchone()
        if existing:
            d = _dict_from_row(existing)
            conn.execute(
                "UPDATE sugestoes_concurso SET vezes = vezes + 1, ultima_aparicao = ?, "
                "ultima_evidencia = ?, banca = COALESCE(NULLIF(banca,''), ?) WHERE id = ?",
                (agora, evidencia, banca, d["id"])
            )
        else:
            conn.execute(
                "INSERT INTO sugestoes_concurso "
                "(nome_normalizado, nome_exibicao, banca, vezes, ultima_evidencia, primeira_aparicao, ultima_aparicao, status) "
                "VALUES (?, ?, ?, 1, ?, ?, ?, 'pendente')",
                (nome_norm, nome_exib, banca, evidencia, agora, agora)
            )
    except Exception as e:
        log.warning("_registrar_sugestao_concurso erro: %s", e)


def salvar_itens(itens):
    """Salva itens novos no banco. Retorna lista de itens efetivamente inseridos (para notificacao).

    Bloqueia re-insercao de itens cujo hash esta na tabela `excluidos` (blocklist do usuario).
    """
    if not itens:
        return []
    inseridos = []
    ids_inseridos = []
    bloqueados_blocklist = 0
    agora = datetime.now(timezone.utc).isoformat()
    with db_conn() as conn:
        # Carrega blocklist uma vez no inicio (rapido, indexado por PK)
        blocklist = set()
        try:
            rows = conn.execute("SELECT hash_unico FROM excluidos").fetchall()
            for r in rows:
                d = _dict_from_row(r)
                if d.get("hash_unico"):
                    blocklist.add(d["hash_unico"])
        except Exception as e:
            log.warning("blocklist: nao foi possivel carregar (tabela ausente?): %s", e)

        for item in itens:
            h = hash_for_dedup(item["titulo"], item.get("orgao", ""), item.get("link", ""))

            # BLOCKLIST: se o usuario ja excluiu este item antes, nao insere de novo
            if h in blocklist:
                bloqueados_blocklist += 1
                log.info("blocklist: bloqueado re-insercao de '%s'", item["titulo"][:60])
                continue

            # Enriquecimento de metricas (YouTube/Reddit) - silencioso se nao configurado
            metricas = enrich_metricas(item.get("link", ""))
            metricas_json = json.dumps(metricas, ensure_ascii=False) if metricas else ""

            try:
                cur = conn.execute(
                    """INSERT INTO oportunidades
                    (categoria, tier, flag, titulo, descricao, orgao, estado,
                     concurso, cargo, banca, vagas, salario, prazo_inscricao,
                     data_prova, fase_atual, data_publicacao, extras_json,
                     link, relevancia, etapa_concurso, data_coleta, hash_unico,
                     metricas_json, status_triagem)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pendente')""",
                    (item["categoria"], item["tier"], item["flag"],
                     item["titulo"], item["descricao"], item["orgao"], item["estado"],
                     item["concurso"], item["cargo"], item["banca"], item["vagas"],
                     item["salario"], item["prazo_inscricao"], item["data_prova"],
                     item["fase_atual"], item.get("data_publicacao", ""),
                     item["extras_json"], item["link"],
                     item["relevancia"], item["etapa_concurso"], agora, h,
                     metricas_json)
                )
                # Captura o id inserido pra triagem posterior
                try:
                    novo_id = cur.lastrowid
                    if novo_id:
                        ids_inseridos.append(novo_id)
                except Exception:
                    pass
                inseridos.append(item)
            except sqlite3.IntegrityError:
                pass

    if bloqueados_blocklist:
        log.info("blocklist: %d item(ns) bloqueados nesta coleta", bloqueados_blocklist)

    # v7: roda triagem sobre os itens recem-inseridos (encaixe em concursos / transversal / sugestao)
    # Se capturamos os ids (SQLite local), tria so eles. Senao (Turso pode nao dar lastrowid),
    # tria todos os pendentes - cobre o caso de qualquer jeito.
    if inseridos:
        try:
            if ids_inseridos and len(ids_inseridos) == len(inseridos):
                triar_itens(item_ids=ids_inseridos)
            else:
                triar_itens(item_ids=None)  # tria todos os pendentes
        except Exception as e:
            log.warning("v7 triagem pos-insercao falhou: %s", e)

    return inseridos


def executar_coleta(api_key, categorias_a_rodar, tipo_run="manual"):
    """Executa coleta categoria por categoria com delay para respeitar rate limit.

    Rate limit Tier 1 da Anthropic: 30k input tokens/min.
    Cada categoria usa ~3-5k tokens de prompt + retries do web_search.
    Delay de 35s entre categorias garante folga.
    """
    import time
    inicio = datetime.now(timezone.utc)
    total_novos = 0
    erros = []
    todos_itens_novos = []  # acumula pra notificacao Discord

    DELAY_ENTRE_CATEGORIAS_SEG = 35

    for idx, cat_id in enumerate(categorias_a_rodar):
        if cat_id not in CATEGORIAS:
            continue

        if idx > 0:
            log.info("[throttle] aguardando %ds antes de %s", DELAY_ENTRE_CATEGORIAS_SEG, cat_id)
            time.sleep(DELAY_ENTRE_CATEGORIAS_SEG)

        tentativas = 0
        max_tentativas = 2
        while tentativas < max_tentativas:
            tentativas += 1
            try:
                itens, erro = coletar_categoria(api_key, cat_id, inicio)
                if erro and "429" in str(erro) and tentativas < max_tentativas:
                    log.warning("[%s] rate limit 429 - esperando 60s e tentando de novo", cat_id)
                    time.sleep(60)
                    continue
                inseridos = salvar_itens(itens)
                total_novos += len(inseridos)
                todos_itens_novos.extend(inseridos)
                log.info("[%s] %d novos salvos (de %d encontrados)", cat_id, len(inseridos), len(itens))
                if erro:
                    erros.append(f"{cat_id}: {erro}")
                break
            except Exception as e:
                if "429" in str(e) and tentativas < max_tentativas:
                    log.warning("[%s] excecao 429 - esperando 60s e tentando de novo", cat_id)
                    time.sleep(60)
                    continue
                erros.append(f"{cat_id}: {e}")
                log.error("[%s] falha total: %s", cat_id, e)
                break

    duracao = (datetime.now(timezone.utc) - inicio).total_seconds()
    sucesso = len(erros) == 0

    with db_conn() as conn:
        conn.execute(
            """INSERT INTO execucoes_cron
            (data_execucao, tipo_run, categorias_processadas, itens_novos, duracao_segundos, sucesso, erro)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (inicio.isoformat(), tipo_run, ",".join(categorias_a_rodar),
             total_novos, duracao, 1 if sucesso else 0,
             "; ".join(erros) if erros else None)
        )

    # Notificacao Discord: so se teve algo novo
    if todos_itens_novos:
        # Ordena por relevancia (maiores primeiro)
        todos_itens_novos.sort(key=lambda x: -int(x.get("relevancia", 5) or 5))
        notificar_discord(todos_itens_novos)

    return {
        "tipo_run": tipo_run,
        "sucesso": sucesso,
        "categorias": categorias_a_rodar,
        "itens_novos": total_novos,
        "duracao_segundos": round(duracao, 1),
        "erros": erros,
    }


def categorias_por_tier(*tiers):
    return [cid for cid, c in CATEGORIAS.items() if c["tier"] in tiers]


# Routes
@app.route("/")
def home():
    return Response(HTML_INDEX, mimetype="text/html")


@app.route("/logo.png")
def logo_png():
    """Serve o logo PNG embutido (decode do base64)."""
    import base64
    png_bytes = base64.b64decode(LOGO_B64)
    return Response(png_bytes, mimetype="image/png", headers={
        "Cache-Control": "public, max-age=86400"
    })


@app.route("/api/oportunidades")
def api_listar():
    categoria = request.args.get("categoria", "")
    tier = request.args.get("tier", "")
    flag = request.args.get("flag", "")
    estado = request.args.get("estado", "")
    etapa = request.args.get("etapa", "")
    incluir_lidos = request.args.get("incluir_lidos", "0") == "1"
    limite = int(request.args.get("limite", 200))
    dias = int(request.args.get("dias", 7))

    where_parts = ["arquivado = 0"]
    params = []
    if not incluir_lidos:
        where_parts.append("lido = 0")
    if categoria:
        where_parts.append("categoria = ?")
        params.append(categoria)
    if tier:
        where_parts.append("tier = ?")
        params.append(int(tier))
    if flag:
        where_parts.append("flag = ?")
        params.append(flag)
    if estado:
        where_parts.append("estado = ?")
        params.append(estado)
    if etapa:
        where_parts.append("etapa_concurso = ?")
        params.append(etapa)
    # v7: filtros de triagem
    concurso_id = request.args.get("concurso_id", "")
    status_triagem = request.args.get("status_triagem", "")
    tipo_transversal = request.args.get("tipo_transversal", "")
    if concurso_id:
        where_parts.append("concurso_id = ?")
        params.append(int(concurso_id))
    if status_triagem:
        where_parts.append("status_triagem = ?")
        params.append(status_triagem)
    if tipo_transversal:
        where_parts.append("tipo_transversal = ?")
        params.append(tipo_transversal)
    if dias > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
        where_parts.append("data_coleta >= ?")
        params.append(cutoff)

    where_sql = " AND ".join(where_parts)
    sql = f"""SELECT * FROM oportunidades
              WHERE {where_sql}
              ORDER BY tier ASC, data_coleta DESC, relevancia DESC
              LIMIT ?"""
    params.append(limite)

    with db_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

        # Pega contagem de notas por oportunidade em um query so
        notas_count = {}
        if rows:
            ids = [_dict_from_row(r)["id"] for r in rows]
            placeholders = ",".join(["?"] * len(ids))
            counts = conn.execute(
                f"SELECT oportunidade_id, COUNT(*) AS c FROM notas WHERE oportunidade_id IN ({placeholders}) GROUP BY oportunidade_id",
                ids
            ).fetchall()
            for c in counts:
                d = _dict_from_row(c)
                notas_count[d["oportunidade_id"]] = d["c"]

        # Pega data da ULTIMA execucao de cron por tipo - usado pra marcar itens "novos"
        # Estrategia: para cada tipo_run (manual/tier1/tier23), pega o data_execucao da ULTIMA
        # execucao bem-sucedida. Itens cuja data_coleta seja >= esse valor sao "novos".
        ultima_exec_por_tipo = {}
        try:
            execs = conn.execute(
                "SELECT tipo_run, MAX(data_execucao) AS ult FROM execucoes_cron "
                "WHERE sucesso = 1 GROUP BY tipo_run"
            ).fetchall()
            for e in execs:
                d = _dict_from_row(e)
                if d.get("tipo_run") and d.get("ult"):
                    ultima_exec_por_tipo[d["tipo_run"]] = d["ult"]
        except Exception:
            pass

    # Marcador de "novo": item e novo se foi coletado na ultima execucao
    # Estrategia simples: para cada tier, pega o cutoff da ultima execucao que cobre
    # esse tier (manual cobre tudo; tier1 cobre Tier 1; tier23 cobre Tier 2+3).
    cutoff_tier1 = max(
        ultima_exec_por_tipo.get("tier1", ""),
        ultima_exec_por_tipo.get("manual", ""),
    )
    cutoff_tier23 = max(
        ultima_exec_por_tipo.get("tier23", ""),
        ultima_exec_por_tipo.get("manual", ""),
    )

    itens = []
    for r in rows:
        d = _dict_from_row(r)
        if d.get("extras_json"):
            try:
                d["extras"] = json.loads(d["extras_json"])
            except:
                d["extras"] = {}
        else:
            d["extras"] = {}
        if d.get("metricas_json"):
            try:
                d["metricas"] = json.loads(d["metricas_json"])
            except:
                d["metricas"] = None
        else:
            d["metricas"] = None
        d["notas_count"] = notas_count.get(d["id"], 0)

        # Marcador eh_novo: comparar data_coleta com cutoff da ultima execucao do tier
        cutoff_aplicavel = cutoff_tier1 if d.get("tier") == 1 else cutoff_tier23
        data_col = d.get("data_coleta", "")
        d["eh_novo"] = bool(cutoff_aplicavel and data_col and data_col >= cutoff_aplicavel)

        itens.append(d)

    # DEFESA EM PROFUNDIDADE: dedupe no momento da exibicao.
    # Mesmo que o banco tenha duplicados (legacy), o frontend nao vai ver dois cards
    # da mesma noticia. Agrupa por hash recalculado (link normalizado ou titulo+orgao).
    seen_hashes = set()
    itens_unicos = []
    for it in itens:
        h = hash_for_dedup(it.get("titulo") or "", it.get("orgao") or "", it.get("link") or "")
        if h in seen_hashes:
            continue
        seen_hashes.add(h)
        itens_unicos.append(it)

    return jsonify({"total": len(itens_unicos), "itens": itens_unicos})


@app.route("/api/oportunidades/<int:item_id>/marcar_lido", methods=["POST"])
def api_marcar_lido(item_id):
    """Marca como lido (item continua no Turso, so fica oculto por padrao)."""
    with db_conn() as conn:
        conn.execute("UPDATE oportunidades SET lido = 1 WHERE id = ?", (item_id,))
    return jsonify({"ok": True})


@app.route("/api/oportunidades/<int:item_id>/marcar_selecionado", methods=["POST"])
def api_marcar_selecionado(item_id):
    """Marca o item como ja enviado ao sistema comercial (botao Selecionar).

    Apos o frontend conseguir o POST 200 no sistema comercial, chama este
    endpoint para persistir o estado. A proxima vez que o item carregar
    no painel, o botao ja vira desabilitado e em verde.
    """
    try:
        with db_conn() as conn:
            conn.execute(
                "UPDATE oportunidades SET selecionado_marketing = 1 WHERE id = ?",
                (item_id,)
            )
        return jsonify({"ok": True})
    except Exception as e:
        log.error("Erro ao marcar selecionado %d: %s", item_id, e)
        return jsonify({"erro": str(e)}), 500


@app.route("/api/oportunidades/<int:item_id>", methods=["DELETE"])
def api_excluir(item_id):
    """EXCLUI permanentemente do Turso E adiciona o hash a lista de bloqueio.

    Apaga o item da tabela oportunidades. As notas associadas tambem somem.
    O hash do item e registrado na tabela `excluidos` para evitar que o mesmo
    item seja re-inserido em coletas futuras (o Claude pode achar a mesma
    noticia em uma busca posterior).
    """
    try:
        from datetime import datetime, timezone
        agora = datetime.now(timezone.utc).isoformat()
        with db_conn() as conn:
            # Captura titulo, link e hash_unico antes de deletar
            row = conn.execute(
                "SELECT titulo, link, hash_unico FROM oportunidades WHERE id = ?",
                (item_id,)
            ).fetchone()

            if row:
                d = _dict_from_row(row)
                titulo = d.get("titulo") or ""
                link = d.get("link") or ""
                hash_atual = d.get("hash_unico") or ""

                # Calcula o hash com a logica ATUAL (caso o legacy seja diferente)
                hash_novo = hash_for_dedup(titulo, "", link)

                # Insere ambos os hashes (o atual e o recalculado) na blocklist.
                # Garante que mesmo que a logica de hash mude no futuro, o item nao volte.
                for h in {hash_atual, hash_novo}:
                    if h:
                        try:
                            conn.execute(
                                "INSERT OR IGNORE INTO excluidos (hash_unico, titulo, link, data_exclusao) VALUES (?, ?, ?, ?)",
                                (h, titulo[:200], link[:500], agora)
                            )
                        except Exception as e:
                            log.warning("blocklist: nao consegui inserir hash %s: %s", h, e)

            # Apaga as notas antes (caso o ON DELETE CASCADE nao seja aplicado pelo Turso)
            conn.execute("DELETE FROM notas WHERE oportunidade_id = ?", (item_id,))
            conn.execute("DELETE FROM oportunidades WHERE id = ?", (item_id,))

        log.info("Item %d excluido + hash adicionado a blocklist", item_id)
        return jsonify({"ok": True, "excluido": item_id})
    except Exception as e:
        log.error("Erro ao excluir item %d: %s", item_id, e)
        return jsonify({"erro": str(e)}), 500


@app.route("/api/blocklist", methods=["GET"])
def api_blocklist_listar():
    """Lista os hashes na blocklist (itens que o usuario excluiu)."""
    try:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT hash_unico, titulo, link, data_exclusao FROM excluidos ORDER BY data_exclusao DESC LIMIT 500"
            ).fetchall()
        itens = [_dict_from_row(r) for r in rows]
        return jsonify({"total": len(itens), "itens": itens})
    except Exception as e:
        return jsonify({"erro": str(e), "total": 0, "itens": []}), 500


@app.route("/api/blocklist/<path:hash_unico>", methods=["DELETE"])
def api_blocklist_remover(hash_unico):
    """Remove um hash da blocklist. Caso o usuario queira voltar a receber esse item."""
    try:
        with db_conn() as conn:
            conn.execute("DELETE FROM excluidos WHERE hash_unico = ?", (hash_unico,))
        return jsonify({"ok": True, "hash_removido": hash_unico})
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


# ===== v7: CONCURSOS MONITORADOS (CRUD) =====

PRIORIDADES_VALIDAS = ("urgente", "importante", "naourgente")


@app.route("/api/concursos", methods=["GET"])
def api_concursos_listar():
    """Lista concursos monitorados com contagem de noticias encaixadas em cada um."""
    try:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM concursos_monitorados WHERE ativo = 1 ORDER BY id DESC"
            ).fetchall()
            concursos = [_dict_from_row(r) for r in rows]
            # Conta noticias confirmadas por concurso
            for c in concursos:
                cnt = conn.execute(
                    "SELECT COUNT(*) AS n FROM oportunidades WHERE concurso_id = ? AND status_triagem = 'confirmado'",
                    (c["id"],)
                ).fetchone()
                c["noticias_count"] = _dict_from_row(cnt)["n"] if cnt else 0
        return jsonify({"total": len(concursos), "concursos": concursos})
    except Exception as e:
        log.error("api_concursos_listar erro: %s", e)
        return jsonify({"erro": str(e), "total": 0, "concursos": []}), 500


@app.route("/api/concursos", methods=["POST"])
def api_concursos_criar():
    """Cria um novo concurso monitorado.

    Body JSON: { nome, banca?, palavras_chave?, prioridade?, vagas? }
    """
    try:
        data = request.get_json(force=True) or {}
        nome = str(data.get("nome", "")).strip()
        if not nome:
            return jsonify({"erro": "nome obrigatorio"}), 400
        banca = str(data.get("banca", "")).strip()
        palavras = str(data.get("palavras_chave", "")).strip()
        prioridade = str(data.get("prioridade", "importante")).strip().lower()
        if prioridade not in PRIORIDADES_VALIDAS:
            prioridade = "importante"
        vagas = str(data.get("vagas", "")).strip()
        agora = datetime.now(timezone.utc).isoformat()

        with db_conn() as conn:
            conn.execute(
                """INSERT INTO concursos_monitorados
                   (nome, banca, palavras_chave, prioridade, vagas, data_criacao, ativo)
                   VALUES (?, ?, ?, ?, ?, ?, 1)""",
                (nome, banca, palavras, prioridade, vagas, agora)
            )
            row = conn.execute(
                "SELECT * FROM concursos_monitorados WHERE nome = ? ORDER BY id DESC LIMIT 1",
                (nome,)
            ).fetchone()
        novo = _dict_from_row(row) if row else {"nome": nome}
        log.info("v7: concurso monitorado criado: %s", nome)
        return jsonify({"ok": True, "concurso": novo})
    except Exception as e:
        log.error("api_concursos_criar erro: %s", e)
        return jsonify({"erro": str(e)}), 500


@app.route("/api/concursos/<int:concurso_id>", methods=["PATCH"])
def api_concursos_editar(concurso_id):
    """Edita campos de um concurso monitorado (nome, banca, palavras_chave, prioridade, vagas)."""
    try:
        data = request.get_json(force=True) or {}
        campos = []
        valores = []
        for campo in ("nome", "banca", "palavras_chave", "vagas"):
            if campo in data:
                campos.append(f"{campo} = ?")
                valores.append(str(data[campo]).strip())
        if "prioridade" in data:
            p = str(data["prioridade"]).strip().lower()
            if p in PRIORIDADES_VALIDAS:
                campos.append("prioridade = ?")
                valores.append(p)
        if not campos:
            return jsonify({"erro": "nada para atualizar"}), 400
        valores.append(concurso_id)
        with db_conn() as conn:
            conn.execute(
                f"UPDATE concursos_monitorados SET {', '.join(campos)} WHERE id = ?",
                valores
            )
        return jsonify({"ok": True, "id": concurso_id})
    except Exception as e:
        log.error("api_concursos_editar erro: %s", e)
        return jsonify({"erro": str(e)}), 500


@app.route("/api/concursos/<int:concurso_id>", methods=["DELETE"])
def api_concursos_excluir(concurso_id):
    """Para de monitorar um concurso (soft delete: ativo=0).

    As noticias que estavam encaixadas voltam a status 'sem_concurso' (nao somem).
    """
    try:
        with db_conn() as conn:
            conn.execute("UPDATE concursos_monitorados SET ativo = 0 WHERE id = ?", (concurso_id,))
            conn.execute(
                "UPDATE oportunidades SET concurso_id = NULL, status_triagem = 'sem_concurso' "
                "WHERE concurso_id = ?",
                (concurso_id,)
            )
        log.info("v7: concurso %d desmonitorado", concurso_id)
        return jsonify({"ok": True, "id": concurso_id})
    except Exception as e:
        log.error("api_concursos_excluir erro: %s", e)
        return jsonify({"erro": str(e)}), 500


@app.route("/api/concursos/<int:concurso_id>/prioridade", methods=["POST"])
def api_concursos_prioridade(concurso_id):
    """Altera so a prioridade (semaforo). Body: { prioridade: 'urgente'|'importante'|'naourgente' }"""
    try:
        data = request.get_json(force=True) or {}
        p = str(data.get("prioridade", "")).strip().lower()
        if p not in PRIORIDADES_VALIDAS:
            return jsonify({"erro": "prioridade invalida"}), 400
        with db_conn() as conn:
            conn.execute("UPDATE concursos_monitorados SET prioridade = ? WHERE id = ?", (p, concurso_id))
        return jsonify({"ok": True, "id": concurso_id, "prioridade": p})
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


# ===== v7: CAIXA DE ENTRADA (triagem) =====

@app.route("/api/inbox", methods=["GET"])
def api_inbox():
    """Retorna a caixa de entrada: encaixes a confirmar + sugestoes de novos concursos."""
    try:
        with db_conn() as conn:
            # Encaixes pendentes (a IA sugeriu um concurso, usuario confirma)
            erows = conn.execute(
                "SELECT id, titulo, descricao, sugestao_concurso, orgao, banca "
                "FROM oportunidades WHERE status_triagem = 'pendente' AND sugestao_concurso IS NOT NULL "
                "ORDER BY data_coleta DESC LIMIT 100"
            ).fetchall()
            encaixes = [_dict_from_row(r) for r in erows]

            # Sugestoes de novos concursos (apareceram >= 2x, ainda nao monitorados)
            srows = conn.execute(
                "SELECT id, nome_exibicao, banca, vezes, ultima_evidencia "
                "FROM sugestoes_concurso WHERE status = 'pendente' AND vezes >= 2 "
                "ORDER BY vezes DESC LIMIT 50"
            ).fetchall()
            novos = [_dict_from_row(r) for r in srows]

        return jsonify({
            "encaixes": encaixes,
            "novos": novos,
            "total": len(encaixes) + len(novos),
        })
    except Exception as e:
        log.error("api_inbox erro: %s", e)
        return jsonify({"erro": str(e), "encaixes": [], "novos": [], "total": 0}), 500


@app.route("/api/inbox/<int:item_id>/confirmar", methods=["POST"])
def api_inbox_confirmar(item_id):
    """Confirma o encaixe sugerido: a noticia entra na ficha do concurso.

    Body opcional: { concurso_id } pra encaixar num concurso especifico (botao 'Outro concurso').
    Se nao vier concurso_id, usa a sugestao (busca o concurso pelo nome sugerido).
    """
    try:
        data = request.get_json(silent=True) or {}
        concurso_id = data.get("concurso_id")
        with db_conn() as conn:
            if not concurso_id:
                # Resolve pela sugestao
                row = conn.execute(
                    "SELECT sugestao_concurso FROM oportunidades WHERE id = ?", (item_id,)
                ).fetchone()
                sug = _dict_from_row(row).get("sugestao_concurso") if row else None
                if sug:
                    crow = conn.execute(
                        "SELECT id FROM concursos_monitorados WHERE nome = ? AND ativo = 1 LIMIT 1",
                        (sug,)
                    ).fetchone()
                    if crow:
                        concurso_id = _dict_from_row(crow)["id"]
            if not concurso_id:
                return jsonify({"erro": "concurso nao resolvido"}), 400
            conn.execute(
                "UPDATE oportunidades SET status_triagem='confirmado', concurso_id=?, sugestao_concurso=NULL WHERE id=?",
                (concurso_id, item_id)
            )
        return jsonify({"ok": True, "item_id": item_id, "concurso_id": concurso_id})
    except Exception as e:
        log.error("api_inbox_confirmar erro: %s", e)
        return jsonify({"erro": str(e)}), 500


@app.route("/api/inbox/<int:item_id>/criar-e-encaixar", methods=["POST"])
def api_inbox_criar_encaixar(item_id):
    """Cria um concurso novo na hora e encaixa esta noticia nele.

    Body: { nome, banca?, palavras_chave?, prioridade? }
    """
    try:
        data = request.get_json(force=True) or {}
        nome = str(data.get("nome", "")).strip()
        if not nome:
            return jsonify({"erro": "nome obrigatorio"}), 400
        banca = str(data.get("banca", "")).strip()
        palavras = str(data.get("palavras_chave", "")).strip()
        prioridade = str(data.get("prioridade", "importante")).strip().lower()
        if prioridade not in PRIORIDADES_VALIDAS:
            prioridade = "importante"
        agora = datetime.now(timezone.utc).isoformat()
        with db_conn() as conn:
            conn.execute(
                "INSERT INTO concursos_monitorados (nome, banca, palavras_chave, prioridade, data_criacao, ativo) "
                "VALUES (?, ?, ?, ?, ?, 1)",
                (nome, banca, palavras, prioridade, agora)
            )
            crow = conn.execute(
                "SELECT id FROM concursos_monitorados WHERE nome = ? ORDER BY id DESC LIMIT 1", (nome,)
            ).fetchone()
            cid = _dict_from_row(crow)["id"]
            conn.execute(
                "UPDATE oportunidades SET status_triagem='confirmado', concurso_id=?, sugestao_concurso=NULL WHERE id=?",
                (cid, item_id)
            )
        log.info("v7: concurso '%s' criado e item %d encaixado", nome, item_id)
        return jsonify({"ok": True, "concurso_id": cid, "item_id": item_id})
    except Exception as e:
        log.error("api_inbox_criar_encaixar erro: %s", e)
        return jsonify({"erro": str(e)}), 500


@app.route("/api/inbox/<int:item_id>/descartar", methods=["POST"])
def api_inbox_descartar(item_id):
    """Remove da fila sem encaixar. Marca como sem_concurso (some da caixa de entrada)."""
    try:
        with db_conn() as conn:
            conn.execute(
                "UPDATE oportunidades SET status_triagem='sem_concurso', sugestao_concurso=NULL WHERE id=?",
                (item_id,)
            )
        return jsonify({"ok": True, "item_id": item_id})
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


@app.route("/api/sugestoes/<int:sug_id>/monitorar", methods=["POST"])
def api_sugestao_monitorar(sug_id):
    """Transforma uma sugestao de concurso em concurso monitorado.

    Encaixa retroativamente as noticias 'sem_concurso' que casam com essa sugestao.
    """
    try:
        agora = datetime.now(timezone.utc).isoformat()
        with db_conn() as conn:
            srow = conn.execute("SELECT * FROM sugestoes_concurso WHERE id = ?", (sug_id,)).fetchone()
            if not srow:
                return jsonify({"erro": "sugestao nao encontrada"}), 404
            s = _dict_from_row(srow)
            nome = s["nome_exibicao"]
            banca = s.get("banca", "")
            # Cria o concurso monitorado
            conn.execute(
                "INSERT INTO concursos_monitorados (nome, banca, palavras_chave, prioridade, data_criacao, ativo) "
                "VALUES (?, ?, ?, 'importante', ?, 1)",
                (nome, banca, nome, agora)  # usa o proprio nome como palavra-chave inicial
            )
            crow = conn.execute(
                "SELECT id FROM concursos_monitorados WHERE nome = ? ORDER BY id DESC LIMIT 1", (nome,)
            ).fetchone()
            cid = _dict_from_row(crow)["id"]
            # Marca a sugestao como aceita
            conn.execute("UPDATE sugestoes_concurso SET status='monitorado' WHERE id=?", (sug_id,))
        # Re-tria os itens sem_concurso (alguns vao encaixar agora)
        try:
            with db_conn() as conn:
                conn.execute(
                    "UPDATE oportunidades SET status_triagem='pendente' WHERE status_triagem='sem_concurso'"
                )
            triar_itens(item_ids=None)
        except Exception as e:
            log.warning("re-triagem pos-monitorar falhou: %s", e)
        log.info("v7: sugestao %d virou concurso monitorado '%s'", sug_id, nome)
        return jsonify({"ok": True, "concurso_id": cid, "nome": nome})
    except Exception as e:
        log.error("api_sugestao_monitorar erro: %s", e)
        return jsonify({"erro": str(e)}), 500


@app.route("/api/sugestoes/<int:sug_id>/ignorar", methods=["POST"])
def api_sugestao_ignorar(sug_id):
    """Ignora uma sugestao de concurso (nao aparece mais na caixa de entrada)."""
    try:
        with db_conn() as conn:
            conn.execute("UPDATE sugestoes_concurso SET status='ignorado' WHERE id=?", (sug_id,))
        return jsonify({"ok": True, "id": sug_id})
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


@app.route("/api/triagem/retroativa", methods=["POST"])
def api_triagem_retroativa():
    """Roda a triagem uma vez sobre TODO o acervo antigo (itens sem status definido).

    Usado na migracao pra v7: as noticias antigas foram coletadas sem o conceito de
    concurso monitorado. Isso tenta encaixa-las nos concursos cadastrados.
    """
    try:
        with db_conn() as conn:
            # Reseta pra pendente os itens que ainda nao passaram por triagem real
            conn.execute(
                "UPDATE oportunidades SET status_triagem='pendente' "
                "WHERE status_triagem IS NULL OR status_triagem = ''"
            )
        stats = triar_itens(item_ids=None)
        return jsonify({"ok": True, "stats": stats})
    except Exception as e:
        log.error("api_triagem_retroativa erro: %s", e)
        return jsonify({"erro": str(e)}), 500


@app.route("/api/oportunidades/<int:item_id>/notas", methods=["GET"])
def api_listar_notas(item_id):
    """Lista todas as notas de uma oportunidade, mais recentes primeiro."""
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM notas WHERE oportunidade_id = ? ORDER BY id DESC",
            (item_id,)
        ).fetchall()
    notas = [_dict_from_row(r) for r in rows]
    return jsonify({"total": len(notas), "notas": notas})


@app.route("/api/oportunidades/<int:item_id>/notas", methods=["POST"])
def api_adicionar_nota(item_id):
    """Cria uma nova nota. Body JSON: {texto: '...'}. Maximo 500 caracteres."""
    data = request.get_json(silent=True) or {}
    texto = (data.get("texto") or "").strip()
    if not texto:
        return jsonify({"erro": "texto vazio"}), 400
    if len(texto) > 500:
        texto = texto[:500]

    agora = datetime.now(timezone.utc).isoformat()
    with db_conn() as conn:
        # Verifica se a oportunidade existe
        ex = conn.execute(
            "SELECT id FROM oportunidades WHERE id = ?", (item_id,)
        ).fetchone()
        if not ex:
            return jsonify({"erro": "oportunidade nao encontrada"}), 404
        conn.execute(
            "INSERT INTO notas (oportunidade_id, texto, data_criacao) VALUES (?, ?, ?)",
            (item_id, texto, agora)
        )
    return jsonify({"ok": True, "data_criacao": agora})


@app.route("/api/notas/<int:nota_id>", methods=["DELETE"])
def api_deletar_nota(nota_id):
    """Apaga uma nota especifica (botao lixeira ao lado de cada nota)."""
    with db_conn() as conn:
        conn.execute("DELETE FROM notas WHERE id = ?", (nota_id,))
    return jsonify({"ok": True})


@app.route("/api/limpar_exemplos", methods=["POST"])
def api_limpar_exemplos():
    """Apaga itens [EXEMPLO] do banco. Usado pra limpar antes da primeira coleta real."""
    with db_conn() as conn:
        conn.execute("DELETE FROM oportunidades WHERE titulo LIKE '%[EXEMPLO]%'")
        c = conn.execute("SELECT changes()").fetchone()[0]
    return jsonify({"ok": True, "removidos": c})


@app.route("/api/dedupe", methods=["POST"])
def api_dedupe():
    """Roda dedupe retroativa no banco inteiro.

    Estrategia:
    1. Recalcula hash_unico de TODOS os registros com a logica atual de hash_for_dedup
    2. Agrupa por novo_hash
    3. Para cada grupo com mais de 1 registro, mantem o ID MENOR (mais antigo) e
       deleta os demais (junto com suas notas)
    4. Atualiza hash_unico de todos os mantidos pro novo valor

    Retorna estatisticas: total antes, duplicados achados, deletados, total apos.
    """
    log.info("=== DEDUPE retroativo iniciado ===")
    with db_conn() as conn:
        # 1. Coleta todos os registros
        rows = conn.execute(
            "SELECT id, titulo, orgao, link, hash_unico, data_coleta FROM oportunidades ORDER BY id ASC"
        ).fetchall()
        total_antes = len(rows)
        log.info("dedupe: %d registros no banco", total_antes)

        # 2. Recalcula hash e agrupa
        grupos = {}  # novo_hash -> [ids ordenados]
        for r in rows:
            d = _dict_from_row(r)
            novo = hash_for_dedup(d.get("titulo") or "", d.get("orgao") or "", d.get("link") or "")
            grupos.setdefault(novo, []).append(d["id"])

        # 3. Identifica duplicados (grupos com >1)
        ids_para_deletar = []
        ids_para_atualizar_hash = {}  # id -> novo_hash
        for novo_hash, ids in grupos.items():
            ids.sort()  # menor primeiro = mais antigo
            mantido = ids[0]
            ids_para_atualizar_hash[mantido] = novo_hash
            for outro in ids[1:]:
                ids_para_deletar.append(outro)

        log.info("dedupe: %d grupos, %d serao deletados", len(grupos), len(ids_para_deletar))

        # 4. Deleta (notas via DELETE explicito porque CASCADE pode nao estar habilitado)
        if ids_para_deletar:
            for chunk_start in range(0, len(ids_para_deletar), 100):
                chunk = ids_para_deletar[chunk_start:chunk_start + 100]
                placeholders = ",".join(["?"] * len(chunk))
                conn.execute(f"DELETE FROM notas WHERE oportunidade_id IN ({placeholders})", chunk)
                conn.execute(f"DELETE FROM oportunidades WHERE id IN ({placeholders})", chunk)

        # 5. Atualiza hash_unico dos mantidos (caso o atual difira do novo)
        # Faz so dos que de fato precisam mudar - evita writes desnecessarios
        atualizados = 0
        for r in rows:
            d = _dict_from_row(r)
            if d["id"] in ids_para_atualizar_hash:
                novo = ids_para_atualizar_hash[d["id"]]
                atual = d.get("hash_unico") or ""
                if novo != atual:
                    try:
                        conn.execute(
                            "UPDATE oportunidades SET hash_unico = ? WHERE id = ?",
                            (novo, d["id"])
                        )
                        atualizados += 1
                    except Exception as e:
                        # Pode dar erro de UNIQUE constraint se 2 ids chegarem com mesmo hash
                        # antes do delete completar - log e segue
                        log.warning("dedupe: nao consegui atualizar hash do id=%d: %s", d["id"], e)

        total_apos = conn.execute("SELECT COUNT(*) AS c FROM oportunidades").fetchone()["c"]

    log.info("=== DEDUPE concluido: %d -> %d (deletados %d, hashes atualizados %d) ===",
             total_antes, total_apos, len(ids_para_deletar), atualizados)

    return jsonify({
        "ok": True,
        "total_antes": total_antes,
        "total_apos": total_apos,
        "duplicados_deletados": len(ids_para_deletar),
        "hashes_atualizados": atualizados,
    })


@app.route("/api/status")
def api_status():
    with db_conn() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM oportunidades").fetchone()["c"]
        nao_lidos = conn.execute(
            "SELECT COUNT(*) AS c FROM oportunidades WHERE lido = 0 AND arquivado = 0"
        ).fetchone()["c"]
        ultima = conn.execute(
            "SELECT * FROM execucoes_cron ORDER BY id DESC LIMIT 1"
        ).fetchone()
        tier_counts = {}
        for t in (1, 2, 3):
            r = conn.execute(
                "SELECT COUNT(*) AS c FROM oportunidades WHERE lido = 0 AND arquivado = 0 AND tier = ?",
                (t,)
            ).fetchone()
            tier_counts[f"tier{t}"] = r["c"]
        # Tamanho da blocklist (itens excluidos pelo usuario, que nunca mais devem voltar)
        try:
            blocklist_size = conn.execute("SELECT COUNT(*) AS c FROM excluidos").fetchone()["c"]
        except Exception:
            blocklist_size = 0

    return jsonify({
        "total": total,
        "nao_lidos": nao_lidos,
        "tier_counts": tier_counts,
        "blocklist_size": blocklist_size,
        "ultima_execucao": _dict_from_row(ultima) if ultima else None,
        "model_tier1": MODEL_TIER1,
        "model_tier23": MODEL_TIER23,
        "version": APP_VERSION,
    })


def _executar_coleta_background(api_key, categorias, tipo_run):
    """Roda executar_coleta em uma thread de background. Pra crons fire-and-forget.

    Permite que o endpoint responda imediatamente com 200 OK (~30 bytes), evitando
    timeout do cron-job.org (30s) e erro de 'saida muito grande'.
    """
    import threading

    def _run():
        try:
            executar_coleta(api_key, categorias, tipo_run=tipo_run)
        except Exception as e:
            log.error("Coleta background (%s) falhou: %s", tipo_run, e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()


@app.route("/cron/tier1", methods=["GET", "POST"])
def cron_tier1():
    if CRON_SECRET:
        secret = request.args.get("secret") or request.headers.get("X-Cron-Secret")
        if secret != CRON_SECRET:
            return "forbidden", 403, {"Content-Type": "text/plain"}
    if not ANTHROPIC_API_KEY:
        return "no api key", 500, {"Content-Type": "text/plain"}

    log.info("=" * 60)
    log.info("CRON TIER 1 disparado em %s (background)", datetime.now(timezone.utc).isoformat())
    log.info("=" * 60)

    cats = categorias_por_tier(1)
    _executar_coleta_background(ANTHROPIC_API_KEY, cats, tipo_run="tier1")
    # Resposta MINIMA em texto puro - 2 bytes - evita "saida muito grande" do cron-job.org
    return "OK", 200, {"Content-Type": "text/plain"}


@app.route("/cron/tier23", methods=["GET", "POST"])
def cron_tier23():
    if CRON_SECRET:
        secret = request.args.get("secret") or request.headers.get("X-Cron-Secret")
        if secret != CRON_SECRET:
            return "forbidden", 403, {"Content-Type": "text/plain"}
    if not ANTHROPIC_API_KEY:
        return "no api key", 500, {"Content-Type": "text/plain"}

    log.info("=" * 60)
    log.info("CRON TIER 2+3 disparado em %s (background)", datetime.now(timezone.utc).isoformat())
    log.info("=" * 60)

    cats = categorias_por_tier(2, 3)
    _executar_coleta_background(ANTHROPIC_API_KEY, cats, tipo_run="tier23")
    return "OK", 200, {"Content-Type": "text/plain"}


@app.route("/cron/manual", methods=["POST"])
def cron_manual():
    if not ANTHROPIC_API_KEY:
        return jsonify({"erro": "ANTHROPIC_API_KEY nao configurada"}), 500

    tipo = request.args.get("tipo", "completo")
    if tipo == "tier1":
        cats = categorias_por_tier(1)
    elif tipo == "tier23":
        cats = categorias_por_tier(2, 3)
    else:
        cats = list(CATEGORIAS.keys())

    log.info("Disparo MANUAL pela UI: %s", cats)

    def run_collect():
        try:
            executar_coleta(ANTHROPIC_API_KEY, cats, tipo_run="manual")
        except Exception as e:
            log.error("Manual run falhou: %s", e)

    t = threading.Thread(target=run_collect, daemon=True)
    t.start()
    return jsonify({
        "ok": True,
        "categorias": cats,
        "mensagem": f"Coleta iniciada ({len(cats)} categorias). Aguarde 2-4 minutos e recarregue."
    })


@app.route("/debug")
def debug_page():
    """Pagina de diagnostico - mostra estado real do app."""
    with db_conn() as conn:
        # Total por tier
        tier_data = {}
        for t in (1, 2, 3):
            r = conn.execute(
                "SELECT COUNT(*) AS c FROM oportunidades WHERE tier = ?", (t,)
            ).fetchone()
            tier_data[t] = r["c"]

        # Total no banco
        total = conn.execute("SELECT COUNT(*) AS c FROM oportunidades").fetchone()["c"]
        exemplos = conn.execute(
            "SELECT COUNT(*) AS c FROM oportunidades WHERE titulo LIKE '%[EXEMPLO]%'"
        ).fetchone()["c"]

        # Ultimas 10 execucoes do cron com tudo
        execs = conn.execute(
            "SELECT * FROM execucoes_cron ORDER BY id DESC LIMIT 10"
        ).fetchall()

        # Itens recentes (qualquer tier)
        ultimos_itens = conn.execute(
            "SELECT id, categoria, tier, titulo, link, data_coleta FROM oportunidades "
            "ORDER BY id DESC LIMIT 10"
        ).fetchall()

    info = {
        "versao_app": APP_VERSION,
        "modelo_tier1": MODEL_TIER1,
        "modelo_tier23": MODEL_TIER23,
        "tem_api_key": bool(ANTHROPIC_API_KEY),
        "tem_cron_secret": bool(CRON_SECRET),
        "tem_discord_webhook": bool(DISCORD_WEBHOOK_URL),
        "tem_youtube_api": bool(YOUTUBE_API_KEY),
        "tem_reddit_api": bool(REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET),
        "usando_turso": USANDO_TURSO,
        "db_path": str(DB_PATH),
        "db_existe": DB_PATH.exists(),
        "total_no_banco": total,
        "exemplos_no_banco": exemplos,
        "tier1_total": tier_data[1],
        "tier2_total": tier_data[2],
        "tier3_total": tier_data[3],
        "ultimas_10_execucoes": [_dict_from_row(r) for r in execs],
        "ultimos_10_itens_inseridos": [_dict_from_row(r) for r in ultimos_itens],
        "categorias_config": {
            cid: {"tier": c["tier"], "max_idade_dias": c.get("max_idade_dias", 7),
                  "label": c["label"]}
            for cid, c in CATEGORIAS.items()
        },
    }
    return jsonify(info)


@app.route("/health")
def health():
    return jsonify({"ok": True, "version": APP_VERSION})


# Init
init_db()
_ensure_metricas_column()
_ensure_selecionado_column()
_ensure_triagem_columns()


# Logo PNG transparente embutido (gerado a partir da logo Silva Pinto)
LOGO_B64 = "iVBORw0KGgoAAAANSUhEUgAAANwAAADcCAYAAAAbWs+BAAABCGlDQ1BJQ0MgUHJvZmlsZQAAeJxjYGA8wQAELAYMDLl5JUVB7k4KEZFRCuwPGBiBEAwSk4sLGHADoKpv1yBqL+viUYcLcKakFicD6Q9ArFIEtBxopAiQLZIOYWuA2EkQtg2IXV5SUAJkB4DYRSFBzkB2CpCtkY7ETkJiJxcUgdT3ANk2uTmlyQh3M/Ck5oUGA2kOIJZhKGYIYnBncAL5H6IkfxEDg8VXBgbmCQixpJkMDNtbGRgkbiHEVBYwMPC3MDBsO48QQ4RJQWJRIliIBYiZ0tIYGD4tZ2DgjWRgEL7AwMAVDQsIHG5TALvNnSEfCNMZchhSgSKeDHkMyQx6QJYRgwGDIYMZAKbWPz9HbOBQAAB5NElEQVR42u1dd2AU1dY/987M7mbTAyH0XgNiAbGbxIqKYtvos5cnPHv/1GfZrL0rT54Kigg+RHcRKSF0NqElgfTee+9l67Tz/bE7sGAIQQET2PNeJNmdnZ25c3/3nPO7pwB45a8K6e1NRKQAAMuXLx5aUpT5aV1dcVl7ezV2d9djbW1xS2lpzjcrV64cAgCg1+vpqbxQ5VoKCjL/09nRuGvbtvUXAwAYjUamj58niEi8j9wrp1X0ej01m82sMoGPN8F37951UWNjWSmiAxvqS7CmpjCnuqqgoL2tChFtWF1dlLd27eoJhBBAPDWgQ0QGAGDfvl1Xt7dXIyKPhYVZX7jfY0/sXMe/d6945VSBjOlNGxiNxhHV1YXNiHYsLc1etWnT2snuQ6jZvP3y8vL8bEQB8wvSknU6nepUTGREIIhG5sEHH9RUVOYUtbVXYUtLhVBekZsFAMfVWohICAFYu3btoPDwcNUh1U7IISB7xSsnzQw7GgRbEraMSk9PfKi+oTSuvDwn3mg0MkdPWkVr5OWkvocoYklplrkn02z16tVjGxvLmp3Odty/P/5mT210ssRsNrMAAGlpe/+NiJiStufzkpLshO7uBmnz5s1jj2fOKvdfWJi+raWlsqisLO/N5OS953lNTa+cMklI2DouNzft8erqvM0N9aXdiAIiWrC0NKd0+fLlQ92rPfGchAAA5eW5ByTJKh9I3XM7IQRycnJUHseoAADy81O/RpTk/IKMr/6MideHBYOsX2+c2NZeY21qqex65ZVXAouK0j9DlDA7+8D9ipl4LM0OALB+vXFcXV0Rz/OtiOhEh70Za2uLktOzDjx89D17xSt/eqLu3x9/W0tLpbm5uRwRBRTFdmxuKsOystzPcjNSLpo7d666N+Kksak0z2ZrxPSc5PPc5hv19IX0ej09cCDhSUQey8vz1p9sDaecq6AgbSMij2lp+54FAMjOPnA/ooDl5TkrevtOBfw5OSlPShKPhYUZ29LT9y2ory/e7eRbENGGRUVZ6z755BNfZcy8s8crxxS3Ocj0RAwAAGRmJj2JaMfa2qL0vLyUN6uq8nOtthZp48a1c3pb1QlxvVxZmZchil2YlpZ4g3tCMp6TGRFpRsb+pxEFuaQka93JBJxynsTEhDscjhasqSlo2Lx5wyXLli0LXbHiu2nt7dVCZWVejV7/oEa57GOZkyUl2btldGB6enK08l5iYvy1NbXFpYgS5mYf/MF1/LEZTzcb6gWkV3oEDgEAWLx4sV9s7O/nKX9nZSW/h4hYUJj+DSLSlJQU7jia5X+IolyQl7ZcMSPNZjPrJmA4AIDi4qwNiCJmZCXpT5ZJ6far6E+LFgVUVuZXORwtaLM1Ic+3YX19KVZXF9Q0NJSIbW1VuDtx10UegPA4h8ucXLNhzdSWlkqppqagc8mSJYNd1+66b+PvxnNaWiu7W1sr5G3bYqd5grQX8YLubASY2Wxmi4uzozIykh7oA3HAmM1mdv/+HXOs1gasqsove/rpp9XH0nDKSp+SkhRltTZie0eNIysr5bajj8vISHrEYmmUGxpKHdu3x473nOgnQ7ulpe35CNGBxaWZB7Ozk14qLc1eUlaRu6uquqCirq7Ywgvtck5eyms9AV35u7Aw40VECfPy0371PLeyYJSWZsciOjEzM/mhY/mDKSkp3O7d2+cCAOsF3dljOlLPFdi8ZcvUtrZqrG8oEX767aeRiEiOBp2iKRQmTq/Xs5WVuYUORysmJOy8sifNcLQ5lpOT8g2iHVtaK7C4OHNZTubBfxQWpj1QXp5rdDpdmiczM+XB3s51ovcKALB//+4IQWhHi6UJzea4i48+LjV1/9OIDrG0JHtzT5rJvZjQ8vLcg6JoxYMH9873ZD0RkUFEUlKSZUQU5MzMpCePBpyyIC1atCigvDyXr6zKTzObY4d6mc0zWJMdSVS4JolbA5GyipzfEEVMT9+/6Hj+k7LiZ+Uc/BIR5bzc1E+PYwIq383k5aV+2t5eY3Oxmzy6/rVhfX1pcWLirjtOFtiOWFDM269pa6v/PDV17+uKRnJtWrvMwbi4deFOZyvW1xeLP//8c5gnQJRz7NgRN7OjowarqvNbVqxYMQgRGaPRyCi+b0REBFtVVVDO8624e/eWuUePobKAbd78+9j6+lJnU3M5rlmzZozne+5n5N1MP5Nk27a4i7du3ThD+Vuh5/fv33FBR0e92NBQ2m00Gkf3pOWONhN3794V4XC0YVVVfp5er2ePt1IrBMqBA3vG5+WlPNbYXPlqaWnOi1lZaddERLgIi1M92Y66RkVbq3JzU5c0N1ekb98ee40n6A/tI+alvIkoiQUF6f/p6bypqfuf5vkOubq6oPb777/3P1pzKefbu9c822ptxKqq/FJduE7Vi+/slQGs2YjZbB5ZVpa7qqmpHJuaymzFxZlvf/HFF0EeKzEpKEhbiyhjTk7K8bQcIQRg0aJF6urqvPKurjo5Pn77+X3RTn1g707FGFC35jju+ZcsWcIdDchFixapS0qy6xGd2NBQUlZcnPVRdvbBSHPi5rHx8ZvOyc5OebuluYpHtGJWVvLTPY2d8ndq6v6bEa1YXpGXoDwf5b51Op0qryBDn5WVNMtNJnlBONBE0VJmc9zI+oYyvqOjRmppqUBEHuvrS0rT0xMfBgAKAJCQsGVOW1uN3NRc0b19+/bRR5uhR04gl39SUpL9FSJiTm7qf/vKLCLqqTsOUflh/u7J1cs1kEWLFqmzslLuLS/PiW1sLHMiiohowbbWKrR01yOigFZrI+blpnx6rIVDGa+crNSFiBIWFqavdL1epAYAWLVqVXBZWd4uRMSKirymNWvWjOnNyvBKPxbFuc/OPfBvRCsWFaZV5OWlxCF2IWI3Vlbmxyce3BPpMjEP/oKIWFSU3quWUzTVvn3brm9trU9Nzzq48HSYhH+/Sb5teH5+6iPl5Xmmmuqi1Kqq/JTKqvylu3dvu7o30/Aw05n6NqKMLhPVNVa///772Kqa/AxEEaur8/P27Nly7vHYYq/0A/bx6LCqo83K74zfhdTVlTbabK32uLi1l6dlJt1ZX19Sgshjd1cdlpXlfLNp04YbOjvrWltbq6z79u0c05uWUzTAWWKaM33NiujNIigqzvgRkcfMzOQHAQB27YqdVVdfXIkoYkVF7rZlxmWhXrCdCVrO/cDT0xNfR5SwrCx7DwDAqx+8Gpyfn25oaiqzIMpYXV3QWFmZWy9JXZidfXDp8RhLxaE7Vf5XfzTR3f4VoyxkiGb2+L6rC4wVFfnbZOzCnTs3T9kRv+OKlrbKDkQb5uam/AgAXG++rPv7GLc5znh9vL+JFAEAkpKyR9fQUJ68c+fm6W5NR3vUct99FlJVnd9os7Vidnby9cr7sbFrp1VWFqyy25rR4WjGtvZK7O6uR3d0CXip6r8kRGFoq6oKCppbKqx79ux4sam5gueFdszMTFa2K3phho8Z4+kNEzvdpg4AkIKCdCMiYn5+6vZjPSDFl0vPTHoZkceysqy9EAEs4uHI/cTE+Btr64rSm5rKu/Pz0776/ffVo7ybsydlUQS9Xu9XVV1Q39RUzlss9djeXuPcvz/h3uOQNoc+/9tvPw0rKEi/tbuj6e6srIM3vv/V+4MUC8P7fE6jieM26UJraooaBKETk5MTHuoJdApwvv/+e//6uqJKp7Mdd+zYeBuAK+RIOT4iIkKzZs3/xnhH9+Q9IwCAtWtXT2huqUTETmxsqqjeuzf+Ek9C5RgmJAUAkpt70NDcUtGCaEUXS2rDxqay1qysxDfBnfzrBd3p1XKQnLz3IUHoxJragqaff/45rCcTRdFy+TmpT8uyA8tKc1I/M37mo5imnv6D1084Wc/HtZ+3IWWDNq8g7ePqmqLdsbGuKJPetlGUZ5GdfeB/iDa02RqxtDw7qagk8z8lpVm/NDSU2BEFLChI3aLX67XetKCTC6pe64cooMsvTNuBKGJe3sGVngA7ctXU08WLF/tVVuU1t7VV47ZN62Z7+mne/Z/TI70RLYeDrvc+Lkmd2NJaYc/McQVFK7Jx45pJ5eV5yYgy5uam9ong8kof7P8eCBDaEyAJIbBhw4apzc0Vlu7uRnRHpfdgWrrAtHfvzuu2x8ddCH2o5+GVk2eN9GFBI4hIlixZoq2szK+QJAumpye+fvjzh+NAf/75++EtLZVN7e3VqITseQmuP/1wDj+UdevWTUnNSLx99+4d4Z5+QU/mYmr63tcR7VhZkVt4rAxkL8D6v+bbv3/XZVZrI1ZX5zctXrzYD9HIeD53BXSF+Wm/IIqYlZX01PHMVK8cx6TQP/GEX3Fp5redHbWIaMOOjmrB7Sgfi/pnnn76aXVpeU4GohPT0/d/cCxTwzNVxyv979lXVBTNQ7TJ1dUF24/xvFlEpNnZB5YiSnJ6eqLBC7g/aUYCACxdunRyZWVBFiJiVVVBXkV5/raOzhq025vw4MH4G3s2F12ro9m8/fKOjjqhq6sBk5P3XXk8n8Er/U/D7duXcKUgtGNlZW5GD8+aKMCqqMhNRrRjSkriQz357V7pRfR6PTUajUxKSuLl9Q2lNU5nC+bnZ32upK+UlGT8F1GQSsqyf3I9hD8OrvJg0jP3L2tvry9JSztwaR9CtLzSjxZcQgi4iK38equ1WYyP3xLlodUO1Z85cGDvfVZbk9zQWNa1Y8fGEV534YQGGgiinq5cudK3rDy3ClHAvLyDijmhAgDIzT34OiJiYWHG570AjiAiWbx4sZ9Op/PzPoSBa1YePLhnIaIdm5oqqhVLRZHU1MS7m5oqOhDtWFCQ9s6xXAev9D7QFAAgOTnh+paWSt5ibRRSU/e+4DIxdt3U1dVoa2out2zatHZyX7XWqSob7pXTY1rm5Bz4CLEbrbYmLK/ISygtzVpRXp53wGppREQ7FhZmbtHr9aqeCvEqboZHKhTrnQ/HGOiDB/c8Kojt2NBY6kxL3b+kpbUC29pq5N27t8/ryYk+lqbzjujAX4BTUvZENzeWpzocLYjoQFnuxMbGstbc3NRP586dq+4pvEuJQupNg3rFg4ECAEjL2P+hILQh72zF5ubySrN5w+VeAuTsBB0AQGri7pkHDsTfnpmZfPV3330X5rm4HuszKSn7rq+sLIxpaan6sKAg/bktW7ZM9LoZf5RDLFR+ftpqxG6sqSnMXvLhh4GISL2RIGefT9eTsuopW0AxGbdt2zippqbQ7HS2IKJ06KetrYrPykp+TwGmF3hHmoT06aefVpeWZu1DFLGkJDvp6aefVntXqLN2ET4iH68nbYiIJCEhYVxdXXE1ogMbGkpac3MPLs3NTXumsDDjy4aGsjZECYuK0pb0xTU568wJdzbA0JqawhJEBxYWZv6ycutKXyWS3DtKXvGcL3oAWlyctRvRgVVVhZn795sneh4TGxs7vqamOFeUrJicskfn9en+MIgufy0+fvs5LS3l3Q5nCyYk7LzUuzp5xVM8yLZIm60Jm5oqOrdt2zgJwFUO0aUZXUWL4vdsuRaRx4qKvF3eedSDKNEDSXt3zU9L23end5C80oN2c/v86TGIkpyfn+YOjjjc90FxUz7//PNhVVUF5YVFGTmzZs1S3vdaS57iJUq80hfAlZbnfIyIckFB5v+59+Z6DPXS6XSqZcuWhXq5gOOYDb0VU/XK2Qw4pbVY4hMuUiTjOwCAoqIitV6vp4heDeYVr5x0C2jDBuPopqZyR1tHbfu2bdsm9aIRvYERXvHKXzQr3Zniia8i8ljfUFpyMG3fnbuTd09evHixn9dX84pXTq4QBXQZGYmLEbtQljtRENoxNzflGU/T8+8U9hTdOAEgSAigdx545XQpOUKI5E7xeepg2t7tYYMHLQgOCplJCB3iOiTyzJ6P7qgAFhEZL9PoldM47w7Ntblzn1brdIfbY/WmKFwEy+E529+3nxT7mO7cuXmKxx7H4QPI4WIwx0qp8IpXTuJiT/sKrgF3g8puf15OyjsdHTViXV1xaXV1/rrCwsx/p6cnXff777+P8k4Dr/wNwCOKMlBq3xzjUBobu2ZMWlriDXl5afrGxrK4jIx9j3jO7ZOplU6KGieEYElJ9r6xY0dd0tzcZA8ODvZRqwcBgAXqGxplSZJznQ4+k5fEZFu3JaOsrD6vvb29e+HChYJ3anjldFpjv/3224jx48PCAwICLmBZdhbLcuf7+KgnBAeHgiDYgOM4qKqqte3dmzbyvvvua5dlmRBC/rIPeNJIE5PJRABALigoeTp0yJBkQZDLtm5NWDh9+qRLVSpVBMdx5wcF+p3jNyLoHADVfTZbK4yfMNZWVFjxOACsdKVhEMk7F7xyKnw6Qohs3rtt9oSxY18DWZrIcVx4cHAgSwgApQw0N7cI3d22pNrarFhE+frw8HOusNlsK4qKijplWe6fc1NR19m5KZ8jIipl7QAA9u3btcDp7MTKyvzE4uKsVW1t1ZXl5bnJ69at8/duRHrlVJuVrtSdzcNqaooaER1YXV3QUFmZ22S3tYrJyfHvL1myZDAAQFycaabV2tJdV1dS99lnn4UQQvpvaKESIPrFF18E1dUV13RbmtFs3jbbaDSGtrXVtbS2VncqvtzKlSt9H3xQr/Gws73ilVMmCmjWrFk1qaAg46rF+sV+W7fGntfR0eBsbKgoMBqNqueff96nurqwQpZtuH9//D2eSqRfs0MAAPv377pXkmxYWppzsLg4ayeigImJ5vvcx6hOhR/pFa/0gUBxTTp3GnlKyp7nEBELC9J+yMzcvwhRxoKCY7c+69egKyxKNYtiB/JCK+bnp/wIcDjdxmtGeuXvmZuHtgOIMhdLS3O28HwrtrVVYXNLhW3btthpp6qeKf3zF27srQMlEkKgqrLhmfb2NofDLogNDe3fH0ETEYIng/XxildORAgxyIQQiRCCzc3NiIgkKengcx0dHQ5/fz+oqa7/4rrr5uXHx8czhBC536nmY4myemRkJH6E6MSysuxko9Ho5y2P4JX+ZollZSW/J4rdclV1QZ6731z/C8pISNj+IADQYwFIUcmLFxv9qqvySxElTErad78nGL3ilb9LlM1ss3nbxW3tVdhtacDExPgb+pXvplxkZkrSFaLYidlZBz50OZ4pXG8rSFra/geqqooKjEZjiNd380p/sdIQkezZs310Y2OppbA4bcPxlIHS3+60zV8FcMnJu+d2dNTydnszJiUlPOq+mGP2a16yZAn37rvvDuurOeoVr5wm0FEAgLi4Dddt2bJh6jEaRhJ3eb6jC86eHrNTAV1iovlhm60J29urnUqHk+OZil6weaU/arre3vMsmf7bb78NKSxMH6HX67WHjzkNm+KKNktP3/9/iFZsaCxrWmc0TvEEpBdsXhlImq6XTrk0Ozvtn3V1xbvr64u7mprK+IbGsqqSkqwlP/7444hTBjr3RbGIyBiNRiYnJ0cFAJCVkfwFooiVlfm5S5YsCfQ2pvfKQBfFT1uyZMng8vK87TJ2IqID6+tLsLa2qL29vQYRZayvL6pYt84Y7mmeniwhvZEi+flpvyAilpRkbgEApl/Sql7xyon5drSwMGMrohMbGss6c3MPvrF79/bJa9euHRQfH3dhWVnuKkQHlpdnZS3XL9ectO0uBbm7d++6rLw877mMjOTrd+yIm7ls2bJQADhkPhYWphoRHZidm/LtqUC8V7xymsDmZtaT5jud7djUVNmxd2/8FT0dW1iYvgFRwIyMpEf6wmEAHCc9x62l0Gg0howeHRY3Zsy0AEnqBIvFClOnTrBWVuXWoEwqCEJRW0d7VkNDbeTE8WMX5uenlQDAZ0ajkYmOju43aQ0IQOL1ESe8xxIPANOnDznxqBjTkX/qwsMRACDmmB8wAIAeAAwQE9OHejDkLy+p3kifY1hzWq36dpXKH5ubS768/PLIPYhF6piYVUJMTAympqays2bNErfuin1z9OgxN/oH+t0FAD9ERh6/Zgo5ni1rMBjk7ds3TZ4wYexrIOMghmNGMAyEUcqG+vv7qfz8/AFAAwAADkcbaDRaqK6ubk9Kyh6h0+kcAK4wLu9z7I+rORAAPTGZ8v40bnWeC1NuEzn+4hUpGwwGuf+OiSsvs76+fF1Y2KD5GSnZl51/4aVJJpOJKMrDzV7inj3bR888d1plW2tX3rhx4TMQUQmKxj+l4ZSBufbam4oA4GHl9eeff94nIuLC4JCQIYN9/NRhWq12KCOTsZyaGx4cGDimtbVzXXR0tL2/JJWiu5yK/ul7AyaHWaLVrBwKsgw9VeQlBNApAREFXgjwVzd0dAtgt/Lg48N1hwwJ6QJRAtF9rCSJ7iEUQRRFEEUAUWTdv4sAIoAgS8ijnWhYlX3KiMEdNtFOZImik+fBwgPwPA/dTh54HoDnncDzAGGjBncF+Y7nASxgsVjAYukGiwXAAhawWACg2wIWAOAYSYy45RILpAKket5EKkDqka8cEj8/P7RYppDU1KVICIgAhtO8GCYMiMXIYum2Dh06FK1Ouy8AkPHjx1MAUOYyQUTYtOl3X0GQmyXAWjfIGI9jTlzDHWVaMgCAlFIJcWApLKNRx0RHmyTjuzetmjJKe4/VIQLp4daJe9SQIAACIALIeHi9wj/81/0pPPya2wr3+Iz7f4iA7nVdBvdHZALyofddLyIAMAy1M0AF5XMyovta0P0drouiQESNiu1Uznnoa9F1VkAAhbo6wsYgBAQRkRKmlRdFq5+WawNA10mO43lT5fplGSQEkCQEXpSBlxAoYWy+Wq5NFCSQRBlEGQBRBpZlXIwCA22VrbLp+Y/j8t3KoN9NJLPZzEZFRYmZackPzjx/1o9FRdnLpkw5/589YYIQgl9++WXYuedODY6KmlugvPaXAdfTl8XExJCYmBgAABIfH08iIyPd3k6ka872l0hrDw0X9+ktWQG+ZJrNIcgMpczxphchhFAKIMuIkoQyulF6IoNGCAAgAXR97MjPk54eAoEjOn724KeRQyf+47n6fl0EGOL691j9sA8vM+To5w9HrLl/uI8jXyDuNUijZqGiyVG1t9ER/umn223ur+1XoFPYdZPJ5HvJpefmhA4ePKagoPTfWVkpy+6///EWz3ndF4CdkEnZy8NCAECDwTAgnGDieqgcL0k+ssyyhFLZ6pQlUSIOIDIh7vUfAV2/I1IghBKCDgDaQVAO8fVh/F1q46g2t6C86lY/Ryg/0qMxgJ6/YI/vIiHAHDZ58Y9HEiDgCoRABJDJIf15AmwJ9kDBkF6gi+6VgxKq1bAEgICMslu9/vEOEV0aTpQQJBkEi50nPhwdPVLkRhMC+TqdjjGZTFL/miwE3WSfJSVl78O+Wu2Wc8+98H0fX/VthJCLPEFGCEFEJDEQQwykb37p2RS1zwqi5CvLFLQqjtZ3OJ//fUepadDgAKbVJkkAAFoA8NEyjLWtw7E6ocj2z/mXTp4zMyQ61Jc+TID4yeBCACIgEJAIIqGEMAxLgKEMoRSAkiPnLTlBzaMg2uYQQZJdE5djGcLQI0/EizLIMgIiEI2KUhXH9KCLelJ/6EmaHDJZZdn1I8kIMqKMQGQABOL6PyUECBKCDEOJ1Y4dnQ5+t4qlPAEMQCTEZZC6bVKUAQgFWcZAjmG1siyP9FVDCALIBAGdUrd/f54o0dHRktFoZGbPvty8Z4/5+unTmc9ZSpvcg0c9B1FRPidDw5G+nMitgml/r7gVERGBao6V3EsTBAX6tvy+p6C+p5t88smrB/1207RX/FXwqK+GjhBECXhRBperhZKaZRiVmmUlGcFql8DhlDsQxRZRkh0sq2q2OwSUZBn8NGyLimOdR4zk0b/DH/+WEYksyfN9VNQfEcDmlOtFGTo8D+cojuFY4sMylHQ5MZvyUoYsIpUQZEmWASUZZESQAECSZDcUKDAMARVDQc1RsDnFUA3HqSRZDmFZ9AMggwghQb5qlqo5QikhIEgy8IIEkixLgARllKlGzagkJ0ilzfJ/n/9oU/zxxn6Z/tb7QgLIT3YHL1NCWH8/FSoMp6kfg85d7SseAGatWLEi5HiMu16vpzExMRQA5GO5VGxvqlWWd7EAkXJv/pj7Avot2JRV/5YLLiAcW0YRJbfzL1NEIKaYcK693gcXLk0VAEb6rH73gqeCtOT/QvyZwXanAFa76FquAWWOpVStUjGt3Xyjtdu5nReZ7RYrn13SzlZ/vnRT+8kcB+O7cwv9NIw/xzJgEcSY+S9vXKoQVwAgx31+W5yak29gGQbKGhyxj74X9++/uCSxT97PBk4cRoYEqdnhaoaGq9VcOMtJ57MEpgdoOT+OIeBwisCLok9YoOq2Qf7kto0f37y3rlt6a+E7cWZEPV26NJZZsGCeZDLlkfHtwfTChUsFXw3aFfaGEAI+HDcgTCJCiGw0Gpm77rpLevDBB1v/YCJ4KByFtzjelkdvuT9ASJTo/l0JW5E9ER4REcEuWfL5JkmSU43G2LdiYmKk/rbnFqMHAgbA6qaUoMnD/IM4hro8LkIQCIDOrJNJlEFc/PLcq8eEqj8ZHMicb3Py0GmRRABgCCUUZZB8NSzT5cC26jb+g+0l9uU//bSztSdnUX5LTw/tbJvySF/2phQpqreQBcP88JnkWoYSwigDyVHX4O/SR5Dm6UMgOtoEhFHcMASCfJBZH8Ee2qPoozRPH4K63HCkbxtkxATxvz9BKwC0AkA+AOxUjnv3+atGjBukvVjDwc0altwQ7KseIogi8IIsBPuzl/to6K5f37vl60su2fpSUlKqPTh4PBMdbZL0+giCAGDnRYqgOqTFuQHkyLj33oh7jw098EEB4ikhRFQW2s2bfx87ZcqUB7u6uqznnXfxp0o9zGMCTtnojo/fNmnypHG/Wuz29RVlVUZCSP7hLzKzqan+ZNasWWLCvp1XTJky87qqqmJfF1tsov1V2w0bHkwolV0UAwGQEVkCgBAVS1a9c+OnQ4NUL7IEwWJ3yj5qltpliQUAkCWU/bQc02bD9KJqR/SLX2wrAQBAo46JyW0iAJFyTIwBXX0TAInBIMOf55LIv9yuVcQVk7BHoqWnxZGgFGVIEM36CIgyJIh/9rsRAWJi9GT69DwS6l4oImMSJEJ21QLAbwDw2wv/iBh83jS/24O0zOMhfqrzBF4AWZKFsUM0T7yhG3Jh2nnX3x8dbSo06yNYxdbstjgohqpgAFfWQBc7daQ2AwB5wYIF2ieffOzagADt/T4+6pvCwiZoqqoL+J9++mkpAHQfzWQeAbjIyEhqMBhkf3+/64cNH3M+AJwf4Of3VkVV/saOtu6VX3/9/RZComzK8fn5KQtlWYSaysYPDQaDHBnZf0snaLWH3SdKCKjUbMcjt9ziP/8KsnFoEBvRbXXwjIpRiTIVK5uEtEH+5CKWgqxVs0y7RS5OyXdcY1i2rW3JklncwoWpIolW2LUEGBhkbV8mFcAfNsINrogUk0lHdQBAok0tALAUIOKHH9/xv3+IluqD/VVjOi0OR4gve+EFk9X7P3n2+juiDFvjl+sjNAAg+qvUnYgIBAZ2QLun+xQfv/2cESOGRWu1qruHDRs2kRAfaGmuclRV5f3WabWsuuiiQc6efL4jABIVFSUiIklNTf0hOzujIDDI/0EfjfrWMaMmzB8zyjE/JualkldeeXp1aWnFT/n5WR2hQ0LnNzRU1qSkZ213I7l/lyqnAIQAEUUJ6hod5994MbwxPFgzu6Pb4fD3VWmau6SDNa3kIa0P3Bvoq77Y6uDRLoJYVGO7x7BsR5tZH8FGLUw46/oguDaoXQsMApAYfQTz9tsJ4kNvwvIn779ww5XTwxaNDFLfa3M4eH+ODTlvgjru29dvvv1hw8Yter2ehgXkd8jID+zVCJHs2bN5cFDQ4OtDQgLv1ahVcwcNHg083wENDQ0ZXV2WX5qbW0xXXHFt2Qn5cG5E2gBgBwDsiI2NHTNlUtedPr7a+0NDB52rUgW/6eOjfm3y5PE5QUGBmvy8xlXPPvus85lnnjkh/+FvwxwhYHOKODSA6P1VDHRYnLyvj0ZT08x/e+fr+c/dd5+Wveu8MU85nIKs9VGxFY32pS98uSPFrI9g/4K5duaADwDBPQ7uMWn9L8B93711feHkMN+3RYGXOArq8aE07osXrrv9eYNh3Xf66zXjB2ncWyoEBHGggc3MEkLE7OzUd2fMuGABgAMaGuo6y8sL1na0dP58wZxLzYrmU/iOYymfY5qARqOR0el0QAipBIDPAODLtLTEq4KC/B7Qan1vHTNm9HltrQ1QW97wo/sj8gCaNKBiCDolSdRqVKryRpvhXv3mGACAq8bNvS5AywbwPC/xNlGut6gWIwIxRQ/xBmAfJVGGBBERCJh0lESb3vnm9bllE8NUPxBAqmKRho/Wrvro5ZvOk7u6W8lgzYA1KE2mZgQAkGXR2NJSPb2xsfmnrJLCjffcek+dcozZbGbj4+Pl40VYscdhZ0Cv19PIyEgaFRUlXnDBJdsBYPu2bduGjxtnfdhms4yae8stSgyZPDCgpoSDoOSv5bjiWseiBwybY8zLH9REPbzCqVYxsziGoMxQptMmlb34kSX/xY8AAUyyF2LHNjddvu2WVYv+fb1l+lDNOpRk3lcN2kmDIS69GP5vnChJDCVURhnswsCyyhUsnHvuRTs92VtEZEwmE0RHR8tRUVF90tvHTRI1GAzKyYi7aylz3XXX1U2adM575557yb/+TDzZ3yE+Wu2hGC8AkPx9OLaqRTA/YIh7Ds0RrD+fIwEAqhkch4iEYygA0BKABNFds8Kr4XqRhQtTBaNep3r2/a3rK5qFl300nMpqF4RQP27iueN9lggyurdECQgD2AtWSqQrnIUbjH2eG/SokzGIZvYYRVEwOjpaIoRIrpoPZlZpwti/h0h/JEuJACxLaKdN7MqrFB4hBCAmPlKetWCeBAAgSxim5DWJgtjhsinyvOUi+qIJDCbebI5g//lO3KeVzfa4AD8VZ+UFcbC/KlTNEtYVbUpAcCPONADvkRCCUVFR4p+d9/Sok0mERInu+utwrF7cBoNBdh1HBoyZFawKQEIIyDKiVs3S1m5cYViytWLXWxGsZ3QAEsIq1qeDl7xlIk5Q4uMjZUQgBY24sM0idWlYhvKiLCtB3IjygNZwRyunE+1V7zmhSEZG4udlZTkLN27cOEmJNHFrNXQXBhqwxYH2HSwOkSRZzVCQnQKCxSFtRgQSr0yUmHgKAKBWMS1KwDGjol6/7QTFYDDI8TERjOGrLTVNneJnGjX7B3NcgIGPOEU5Kfhwb4wzSmU7d+W6P2BFMQvl9ZvXh58zM/x5SvxAq9VARUX+AbuT32KzdG/58cdf0wghTk87dsCVTXB2qwB8KSFEEiUEix1thAAadUeyj3ZBbiWEAVFC4Fg6HAAAcsO9/tsJSGRMgoQxQP75z7bFg/wGPRfgwwQ7BQkZSgBlBNk5sEtuLFq0SB0VdelbnJqtau3ozGqsaS294447mnraCjh6m4B1vYZk82ZTU0Fh0eNajc9cjUZ9xZgxI+cA+M5xOFre+vdrz5a8+OK/dnZ0d207mJS8jxDSCH3MJugvovFXufJHEQEJgMwLPWpqpyAXARIQBBE1lM54/PHLg4nB0N5fM5T75+oPaNZHsMuWJbRd+968VUMC2ad4QRYJAAcAwtRJo1oAAMLDB95C5lZQzvLy3DvGjg2f4rA3Q/uozq6qyvwKQRRzRVFIbe3ozLVbrPlXXz2vnhByxI4/66GpmgHgWwD4du2KFYOmXHzORT6c6iaNRn11QID/lKG+4yaOAdvC8WNGd86effE3559/8Wv9pWZJX8SX44DQQ4MGNuFIwDXnuTSdzSYk23kOJEQpMEAdNDsseC4ArI6PiWAAvBvffRX3eJJWi7gmLIB9ihKk7mx2ZHxUA95Ub2rteEmrLb9XEMRxKhU7JyDAf6avr99MAJ9/AAhgsbRDdXVBJaFMXlNLS+wF517yNSIS1tNMjI+PZyIjI2VCSCsAxAFAHMyaxR345j/nBQc3Xstx7HWhoaERKhVjURazgTJAHKdypTsgUBkAJ4wMa3FZi65VNtpkkhGARDYKGc8NEmuCtcwIlCUIZKVXAeCX5uneje8TkWiTSQYAzMxmD44KFuoDtHSYILoTajlmwI6lQhReNPuyWACIBQCoqMjdxbLsFWlpB+8PDQ0OUqm4KQxDh7IsuWHE8OE3EMQbFi1a9D9CSBf1OJFCd8ruVj4MIjKQmirMmXPZwUmTzn1/7NjpkXv2JE5qarIscn+q32u3GPe/KhWnLA8EEUDlrzo6uA/j9RFMwooER7dTXumjYYndKfDDB6tnLntr7iPR0SZpyYJZnBdKJ2B96fV0aWysTZQgR8UyQAAlRGAL82qCAADy8gbudou79L8aEYndye8OChrMqrRsxbRp5307YcL051va2vao1RpwOK3QbbGuCA4OlvR6PaXHQDG627IquUDKvhuZO3d+SVRUlEWx1QfKAGk5zqPcAYJgs5MenX0EUtopLWruEjs0LMM4BVEaGaL59I1/RU1ZuDRVMBp1jBdLfZN4cDG/ooBFlFIAIDKhlMooas8AllKOj4+XCCHY0dZ5gBAWfNWqa3bu3Dy9prYoadb5F/+XAFGXFJf9c+rU8x964IEHrAaDoU/lyJEQouy7obsp+YBbmXx9Va49EHdVO57v2dk3mXTU8Nn2ptp2PkatUTGigKKvGoIuGKeN1c09LzQ62iR5QXdiQjm2Uinvx1ACY0b5igBHFpEdiBIZGSkhIpOTU5zU0FBtCQkJ/teUqWP3jBg+4aKamuID6Rk5c8455+JlHh1VXYA7kf0196b4gLPBOQ4OlYNDROD5ntNFFEAtfHfrotJG25ZAf7Xaahccw4LUE++6MizuppsuD46ONknuDGuv9EFsTl50B3YhyxCwWMRgOBMQ59J0UmCgJlSSJMuQIYNHhAQPDi4qyl50330LI6699qZss9nMehKL1F2X8A+lERQQIiJ1NykYkPa2abrLTyiqbBlCCQECIAMC6XYeOz9LpzPJqNfTA8W2fzR0OHMDfDlNp9XhGDlIO3vBlcG7Xv/nFePcGdYsDuA05tMlKpZBVzEz1wYx7yT+A/2eEPU0JiaGycxMfujKKy/ZP2JE2NC2ts7G3Nwi3ZQp5z23e/duByLSo4OaKSLCnj07HtXr9So30IgSmOn25ZTg5QHN0lEC7KGqWIDQC96AEMAYAFi0IqEjtdpxY6tVLgnyVWs6rbxjcCBz3mXTBiX+95W5N0cZEkQCgHqvtjuOGmBClDhWCgC+Ppw8sMGGhNK35YqKCjYoyP+LsLAxIeVlNeY9e1IvvfDCy9YgIivLPWfQ0AMH9t1/+eWXf6+7a/4itxZjCSGYnZ3yeF1d8e7amqLNOXkpTwIAc1obip9k8fFhEQ6ZlK46/r2JwWCQjTodY/jPjqrE4varmq3yweAAtcZmE50+KjksfKRmg+m9m//7qO7iEIMhQSTElZDZU7+CPzVH6cBXnJHurRSWkcccUTRzgC9Prop2Ml2xYoXDbnfuLC0t+GL8hOlX3XnnnWVuE1I8FqFIhwwJfsnptKIsY677ZEJGRtJrM2ZM+XrYsOFXDB8xau70aTMWZ2cfXOoO8h2QM0GtZg8HjiKA3e447n1Em0ySUadj3vtmT/XK9R1XVTXyq7RaTo2IIIiCMDJU9cTtFw9J+1/MvPsRgYtyAQ/RqGP+bBdYPKxl3b8AeLThG1iic+UQsgSmi7IEAEiQADBnQPiAor327k19eOLEc14ghIBer6fHy4tjff18ZjQ2N5bOnDFrMSIyW3ZvGTJ02ODX7XYHVFbWfuFw8AUjRgz+cPTo4Y9s377pS0JIdk/lv/o94BjGo+4qysgo5SAMxwWdu5qZxZQA9y15Y+6uMYPU7wX7ckM7bA7RR0XGBISpVm78dN4r3TxdmlVLV5FoU6ti55ui80j0nyjnTcjA1nB6PVBCQP73wqtGcAyEC7wMcCjW58yRf/7zn91Kmlpf2nBRluUoQ6hVYVzGDx3xf2FDRvlWVtVsnzbtvBfOP3/O0ra2zm8CAobgoEHB5wAAxMfHD7iB0/hqZXQloFI1x3RfMmtyGwD0qfGhwWCQEYC42MstP+wssc6qaRN+IIRhVCwHDodT8NfQ6eMGsYsiJmKO8f0bv1z02txwQgxytMkkIQJx+3l9RJEOGErgcGMCacBNxEiIoIhAJg/1uTrIT+UjypIEcOa1ofaodtAnjoNtb+8sDQsLPSc3P+XfDpvDMTg06ImurhaoKC/XK5pMFtEJAMBxrHOgDkyXze47SMMeNtu6T1DjACC4twyio011APDoEv2Ny4Zo4Z1AP+4qjgJY7E5RRcnQ0YN8ng3Q8E+s++jmDR0O8l9CNpiVOExXvcYE2WDorQaMDlhmFQ6gMjE9AC5SJiQBf30XHiLE1YzhTKRzT7h7Tktz6xuDQoJXh0+d+R6AEwBYyM3N+uGGG25PRESyY8fGEQFBfg91djWS6uq6fACAyMjIATMTct0FTTvaLINJSNChrO/uE0WcYmJGuzSWu3DOfgC4+qtXr543MlDzjK8Pc62PmoVuu0NmKdDQQO6OQC3csfGzmxNbu+Qff0lu/iXKkNAF4CokazK5TNajv2PWgh2UEGAGKi9s1OkYiDHIX/LXzgj2Z6+w2QUEQhgAlOAsF3bOnCt/2b8/3jpyVNdjDGVCrVb7FtOTce8hIksIEXPz0z4dMWLSuMLCtB033nhr/kDz3yLdXhrHMjI5bAZAV9dfWdVchXNQr6cQE4OEkFgAiP1Gf+ucIRr+OT81c2uAH+PjcEogSLIYqGEuCfHlLnnq2rDXH7ny5h8rWx0/kmhTueLnQQwAcZmtQABgUMVef8eEccFqn4Hp8oSGNxFCAFe/w70d4MOwnVZRpIR4t04AgEXUU0IiNwLARs83ppumMwAAzY0Nn1doCiZUVdU9AdBLR6T+7sNxzEm/cldZcwMYdTpGFx6OxGA4AAD3vP/k1ZMnjlDd66tm/+Hvw06iBMDucEhajhk9yE/1VqAGXvj9k/lrK+ut3xFi2HuIYDHlEYg2SdMDRwJLgMg48FSc0ahjoqJN4pcvXHftsCDVbRarU2IpZQ91eT3bAUeIQXbXoFT6XFFCiKyUBouMvPEgAMzxsFkHpGPBqdw9DE/BuRWzUK/X0+nT80h0tKkIAPRjIiI+ePfawBv8OXmBj4q5TqtmwWJ3AKHEd6gvecBPpX1g3Se3rG+2sZ8QYtinTNiShDZCiOpwy9QBxEzqcsNx/vxzg8YPV3/DUJQlSojFIQDHMKjy5lq4tiAVcPVEibl21SnKskwGYgylIvSIxAg8cdakD6LQwno90EiIoFGGBMf9CfA7APz+zRs3nRvqEP/lq6Z3B/mxQXaHCIiyGOqvmu+vluev/Wje75XNzNvR0aaM71++hSdEHlD55QhAYLqOkGiD9Mt7N/4QGsBN6LbxIgFKi5v5D8LDfF6CQz1/zl45rpOgtFUdyGADANAA46HfTq3eMBhAdpdFJ0ajjkHU08ff3ZR552sbH08pds6saRPfsglY5++jZnleBEkSxaFB7G3TR2HyqrfnfZTd2j0cKEgDRb/p9UDBqKMk2iStem/eF2NCNbd1WXlHsL+GbbGK/6mr7Vyp1nAcGYj7G6cbcB4qYWDfKPO3RGtgdLRJIsQg6/V6ikYd8+Y326p1r258Z1Nqx4zqNuElp8hUBmhVrM0uIEGJHRfG/V/UVG0KA+CPIEv9PdJEr49gDQaQSbRJ+t97874aP1j9XLfV6fTTsJqaFkfRt7z15RGD/YJk2VsA7UQAN+BFRRWF8ffUPjIYDDJxbymY9RHsNz/vbY9+beNn5kTu3Ipm59sS0C4/HxW12nk+QEP9OAY4GYi7n7OMJytG86StJHo9NRp1jMGQID5691Vhpg9v2jAhVPWUxeZwajhG3enAzrzy7jsSDAmiiMgBektXH/LhzgphPHU1AYC/J0PEtaWQICIAiddHMFEGUyeYQP/Oi9f+OG2I+uMwf9WdoiSBKMkSuFKnwOaUWEIAjfpmCjDkbzPLDvWJyw13NZ4EgGVv3nTbsCD2ixA/OqbTyju0alZjF4iluMkx//VvE3IAgKDk3X876wBHKQP9qbKf0vbpMPC2lwOAbuXbc+8O89d86e/DhFnsPO/gRWQYeGbBvAhTtCGhxWicroJT7wv9oRuqK3LEICt94pa/e/PFIT7MK0EaciuiCF1W0Rnkq9G0WoT60mbHHc9+uDVx0dNz1c9+tcXphdnZqOEOaxgQJIQDBbX9Cnh6PdCY6TpCok2/vPPUtclTRqp+Gx2iOb/T6uCH+HNTb4kK3kxCrro1OtpUi3o93YJZHiYmQ9GoY+Jzm1izPuKEvj9y+hBUavzrdEZZKWX3x26oCfDRy9cMHx/kd5VaJT2oUdFrfNUAVoeT5yij8vNVq2s7+K2p+c7H3vtxW7XRqGPABN6ygmcr4DiWQQQA6soz60otGtsNcBD6S1C+wQCyAUzuJofby8Nmzrziy7tG/jw+VHtLh8XhHOTPzp47w3fH0Cevnk8MhqK4L26TDkGWsp3uFsjSX4Y/AMDEiepXr5/hNzJYGurL4VQ/FTmfI3A5x5HzA7U0ABHBwYuig6dsgNZH1W6TqqtqHB8++E7c1wCu0K7oaFdqEwAA640xOfsAx4sy46MiQCkBhsgteXkmoT9WU44yJIg6nY5Zs8Zk/UdW1q0/v3fDrxNCfXSdFsEZ6sdMvXCsJnXdx7ekUBCn8yIAFRGGBFDdmg9vHM0L0MJQpknkeXCIKA8O9mtQUZBkRFLXYhmKgKyvhgOtrwZsFnuYn5pTiSAHAiH+KpZheaczVMWxVAYcxDKiPyUk2FfDAssQkCQJnIIMDl4ADUdBq1GxnTaptrlJXLan0PKf//60sxURSUwMIdGGI+ND1WoVAvFSJmcV4BpaLMMCR/sDAQCGoXYAwJgY6Jd930wmk6TXA42JQSSE3PPLuzeEjgn1iey0OkVfDfVTczTSwUsgyggSIPj7kIkhrGqiqwEeAQDGHVWjROIRCPb1O2xTAwD4+QAiASAyILriS9FHDYgIMiLIMgKijLyARJYJcCwDGhUDnTbRYuVxt90hrNme1bJ+mSmpDcAVIeMulnNoPHPDmwgCkJ/8tX4qlgFBFNALuLOGNSGUEAKiJKMgwsQxYyI0MRDJx4DBo09j/xFX+k4MJQTEzcW2225T0bRBfuw4u1OURFEGBOKqiYQAgMQNEPS4EXQBDl3ZEa4EGRfeCCFACQVCZBdACbj/dsf2IYCMCA4BCC9IFjuPlU5JTBUEiMup4/e/98226kPfYtQxEG2S3Sbtkf6h20f9iQjXqDkOeJ7g2V5y6awBHEtZIARAkoAPCWCHvHGv5h1iMLycsmQBBwuX9sv+SQaDQXbn33XM/L+r7vZV++1nGQKiDJS4mERUsZRYeVLsFMQaSZACKcsgSykwDAKlBAgQ1u4QQxjqCm8jhFglSbYgS5plEUVREAihaFdxbGuXQwCNmmkUnNCNwNTaBKx2OEj5059srPO8LlfFqngaY0iQegIagBLeFik/eT87KMiHedTOC4gEzvp6nmcN4FQq6l5ckTqdojRpuPalxa/emDZ74dLVKUtmcbMXpvZL0Ck1MKMMuw78+NYNn0wZ6fNqt9UhAaEMEJB8NCxbXN+5/rH3d77cy2nUABNh4sSJUFKyhT9RM5oAgGzUMSYAyM01oWt7AOTeilNEQgQlBoP409s3vhsaqB7UZeNFcgZmfHsBd6wZp1K5OXhCRBkppYI0ZRi38r+v3WCfvXDzOldT+NR+WQ4w0pAgoV5P79//+7uB14y+f5AfN9whiDIAcZWeI6hF1FNYWs/AgiWiZwwmdR3iBCiBkpISt4ZybWADHK7FGu9O1FUkHgCm5w1BndEkEwJwLE3WkygpOp88e3Xk8GBugdXOS8QVeiB7AXcWaTiFQ2AJIYKIwLISnTyUW/v9Gzc/8s+FG390mUpA+lIM5nQKAUAzxDP/255lnXvFiC+GBqs+dQiSBwCITIhBNusjaFTPQeZHgElJoO2zejsB0euB6nRG+dn5kUFTRvj+oGKA2ESQKfEWzAU4i2IpW9utw1z6DYCXUORYlgGklIAsTR7GLv/fOzd9TIhBNhgMcn8sYx4PCTIAkIp2+ku7RbCylLCAfdbGeNTPKREEIDHTdYQQApfM8fs5LIgZZ3EIkkbFsJR48XZWAY4hwKGMoGIpywuYXdJgf0REpsNHzbECLzgnDFG/vPGTW3a8/fiV06IMCSKiK7Wm/xAoICMivPHFplqbgNlqFeMq295PBBGIkqKz4u3rvx0Xqr6hw+rkA33UbH27UGTjsZWhhKI3H+4suVHqetaIADKC72PvbVu+r9h6SUOXsMdXq1Jb7LwQ7EevnjM5MPnXd+c9Q4iOugoGYb8BXnxMJAMAIIrSAYaSflO0wFX0FoFEm6Qf9Nd+PXmo74IuK+/0VatUzV181bbMjlsoIQ5Czmg1R7yAO+JG5UNDIkoyO3fuRLVh8baCO/4vNrKkWXobCMuJkgQslX1HhaoWxX7KJ3735tybCSHunDZX7ld/SJMRBcySkfSLwnOufDiDTAihP70995fwEf6Pd9ucPMcSldUp2wubpZtHhe8vRpD9zlTtpiRo96UNwFkDOAnhEJ3AUAJbtpSIer2eol4P972xXp9f57zawpMif42KWu1OPkCLF04aqtmw/uObzUtev0mHCNTgUcoc/2Qp878iSttjm0DKnYIEQJD+fZPMlddnMCSI/7rv2iG/f3Rz3ORh2ru6rE5exVBWQCIX1Fr/8X+fbskaDvNCKAERz9CMODfYqGdVBKWLMCHE3S3V6IorPXusZ+IE180DwzAAoAODwSAbwDVxogybd91z0+UX33ZZ0OehQaqHZEkEXhT4Qf5sZIgfG7nxk1vS2q3898nNGuPhUuZA4mMimOMXdj05kpvr6kdud2CDk5dlhp7+0uHKPROSIAIkiF//+6brx4VyXwdr6fjObqdTpWLUokyl8maH7tlPd67X6/U02CdPcEXTnXGajRJC5N27t08eN27Cqqb2pn/NmnlxqtFoVMLcJDcgZdfxenrWAM7fT9MA7lAnhiEI+nBU2gpEGRJEo07HRJtM7T9vgodXGuavHeQLiwcHakZbbE6QUOQDtMwFIf4+Xw/2l/QRH803tVuE/xESl6xUVFbK3OmiTfKpCxVzXXBbp71LGMrwLENUp2nbkBh1OqrTARBikgASxH8vuH7YzNGcITSAfYxlEDqtDmeAVqXusEmW4hbL3c+8v2OTUa9TRRsM/CsLI4KvmOjnzzJEJgD0DMtGJUOHDvlm5Mhxs7ssbS8BwD06nQ70er3q/vt1b2m1qrmiKLXW1jYuISRy7VkDuG47Twb5q0FGBJQh4OmSzX5fAXQhuuILo00mCeEQ07bxvttmJt9y4Zjn/X3oP4O07GBBlMBm50U1S8IC/VRPBWnwqQ2f3pRs4eGX0jq6nhBD+aGVr5eqyn9FYgyABgDoEMBCKVgIwKBTATcEIDF6PQGIpzHThyCJNknRJpMEJoB3Hr9u1NRRPg/5qfHpIH8utNvqFGREJsjfR93UKSSlVHU9ZvhPQo6rpHu4CAAwKjiAJVRm0RUlDYLoHPDkiaLd4uLiwoeEDbqqtragtLy07Hl0paBI2dlJKyZMmHAvgAAAaggOCbouMzPpnrPHpCRyE7iCcpEh1HdcaLAvAHQdRTN59g9o+t/vWa+99ujNX547WviHny/5h4Zj52g1FOxOAShKUpAPe9Egf/aiYLX03tqPbt7p4GH1zormrSTa1KaYXyaTjkZHm/rc7KEvNFgGjHVci20OJXiZAlI06pji+m4GjToE+GPkyLFE6eEGunCEGABqMLg0tMGA4A7fCg+P8Hv+Du0lwVrmXh8Vc9sgPybA6uCh2+oQ/LQqzmqThfJm8eO7/137FkCqoESaGHVDGAAAdNiDOMaHAKAkI1BEcAz06eRuaCMPGRY0IzAgDOtrGn+cNy+6ARFp3PYNl40ZM+betrYmZ0VF7UsalcpnzLhRH/r7+71/xgMu3v2vk8dW2UVUyoSCFgBCAKA+JkZPjsxsdvcPOKztGgHgSwD48ps3r71sqK9Wx7HkNn+tejRDEewOATgGffy0zM0E6M23a0Mbbnh/3sbGbnEVIVsSlIgON4hPCvBKdxYScVowARULhBAQZWo9OQmoLnlFd03g6Ik+YwLUcL5WS69kiXhNgIYbreEArA4JLXZR8tFwDC8A19Ipb8qvs8a88uWOFEIA3npLT6OjDa7r0AGACSAoUO2nYikRRZGIEoKvj6oVwPXeQBfqqt0BDMdqFX+tqChL7+8/CAoLcxfPmnXpYgCAioq8+4NDAs854wE33b2CSzJtFCQZAED2UTNMkD+MBIDc6e4e4D1oEwR3la34mAjmqrcTxMff2b4PAPbdcsulb945KyTSzwfuUjN0bqCWHQQog4PnZQ1Lhgb7cY8F+7GPbfzk5r0tVlz6cEz9L9HRJoEAwK8u4P0lYNRADcgYBAwlYHeKGOLP3fKT4frRLBCCBBARAWUiB/urG5yCKNmdInAadauKYWyCJIAoET+U5GAfNaicTinMx1cFvFMcquEIg0hDKCGhDIOhfj4MUEKAFwhIsigLIqW+PizptApyS7e8sbqNX/zUB1t2KmY0iTbJnmFxoW4tKxMcxbIURBFAFFFu63K1RxvIeFMa2tTX1OeMH9tEgkMC/pWelVSn4VRTRo0adk1DY3V3TU3rJ26mUqKUaEVBks94wCnMXmuXvW5EEJU4BqiaJaBWcxM8J8WxKV9XlS0AV+mA0PAmEmVI6N6wATYCwMbXHr0qbNoo9Y0BWlanYek1gb4cdfACyBKKQb7M5cF+9PLYT0e81G4f9fn9b1b/HB3tyjSPidH/+ZjNmhoAmAGEEBAEEQcHcFNYlk7xbAl16KaICgBUbr+DAAAHcOg4GQA5F72q4QABQUaXF4cIIEsyUIaCilNBl02gNgFSbZ3OuOo2xy8vf7YrD8BVLi8GAIii1XrSAkQexxACAIRKsmRts/ItAADh4aYBu09ACJHdflx2du6BJTPCz1k4eNDwrwBEQFmEutr6d6+55ppGACAZWckvhIUNnVBTU5PdZ8AhuvZ8BlpvAYPLF4Hs1taaaSN8W9QcDQMgoKLyjBM9lwcJQoxGHdUBgNvkXA4Ayxe9eFV4WIjmbq2auSfEj5sgSzI4eJ7317Azg33pjxs/G/FCY2fYp4TE/QRgQNd2RIJ0wmbmyJGH6A1UKFGEQzneCmDwSCIEADx37YkrGVX5l6Wuz0gIDgGBF7FLRrkSqJxqsbPJLd3OxGc+2pZ5eD64ursSw7GBpviHapZMRUBgGEIkHptjD7a1u57NwM4e8KhK/q+8vJRUf3+/mymlvnV1TaYLL7z825SUFG727NmiRqMao1Kpoa2z64M+OdZGo5E5qv/AwGKU9HpKDAb59w9v3BMawF2OANDSLSTd9krcJX+1jHtPtRpnzZulfXLO0LsHaZkng7XMBQgy8E6RV3OsirIUWrrEhJom6bmnPo3LUPyePmo7AgA4cuRIn6+eOqc4xI8bISNgh1UsUnHMAYZS0m3jQyQJNZS6SgOyDABDGCAMORTlIEoIkiyBhDLvw3HN3U5BIpTUA9IGmxXreUmqaGnmq15ftqvxKG0Pu96KYOMhUu7L9So1Y9Z9PC85xI+ZQwGgpVuKv/WV2CjlmZwRfBzx7FZ7SEEpkWyo0+lUTz/92F1XXnndT+zxB83VJ27v3vhLNFruntkXXPb0QOs1EA8uRskh0BSGZS6z2XlgKZ32/KPXhRBC2tw7A/jnBvtwqoter6eREE+jDAm2R2LhBwD48dtXr9YNDdG+ONhfdaEkSWC3887QAC7CV0WTfn3/5vfu+nfdhwaDQTCeiG83ciQQQkCSZdBqVKS41hm74MO4l076RAJX4ml8bhNRQObqmZDQp60FQgAff/ymYEpgkiBIqPVREV4UCzyfyZkAOEQEs9nMejQqJe6Nbxc3ZDLxJpPpJ0Q8dpM8d1wYJYSI+/aZ/zFt2sSlsiz5LV++/D1CSMNAAt2hkChe3C2IqudkBMHPhwmcOlxzLgCYTUYdhei/vmfmjlyRFZPzrrtM0r8+3PkrAJh+eHv+3UN85VdC/NQzbXYeCSA7NlTz9vqPR9ycVDLogehoU4HbxDxuLceR4KpBoiSgqlj0cacUsZHThwigCz/yucT0cJIYAJPJVeT1aFbXI/EUDyeeJpzQWJh0OgomkxQ+mEz1VTPBsiQKiMA5Bcw4E7mCqKio3p4bcft7EnssE1IJTcnNTXl71Kjhb/r7+0NxcemOiRMnOl3R4QMnME6nM8kAACV1/P6wAMaiURGtRkUhQCNcAQDm0D7uWZ3IoqdoK6NRx9x1l0l65K31P0N4+Jqf7x73dIg/81aADwnosDgcoQHchVdM0+wLeOrqu6IMO3f0BXSjRo0CSg5vZRGKcpQhQTTrI/qemW3og3r7CxIa7hrTYA4u9tFQsNkALHYBm63SQQBXD/ATBXE/1GxMTEwM9sG8RkXj/SEWz2w2s9HR0dKHH74SWFaWZ5o4cdybkiTJ+flFn02efN7cK664oj3G1WZ3wADOZWLr6QfLdjU6BEjUqFgqijKoWHKt6+EnnDLTxt0T3FVGLi+Pv+etTZ8dKLfNbu6GHQG+ao3VwfNaDkNmTfaPXfL69fdHGRJE7EM6kNs9cLGAtP/V5omMiXSTn3gtygAMpZzdgfWJFZ35AADEYBjwkcyEEMmdKQGIZtZsNrNucvGYyxU92l+LiooSt23bNu2eex7dPW7c+Ds7OzoAAUl1db0JACSz2cwOxF5x8THxFABAlMk6hjLAC6Lkw8KcD569fiwxgKzXn9rMCWUz3ayPYN/6z47i+S+vv660yfmhWqVWiRKIDEjc1JHald+/ecMLxF04qK/nZpj+lfTh8t8M8qv3XB6sZslFDqeAGhULThn3mkxJdveCMqABZzQamT17dlwbG7tqvCucK0qMiooS3Sw+IiLTEwDpUayKmJSUMP+88yYljho1aWZJScGOosKyZ4KDQsi4cSPvQEQSGRk5IAcoHlwrbn5d97Y2Cy8gAgT5caoRQXSeS8tFnI5Zi1GGBFdaEOrJfW9seq2kjn+UUIYliCjxojB5uPazJW9cd0+UIUHsa+Ir18+SrGL0EQwAkMkTAq4M8uNCZERBBgCrA2MB+h521k/NSAoAEBoaeM4ll8zeNnv2RUXVNYUplZUFizJzUu5JTIyf5OY+JE8AHgE4t0NHMjOTX58+fdI6lYrzLSnJMUyadN61l1957X+bmpscKhV7XUxMDAEAYjabB9yGucFgkFGvp4avE0rsDtynUbOMJErgq2Hud5lACdLpvBZCDJiyZBb3yLubfiistT+AlGUQgKAkiGMG+3z34bM3Trsr2iTpPfLulKcWHq5FSg9bGSzbvxAX4yKp0FdDdQwlQChh27sFS1Ets+10j/WpsiZZlmOaWlrNkiR3DwsLnTV69ORnZk6fsWrixDFFNTVFOcXF2T/m56c+snPnlnM/+eQT30OAc5uIck5O6jszZ059VxAkR0Fe6WcbNmz7LjY2dmhERISqq7NrV1Bw8Eye5wcRQoTjMDL9WMu5zEqbQP5HCQsOQRADfJnZi/XzzwPiiiQ5nYvl7IWpQsqSBdwTH279qajatoDjWFYQZSlAQ7UTh9LlCMBMz8sjR/sEHRUVGhkkDSIgEASuH7lwCEBItEla8I+IwT4qMs/mFGStiqM2J+74YNnGRldJdBjQESYAgFdccVXq8KETrtqyZc/k3LziiOLCrNeqq8s2CqJYHxISNG3ixGkPTp0aviwq6tKMm26K+kzhR+hhE1HuAqDAcaxm5nlTX3niiftrzr9gct1P/1tS7uvnO4sSgo88cvfykpKMtw4ciH9Ep9OpTg6fdRodeVdUBySX2tY1dzk6GEqpn5rQwZz4MgHAQ0UaT6PMXrhUSFmygHv8423fVTQ7F/lrVWqLjXeOHKS+6Lt/X/9ktMkkodFVQzJG7xrroUGaIEASAIgyAICKY/vNfla8y5yEi6do7hgcwAXKsiwKCNDugOUAALkD2Jw8yrQkiEgfffTR5nPPvWj35KnnfTh6dPgta3/bOjk1NfeSgoL0Z8rLS1Z3dXXXCoJYCAAQGRkJrEJXlpWlfSUIUrpKxU5gGGaKj49mNKVkIkPoaIahgZIswcSJk28CUN80YQKB556Ta00m01ZEIyVkYEShEHCVRyDRptaLDXNNYUHaxyw2Xgz2o3e+91TEOzqdqVCn0zGmk5zH1gfQiWjUMZP+nf7KJ4+FRw3y485xOp3S0BDu9Q9f0a0AnanLHX8JAAbw8+VCOIaqZJAFAsDYedGv3yxqLrqfDfJhnhQlCdUsy7V38aU7Csu3uCJPEs6I/FM3cYiISEwmE9XpdAQAZEKIBQCS3D9fzZs3Tzto1izZ/RmRVVyDW25ZaAOAbUef+OWXX/aPjLxk2NChYaPU6vrRhDDjhgwJm6rx1Q5xHaEbUAMV7Q5Rr7WJX4XaxYdZAAzQcqpxYb56QuAfaAQgpz+MHU0AUFJS4qzqmPhMoFZllkSUQwPUQ8YJjn8RAh+Z9RHs9Ol5CACg4eQhao4FnpcQEMBiFQcDeOS2/U2i10ewxGAQ//t/198a6q8+x+HkeX9ftaqzlf/aZMrj42MiWCUQ/EwRN/AkT83nAmAoAYhEQogNYmOhR5bSg8pklQIon3zySfdNN91eNGvWZTtnzJizfPr0WW+Fho6MnnXexT+5v3BArVgmkyvB9NWPdmR3W+V1vj4cZ7E5hbBAVvf5yzfMJh6NBE/rQhBtktCoY579YEtCU6ewVeuj4uxOUdYw+Mx9913rG2lIkNp3lFEAAC1DRnMsBXA3XeNYpl+YlDEuJpgMC+L0AIgcS5mmTr45v7B7OSIQxaQ/A0xJxmg0Mj25U64qb9ESIVEiIURyR2z9cVuAEIIeVKZysPsL9BTReCQYB/KomVzOfW2X+LHV4UpI0agoMyaE+RIAiE73t10WIAJpttOPnCKCKMpyiD83/MoJ3E0EACcP83MlO1KcQgkFJK6QWdIPSBOzS7vJK16/6YGwENVMu4MXfNQqpstGPv3w573t8TERDAE4Eza7kRAiuYP5EfHQXluvpucfANf7FxhkQqKPBOMAHrRok0kyGXX0uQ+3HGyyCEZ/rZqz2gXnsBDusu//PffxE914PplaDgDg6fe0ezosQiHHUZYhgEEqvBUAIPLmKRIAAMPQc2SUgLg7cf8NxbuOMiVdraleffzy4MHB9ENRFCS1mmUbOpzVuyrJN3q9nkYNcO2GiIyL9El5ubq6+JuUlD0XAwDr1mSHok16A1+fANfLBVBEZPpS/LI/Sm5uOCICKW2wv95hERwsyzC8IIojQ9UfvvHMNZOuetu1QX3aWb6YCAbAJPECjVNzLDhFkag4MjsiIoIls5cKT+gi/FQMnMcLkqucCfz9NLG7NZU8fVjAZyGB7FCHIEscy9Imq/TqDz9s6HZn1Q/kNZoAgPz0oqfVvn7ap0aOnPCvadMmJ9bUFqWVVRS8tXevebYSbeJOTCUKQP8S4BCRuPftwI1qaSCGeQG4Np9NJh198ytzaaNFfN9XzbFOHqRAH+I/c6j6R0Qgka59u9M6n+Pd/9p5cZ8oySBLiCyBEZePk4cBAEwb7Tfbz4cJFURZBuJy4ujfqOD07mDrL1+69rbRg1UPd1sFPsBHrappde56zLD5Z+NJKCnx92s3IyWE4D8uvO3ikODBo2tqCvObm5uLRgwbes64MZMM55wz+WBtTVFSWVnuS3v2bJ6umJ1/FnDEaDQybhIFo6KiRESE5GTz7Jqa4m9T0hMe8lS5A8q0jDbJRqOO+WGn7YPaFmemn5ZRd9tE5+jBmktXvTPvvShDgrhkyazTalrm5blTiexYbHNKMgAQFcuwc84ZowYAGBxE5vlqGEAAGdBlU/5dFfv1ej19++0E8dkHI8ZOGKb5DmRZYhlCO2yio7zR9gQiEKXExcAWHQEAGDJk0B3+/iFQX9/yxtixM87JyMyeV1qa+73NZq8bPmLYRePGTflkytSpOdU1BbtzclIec2s60ifA6fV66g7hcrMuRIqNNQ4tLExfWFlVsG/ylEkHR4yYuDAkcNCLAy1dx3PxAhNAQkKCWN0iPmxzoMByhLHYnMKIQdxrX796zf0LF6YKSxbM4k7XBSl1PuoarN1OQZYpJSAjEEGWBYiIYH3URMfzIgAg/XsHDkjM9DyCCPSyKf6rBvuxgxyCJPqoVWxNi/TyK4vMhSaTjhoGflY3IYSIc+fOVatUbHRTc1VnZmZBPADw559/2aaJE2c+tmNH4rSM7Lzbq2uLV4qC2DJyxJQr1Gr2Prf1R/sEOIPBILtDuOiBA3uuqazM/3727Fl5kyeHfzt61KhL7TZ7Q2lpzuLubsdjHmzMgCRQ9PoI9tnPt6RXtTme16o4FgARRFEaP1S7/INnr45cuDRVON0kilW2u4rSUwKChNKvBzLql0X63DokgBvt5CWJwN/LlMTrIxgSbZJ+fvumpWNCVZd2WXlHsK9aXdniMD72/qbFZn0EO9BNSYWrAAD48D39TaNGhYdZLdaNjz32WJuLrXdZfvfff3/X+TMv+n30yGkPbt4cF15QkP1IbW39q4fXpsPS0yQiiAiJifETQkKC7ggM9LsrJCT4fJUqGFpbqqG2tnJLS0vb6oMHs2Mfe+yxNjgDxOBK3mSjDFv/+/N7N82cGOazoKPL7vRRMapzx2h///iZqyKiDLuy+pqR/RcNNQAwwJjQQRzLMoShBCiVq231Ib6hc7iPRFlEJAT/Tord1RM9QVhpuPGNcUPVj3Z1804/rUpT3yEV7MuVHkPUUwCDdNwk1wGDOSR79+60NzfXJbe0tBrdfj0qEVZKdQS34mkGV1EpRT3KvQLOnaYjFxSkr5gy5bxLJakTmppbi+22+l9qyxvXXHnNNVlHUaU40Cp59SRRhgTJHfb1+K8f3DR23GD1de1dTqe/Dxc0Y5zflrefiLo2ymDOPdWgU+pkhoYwwzUqV+8HSaRt/7jO/4uwIG58R7dTVKsYVpIQpNPf/4mkLJnFzl6YKqyImfv0hDDNO1YH7+RUVNVll9sza4Xbv1q1pes/Ey+ixHBmtMrxmNub3T8UPDK4Pay7Q+CLj49nIiMj5Z5wQY9BgYIo4tKGpuoN6emZty75duXMCRPOeevKa67J8twOcLOUZ0qjdASdSUZEXP57oa62Xdgd5K9WWx28M0BDhs2ZHLD9/cevvDDKkCCeSp9OKffAMXi5Vk3BzksiQ+XZYQHsgx0Wp+Sn5djGDjHL6hTrWYZSPE2g0+uBolFHZy9MFZa9cd2L40J9/+Nw8jxLCCvK1JlZ0fmPtxbF5RuNOuZMqcZ1tGnp1mTycQCKHnlwx2cpFeTOmHHBimFho+dfeGHEeoPB4FCyV4+1HeAuFcYM7NUMMCaGkK0HSrp+3ma/pa5dSgzx16itNtHpx8Gw8yYG7vrspevmevh0J50fjHSHR3EsuUOSZAAZiUbFMICy5MNR0mGTWpOrxLtYlrUQenocZ6NRxxgMIJNok/TLe/PenjLC71On6BQpAUaQCc2rsd/76qL4rWeK33YsTXcyeIreQlKO2NzuDbWuDGZX15Dj7bT3f38O5Lf0emrasaNzYyFe39gtJQQHqNVWp+jUqonfjFE+m75768aFUe7mjCcz7lLRDp+/NPfaED/NuVanKAMhjCQjEkoQKUsr650Pfb50W4EsQyDKpxxuRAGRbu7E0DUfzfttTKj6TbvTyTOEMILMMHm1zruf+Xjr2iULZnGn3r8d+EJ7UaHH3dxWNNrtunm3t7U1HEhO3hNNCJHdgZ0DGHQGWa/X0x9+2ND9yvrieTXtwrpgfx+1IEgCAzJMG6751vjhvK+HDp2ljTYdCgP7q9ruUMm60YOZD1hGBoLEFStJUPTXqtnyFmfME59si3355Uf8iasP0inVauAuCbHoteui7r96atLIYNXtXRaHU61iVU6JOPMa+Juf/Xir0ayPYBcuTRXOJGAo+W5uhcMiIms2m1n3njQ9Oij5LwPuGOwl6cnfkyVpZnBw2IXnnx/+a1ZW8gvR0dHSmQK6/N15ltv/b+PtRXWORWqNiqOA1OZw8mNC1I8veWHk3k9evPEi98qOfa1B0jPzt4CNMiSIK9++UT8iWH2B3SFICMBQACHAT82VVNuWPvRWnAH1QCsquk+ZJtHr9RTd0SG3XHqp/6/vz/sofJhmR7AvM77T6nAE+qrU3Q6oza5wXvXch5tjTw9z+7eYkOihcERCiBgVFSW696Rlz3w4Nyj7hCXyJ5BPD7dQdQFw27a1ocOGjbht9OhRHyCSoMLCggsvvviq1IFeIt11j0AgRk+IwSB/+/p1D48L9VnspyZai11w+PmoNBa7KLVY4IOP1lR+mJWVZUW9npry8khfmzHq9UBjIiMoiUoQv3jpusfOH6ddKguiJCASlgJqfTRMWYPjP/e+telZZXLrdDqfhy5yFPuoYIRaxUBLt7Ru/ssbb3OzrNKfBVrM9DyifP6HN2+ePySIfBzqz03utjolBJAD/dRcXbuwd09B932f/WCuPBPBphQ4Xrfu5+EzZs78kWHYLpHnq0RRrLTZbA2yLNe0tLS1dHfzzdHR0d1wgpZGnzdzt27d6puSEq917zO49yEOmZpNALAkJWVP96xZF64aMiT0FQCI1ul0Z8BKBwhgANfk2rb8i5duTJ8ynF0+KEB1XqeFFxkKZPxQ9Rvv3j9a19Q9/E1iMJgUoB6nGSMx6yOYKEOCaDAkyEvfuuHJcaHqxShJMi/LoGIpZVgOCuudbz6k3/Quop7GxJx89u8w0AySAQAWvTL3/NGBrD44gJnPUIQOq8Pho2I1MlKmrNG+6O434l4GgENNF89AN4sBAHHixMmPThgXfq3F0gB+fv4A4AcAPEiyDTo7usHpdHZWVxe0SJJcq9VqmqqqavfNnn3Fl54K6U+ZlEqFrrCw4Jv/9fhzefv3J9yLiGA0HmYk3aQJ89VX36+tr69s5VTsdXq93s8jAW/AL3zuysbs85/GZbxtVF9a3Sr/h6Ecq1GzTLfF6QzU0imTh2qN6z65ZcdXr143j5BD1ZfRrI9gjTodgwBEr9dTt8+HUYYEccFtVwz75b15y6cO0yymsiQ7RVny81ExvMQ4c2qsDz6kj33XrI9gCTGg4STtben1QM36CBb1riYiJNokffTyTZPWfHTrt1OHqQ4MHczO5wVBcDglKdhPrbHyUJZba73t7jfiniMEBL1eT89UNhIAJEQkjY1NP9fVlZUyjFrOzs5en5d38OWy8qLVtbUN+xwOZ6Usy/6DB4VMGDNmzJWhoePupJRY+mI1HhcMSjOPrKwDb51zzoWGwsLsF6dMOecLAGAIIeLR5ykvzynVarVjGhstw2fOnNk40Bp/9EUjKPGBP7x50xVDAtlPB/mzcxw8D4Io81qNSiVKCO0OYXeXTV7yW0rnxg0b9ncffZ4H50cEXT/H9/5ADX09xJ8Ls1gFAQmSQF8129gpFmdWdj/05lfm/UeZbQRc3VhO1KQker2eREI8jYyJP4IE+/zla2aPDNI8FailukAtq+2287KMKPn7qLlup4hdNvxmS1rzm8tMSW1K00UAQDiDxaN/9/kXXzwzgWU5Ydfu3TfeOk+XDACg0+lU8+dfP2zMmNHDBg0aNLbTYg25ZM6VX58kkzJeuYwmABFF0c65+2Id0o4pKSnshRdeKKxbt25cQEDACLvd1r1zZ6rzTHwYbrARdLUj3gMAl618Z97CUF/yarCfeqTVwYMkoRCq5a4M9SVXLriSqbj/0lt+a7EKv+dU8AXjh6uGjwjm7vbnyAPBvnQkL0rQbXVKWo2KQ6BQ0yb+uCOz7YVvft7b/md9JMXvjId4Gjl9CJJok2QwGNAAIIOBwCcv3jxuVDBe7aPCu1UcvSpIyxGLzYlddonXqjmVhECbuqXtde341hPvb0xSWEty5mq1owkT2Ww2s1FRUel79+6874JZ56+/4pI55ri4tdfdeOPte41Go0AIqQSASnAVC+r7uY//8PSUEIO8Zcv6iRddPCvfbrfXrVzx28xXX32182jtVVSS8fukCeG3lpYWx06cOP3m49mzA12MOh1z1xpX74An77960MUTVM+FBqofDtTSEbwgAi9IAstQTqPhwGKTwMpL7RyFoJAAFeF5CZyCKBEChAArOWU5q7FTev8RQ9xaRCSb/3ODyqfNLilL3vTpQzA3t4lMnz4E403gc9NFjgJFw7VaxPX+3d13hk4fQs+JNvE9qB/6zRs3nROgonM1GrjJl4XZAVrWR0YEu1MQCQDx8eEYQQTosEmJXTbhgwf0WzYCuFoJQ7RJJme4VuvNuktM3PXixRfP+bSuvr4h5WDeFfPnzy9JSVnCzZq1QIqPd9U67WutVtLHL2YIIVJ27oFvZ4TPXlhdXRzf0NDywpw5X2bNmhVMv/zyzlkjRgx/a9ToETe0tbVjTXXdnNmzr0j59ddfBzxL2TczM4I1uDXRCwvmDT5vJHk0WIsP+2u5KSqKYHdIgChLhBIGEWQJUCYAlACAimNJYyfGZlVZ9e9+uzO9r9+56bNbqnw4HKVRM9DYJZtue3lDtPLeaw9fHjp+XOBoLSGzVRxcrGbgIhVLpgVqORAlCZy8KMsAspqhrEqtgk6rINoFsqWtW/r60XdiNwMcbiVsOAPDtHpjJ49+3d3FVMjNTflPePh5T1fXFOfHm82RDzzwRJOne3HSNJwH/U9M/zVpZ90wZcv48TMua22tAafdWQgEqFarmRQUPBTa2hodOTk5T0dE3PD9ma7d/jBGACTezTq6XpmoXvH2lKsDVOw9Kk6+zk9DQ1UsA6IkglOQAWWUkBAEQIpIiAyAgoCNQGmhIMiNMkKVKEIrEGyRUGpnkbW2dDuIIEhoF1n1zHGaH7QqMogQgl1OrOYF3KOiMJgADKMMjFGxTKCvxsVrCYIIoihJSICoOYZyHANWB4Kdl/ItgrSuvp1Z9cLHG3PdrCz8+uvAz9L+M2DrCXQefRIhPz8tdurUmXPLygv3rVxhvCYmJsbpNkHxpALO86L0+iXaB+6/zOAf4Pugn59vKABCd5e108kLW8vKqj6MjLw2HY1Ghhyl2c6EPbm++k/xMZ7AA3j+0etCZgxjLvX34a7XcBDJsTAtQEsZSilIkgyCKIEkIzCUAEMpMAwBSggguFrZyoggywAywqHWtpIkASIgAgLHUKJRMyDLCJKEIEgyAKJECBCGoVTFsoAA0GUVBR4h3cZjvEVQbXhM35qs1IlUenZHm84eoHnKPY/fE/zzNz+3H4soi4mJwaVLlwZcP/eK3WNGT55ZUpa79n8r1+piYmJOqO99n/fhPFYAm8EAL3/99dfvX375BeNFkdLq6pKq+fPvaVSAdTTYlMyCM42x7HmcAN2TmBiNOqoDABJtagOAWPcP+eKNG6cOc5ALGEKvUDF4MUthnJqjARzHAAUAGRF4SQJJQpBRlgkQRI/F0b3kUkKBuOvLgCDIwDAEOI4BFceCU5AZKy85JAFKJFE8YOfJvs5uec/jn2wq9rxes97Vs5sQg3y2gUyxwtLTD84JDNIui77+qkvnz3/UcrTWMhgM8vTp05mFCxd2rl27+vYAf/8MjuPYE1VaJ3zwUSr2aFApCXg9PrjYzWuumXfDnTvOxtVT2QQPzW0iPbGO7z1zVdjoQYGjKZVmcIDnMpw0hhAyVkUhDCgJAUQ1BQBCKLiKHCIgosxSxiLIEhIg7TKQDkkiDaIoVYkiFlt4yOlwQP7Ln8VVedL4igZuzhuC0aYzn+LvCzdRUJixfsrkc2/JzU35ZMaMC//PzVD+4TkpVtrOnZuuYhiuPjLyuvwTdZ3IX7hYYjKZKACATqfrMXXBaDQyOp1OTkjYMWPGOVMzautr/nXujEu+QzQyA6UfwalQgnq9ngDE00gAuOrtBPFYKW333Xet75QAbjCiReuvUoHKXwU8zwOACuxWpxQ2fHR7bmUZbtk7pCsvz8Qf6wuV8hDxECmfLSRIn1hmN4D27dt2dXj49DhREmlGevqc6667Nf3XX3t2gf4MUXJaVxAAgMLC9A2IiIVFmb97vu6Vw1rHqNMxZn0Ea9ZHsOhq6XSipiwguqJYzPoI1mjUMe6ursQ7wsefo9nZiV8hOqWiokwzuAL1md5M0T9bs5Q9lTdCCJFSUvZGDR8+7Obm5or2yorqfx8m9bxypN/3R7ICAYirRZW+x8/FxLj6ZLvABuj2w7wa7ARYSQDA77//3j8wMOgyUbTQUaOHRx44sPshQshyZQ73wGfI/fGmKADQsrKsZES7lJOXss79Out95F75O8Gm/FtUVKQGAMjLO/A9ohPz81N/b2urcjQ0lNYbjcYQj7y3gaGmDx7c84DN3oSdXbXY0Vkr7t699U4AV0C0K+DZzJ4hwc1eGViiNBOFxMT422XZhuXluQcBgM3NPfhfRMSsrOT/KnO1368giEiNRqNPVXVBKc+3Ynp60gfNzeVtrW013atXr57sfd5e+ZssLtixIy68tra4pKYqf2dubsoHDQ0ldR0d9e1ffvTRJMVirK8vybNYmnH79rgLFXJlIGi3VxBFLCnJ3AcAkJGdeIfd0S5VV+envPLKh4Hbt2+aXFFV8M+tW7cOUdgf77TwyqkEHCKS7OwUkyx3IaIVESUUhDZsbCzhq6ry9hUWpn+WlXXw2s2b10bbbO2O8vL8JE+w9jtx58WRVRtWDa5vKGnr7Kyz/PbbbyMBAEaOvNgnL+/gZqezBasq8yvr6ooREbGsLCf7p5/iAgaMveyVAe2/bdmyJcRs3npeTk7Kg6Wluf+prMxNrK0t7BTFdkSUENGBdTVF1vq6Qpssd2JmzoEnT6ZpeVLt05iYGEIIkfMKUl8dGjY6+ODBpGUTJow5r7q68COGodcHBPoNQgAYNDhkdFdnd31leW6S0+lMVastakJIlxdwXjmV4t4rbnP/ZADACgAAY5wxdFzo0Mm+vv7n+/io56hUzBzKMGPb2ttlh9VaCQDQ3Nzcv5h1xSTcuXPnhIamUuzsqMbGhjJE5BGxG+vqi9vLyrO3FRamvbZ//+7L9IsX+3mngFf+Lo7Bo5tvj4v81oSt43bt2jqrXy8giEhzcnL8yspzdjQ1VXTW1RVvLS/NeS0pJf6KL774Iqgnu9qtqonHYHi1nFdOOwhd5e/M7t72R77Xry/cre20y43GoT07rodq+5HeHNyBXmbPKwPb+nTzEXSgrRwUEY8LMEWMxu9CVq1aFez5ee+z94pX+mYnn1CuHQBAQWHGrq7uus6amuJtiYnxN3pB5xWvnBqAMgAAeXkp31osDU5J6kCeb8OUlIQ3XZrPa156xSsnXZYsWaJNSNg8LD19v66lpaJRFDoxIyP1dk9QesUrx3BdvGRb74Ok73WQdu/edrXN1ipWVeWXffb88z5/tnmCV858sHlH4S/4fnq9nqakpHAAAOXlOUmC2I4JCVuv9JqWXjmW7280/hS+cuVKX++20jHks88+8zGbt0z8/vvv/XsaIKUnXUVF3q+IvFRQnH0XwACI3PbKaRMX8w0kJWXfTZ2dDY68vNRV7rlzShplniw5repYiUYJCwsbMnnyhPzLLjs/gRCivE48Vy1CCCEEZoqinba2tVu9U8wrnqLT6ZAQQFEEuygKOHXqxHsyM5PfIISIKSkp3oXZBSZXjzm9Xq+qrS2p6eioF1auXDkOACAnJ0fl3ulnAQDS0xMfcjhasbqmoHP58sVDPcHoFa94uhi7d2+9s7urHu2OZkxKMt/voem8opiFOXkpryEKWF6es/vndeuGex6TlpZ0Z2NjeReiHXPzUw2KmekdPa/04H6wAAAZqclPW21N9uaWCue2bXERXhfksBB3ERZVRUXe74iIDY1ljXl5Kcvy81M/KCvL3tHZVYuINiwtzV6n1+tZNxNFjnaYDwegHgpE9TJWZx/gGACAZcu+Ht/QUNokyZ1YV1fcuGbNqkmeWvBsHyTi9t3YvILUL1taKkREEV3iwNbWSmtZWfanERERbE/MU2/Acj8Ar+l5dswjCgCwefuGS5pbKls7OmqwtDQ7H9GClZX52UuWLAn0MpceoFN+X79+/cSCgrRH6+vLXi4oyHh0y5YtE93ECRwLbE888YRfdvbBB0pKcv5bUJC5orw874X4+G3Tejq/VwaeFaTUvekNbIhINm5cM6mhoazVbm/F9JTEhz788MPAsorcPEQBCwvTNysWlXc+HDYvmb5qKgVse+M3X9HQUJqPaDmUpYsoY0d7DRYXZS568MEHNT2B1SsDc470ZkoWFWWuRhQxKyP5RQAASgmsXLlySE1NQS2iA/PyU77vTxzA3+1UIiFEcjVLiKQAkRAP8RAfEy/3VEqdUiLv2LH5golTJ28OCxvqW11dVdrS0v6DIEvF/lrfCUFBvk9MnDTzmTfffHFyQEDArQAgICKc6f0MzrAFGDZu3OgzZdq45zvbW+PmzIlM76mcOKWu+aHV+pwrSRap22pJBwCQZYRp08bf7h8QENLe3mzlRXG3Un/SO7wnaK/r9XpaWp6ThOjAsrJc81dffTXI85hly5aF1tQV7UaUMCcn5a3+tLJ55fiisIrZmQdeRkQsK89OXrBgAacEQvSk4QoL0/6H6MSyitz8wsLMxwoL0o2IPDa3lNdt3775kuP5/F7pxXw4eHDPtXZ7CzY0lrb89NPSkQDK/h0ySlHPrVu3zujsrOerq4ualyxZEuj15waWX4+IZMeO9WFVVQWFiA7MzEx83/XekRS/EotrNseNrKkpqEB0IqKAiDxWVxckGDcYR3uC2Csn9iBcK1/2gQ8QJbm4OPN7z9ePIkpIVVV+vs3ehJmZ+y8A8FLD/dV3Jz00UDi8mb0roq29RurorBX27DFH9vQcD8dSGkeUlWW9V1qavTwlff+94I6g8j73vwy45G8RUU5N3WNwZ5H/AXCzZs3iKqtyS+2OZszNzbjIO/AD0LR0a7OUlH0fITqwqjq/xE3x0x5MS9ITy+Ktc3oSAJefn/YsooiFhRn/c7+uOrTK5RhVAADx8dsv7OioE+vri6xG448joA+Dj9i3EhBeOTkmo9FoDMzLS/0yLy9Foe1JD8cxCxYs4EpKspMRBczJObiyJ9NSeb7uwAfGsyiVV/70g3IBZsuW9RObm8v5rq76roSEHRf0ZKqUlGbHI/JYWJjRp9ZYXof61ALM7K6EpQALEcmyZctCq6oKrIhWTE9Puqmn56RYJfHx26Y1N1dY7PZW3Jdk/kdfnqlXTiJxkpax71VEBzY1VzQmHth936JFiwJ0Op1q166ts8rKcmNluRMbGoqtm9evn67UIOzFj6Auf2H73N9++22Il2A5hU6ba19UKYsISUkJzyE6sLIyN9HzWRxhWrqPTUnf86QkdWFdXUlLbGzsUO9CeZpEWfXS0va9a7M1I6INa2sLW6oq8ytbWisQUcDm5grr3r3b5/f2UBSTBQCgqCDjVVFsx9rawsKioqIAbyjQydFsAACvvPJKYGZ28hv5hekLPN0DIxqZjz76yL+mprCc57swPn5b9LE0l2JCFhSkb0SUsLQ0a98nn3zi21N8rVdOzcOkAABbd266qrq6cH1DQ2lnW2sV39RU1lhdXfTzli0bz+2NKPEEW3r6/k8kuQs7O2uwqqrAsmzZslCvljtpftroiorcKkFoR4ejGSuq8vbs3r0twvM5FhamL0C0Y2FRWvasWbO4noPUXfT/+vXrw2prC1tLSrK2fP3118HehfG0PtTDYFq+fHkQIg4xGo2Bx/PLPE3MvLyDXyLyWFSUUV9bW2irqSmqX7BggdYLuOOLi6SAXgv5Llq0SF1cnHnAbm8W6uuLHYhW7Oqqw7KynO9iY9eMcZuMmurqggpB6MZ9+1z+WU/7ZsrzWL169VhPK9X7JE6zeXk0sNwRCfTYk8TVlbWgIO1bRBGrqgoLv/vumzl1dUWW6prCSo+HSPrw3YdSg/pa6PZMMhf74m/v3Bl7pcXagA2Nxa1pKbu/bWws60KUsKmprKWgIPUVAGCysw/chchjeXl2ljuqhPb2ve69Oy/Y/m4TpreJoLTRAgCak3NwNaKINTXFFf/73/djzObYod3ddVhalp0PALS3B3q873ED/oxn0Fav/mHU999/79/b4qSMQ1l5TiyigElJ5td+/3312MrKQqPV1oiIDmxsKE3Zvj12fllZdrYkWTE1dd/dx9JyynP0gm2ArMjh4eGqiorc1YgC1tWVlK5e6+rEun/3rsuczlasqSvcczxzVPl9z57tl5eUZD6bnZ34flFRxr8SE3ddNHHiRPUZPo4MAEBW1sH3LJZOS1JS/CPgTqPp8Xi3D52SknhRZ2e9XF9fYl21ZNVg12v7rq+pKUpCdGBnZx1WVOTWW63NQlV1Qc7+/fu9JREHuvZbterr4Pz81K2IAtbWlZZt3LhmkrJiJicnXI9owcKiDPOxAKdsnK9Z878xJSWZu+z2JndakOtHFNuxtq6oqqQke1la2r47t27d6tvTpHFlpv+9sX2em8TuDX/GnTlPjjOWrAtwia8iIhYXZ63xBGJvvnZxceZGRAkzM5M+83ibzc1NebyhobQOsQtt1ga0O1owJWXf9Uf76V4ZQH6eTqdjsrNTfkZEbGgoLdu2bdskxWEHAMjMPPgPRCdWVxf90tMEUoiWFStWDCovzylCdGJtXXFNSVHOu+XlhQ9lZx98vbw8J7aurrgbsRurqgukn9f/HOYJ1BO5XrPZzOIpCks6nrnb2/Uq78XGrhnT0lLprK8vbjcajSG9+XVGo5EhhMDu3bsu6uyslxoaSjs3btw4wl0SgwIAfPnll2EFhenfVFcXJeTnZ0fFxcWpvaTVwBWi1+vp3r17h+fkpD6flLQj/LCv5S5mlJP6FKKIubkpq3sGnOu43NyD7yMKWF1dmKO0UfaU5OTdjyE6hbKyrI2emlKZqGvXrp5cXJjxQ0ZW8gtGo9Hnz5ARfxFsFABg1apV48vL856rrSv5pb29ekNtbdGK/Pz0h3U6nc/xQKeco7Qsa48s2zE5eU90bz6X53jm56duRJQwLy/to8Pa3hsxcsaLMqEUE6mwMD1GkiSxrCL3C8/XFcAiItHpdKqyspxSQejCAwf23A4AUF5ernGbYmpEJKWl2asQRczKSnrK8zwKkH766aeA2toiG6ID9+7dcYWSUmQ2m1ml0vTu3bsuqyov+YfSefNkgdAjYOCZ5uYKK6KMPN+GXV11iGhHRDvW1RWnbdq+aXJvoFPuKTMz6WVECQsK0n8+nuZUtFxc3G8Xt7fXYltbNZ+RkTzZ9TkXmaXku50NQeZnRa6Qa+LGMwDxMiGGIzKHCZLhlFLGabfLPYCTEELk1at/HOWj1Yxpbm60tLR079Hr9XTs2LHOsWPHAiFE/P777/2vu+7y69s7auXa5vbt7o/LAK6+0u5UlK7ikszfALi7fX19LyWE7AGAQ1ntn3zyie+YMcNNo0ZPGNbY0vgqAKSCK81E+qtmJCFEykpPemrGueGL2tra5JKS3Lfb21vX2Wy8ZdCgoAtCBgW9PnzY6POdTmGT0WicnZub2+0u9HR0lrQMANDY2BLb3lH7oZ+f77WLFi0KIIR0GY1GJjQ0lERGRkqen4uOjpbcGdtJefmp64YODZsgy+jjQfOjxz1KXpVwhhMqublpk2pqylZlZSW/cPRqrZhQ69YZpzQ1lctVVfltH330kb+SJqI49qmp+64WhA4sK8tNB4CeUkjchMOBBTzfibW1RXXl5XnG0tLstwsLMx/avXtXRG5eyqeIdiwry/GMLfxLGk7RVKtXrx5bV1/s6LY0SImJu+46+ri4OGNodU1BCaID8/NT3+hB0x9t+pKKirxkUbTggQN7bj5eXKMy1hkZGb4AoIyv10/zmpo9+1X6JXptZVVevdXaIm3ZselaAICioiJ1UVGRGhFJQVHmp4goFxVlvdvTZFUm5JYtW6Y2NpYK7e1V2NlZi64iSAJ2dddja1sltrZWybt2bb3M0wz8a4uKWTEBn0TksbQ0a6f7ejij0ci4GEtXpnxa2v5HEW1yWXlOJgAwx6LmEXNUiMjm5BzQI8qYn5/6vevefh+VlXPg4f37d4w4njnsJUXOcqD1Tm0r+08HPkQUsaq6sGD79k3neB5TXpmX63S24b59CVe6PvMHsCgb5rSsLCfPbm+WEhK23xG/d8cVOTkp95aX55bIsgWzshK/Ox4JcYJa3BX4W5T+H0SUs7ISP1HaQB8+xuVH7d0bN6G5uUyqqy9u+/3334OOB4wVK1ZM6+io5WtrC+tLS7M3NzaU8YiImZnJhmNpSJemO7s121lf78FgMMgGg6G3Q2REpJ9//rnB11cze/z4aVdr1NzBktKsle1tnT+3tXUJwYEB0xqb6uvXrt2Q6vZcjvYH0T3WoiAI2zSawdNCQ4PDwsNn/7ZtW+y0Sy6ZM6SxsbEhMTHrNbc2PNm+DA8AwDAsC3+oXhWDAACSRGyCIEmUMuyECyb01NGIxMfHMw5H15TRo0dcExQUMJ9S4IYNHzpUFMS5LS1tBRVVBRusVsd6N1D/cA/e6mleOQHSBeDBBx/U5OSkftzYWNbhKlrTjbW1xbwotkmFxRmremPsDuV2peybh+jAkjLXxnFpWfZ+RAH379n14PEYvz9rUqan730M0Y7l5TlJrtdTOCX+Uym+tGfP9mscjna5qiq/ZO7cuWp3uZFDHY0IATAavwuprMhzICJarY1YW1eYVlWVp9+/P2GOh2/mFa+cPNABACxevHhoRkbi3dXVRcvr64srBbEdMzOT7+nNHFQ+//XXXw9pairjKysLsrZtjtUh2rCkJNN8ssHm6ZeuWbNmTENDic1ma5LS0vY80JOlU1qaHe/aJ0v90hOsR19/Ts7BL4qKMt7ZstOVBnW0Catk53vFKycDdX+oFH3ffS/6pqTsjYqNjQ3uA1lAAQDKynL2tbVVOaurCpvb2+v4rVs3ziCEnJJiR4dz/5LedmXKl/PZ2ckxZvOWqWZz3MjExJ03VlTkxiMKWFVdUPXzzz8PVzrRHu/crixuV7YEeFlHr5xKbfdnMgMUIiEnJ+1NSe5EQWjDtIx9H51MoqQnXLgBQbNzDnxjsTQgog1bW6uwvr4EJbkDES1YU1ucuHv39kMb0r0tGt5ORV7S5LSK52atwj4CgHw8UsBkMiEAQEND09bQIcEv2ezWlj0J2947RUTJIYxER0fL7pLvj6enJ8UOHhxyL8dx4YiE1tY05juszg2Tp838FQDEnsqKH3XvMrg3wL1y4vL/ECq9lJRIdjAAAAAASUVORK5CYII="


# HTML embutido
HTML_INDEX = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Painel de Oportunidades v7 &#8212; Silva Pinto</title>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;500;600;700&family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600;9..40,700;9..40,800&display=swap" rel="stylesheet">
<style>
  :root {
    /* Paleta oficial Silva Pinto (manual de marca) */
    --bege: #D9D7C5;           /* fundo principal */
    --bege-claro: #e4e2d4;     /* cards / superficie clara */
    --bege-escuro: #c9c6b2;    /* linhas, divisores */
    --gold: #BB904C;           /* dourado - acentos */
    --gold-light: #d0a866;
    --gold-dark: #9a7438;
    --gold-pale: #ece3d0;
    --cinza: #7A7A7A;          /* cinza medio - texto secundario */
    --preto: #1A1A1A;          /* preto - autoridade, texto, fundos escuros */
    --preto-soft: #2a2a2a;
    --cream: #D9D7C5;
    --navy: #1A1A1A;           /* mapeado pro preto da marca */
    --line: #c9c6b2;
    --text: #1A1A1A;
    --muted: #7A7A7A;
    --urgente: #c0392b;        /* SEMAFORO vermelho vivo - so as bolinhas */
    --importante: #BB904C;     /* SEMAFORO amarelo = dourado da marca */
    --naourgente: #2e9e5b;     /* SEMAFORO verde vivo - so as bolinhas */
    /* acoes e chrome usam SO a paleta da marca abaixo */
    --acao: #1A1A1A;           /* botoes de acao = preto */
    --acao-ok: #BB904C;        /* confirmado = dourado */
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'DM Sans', sans-serif;
    background: var(--cream);
    color: var(--text);
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }
  .serif { font-family: 'Cormorant Garamond', serif; }

  .mockup-banner {
    background: repeating-linear-gradient(45deg, #1A1A1A, #1A1A1A 12px, #2a2a2a 12px, #2a2a2a 24px);
    color: #fff; text-align: center; padding: 7px 16px; font-size: 11.5px;
    letter-spacing: 0.4px; font-weight: 600;
  }
  .mockup-banner b { color: var(--gold-pale); }

  /* ===== Header ===== */
  header {
    background: var(--navy); color: #fff; padding: 16px 26px;
    display: flex; align-items: center; gap: 18px; flex-wrap: wrap;
  }
  .logo-circ {
    width: 44px; height: 44px; border-radius: 50%; border: 2px solid var(--gold);
    display: grid; place-items: center; font-family: 'Cormorant Garamond', serif;
    font-weight: 700; color: var(--gold); font-size: 17px; flex-shrink: 0;
  }
  .brand h1 { font-family: 'Cormorant Garamond', serif; font-weight: 600; font-size: 22px; }
  .brand .sub { font-size: 9px; letter-spacing: 3px; text-transform: uppercase; color: var(--gold); opacity: 0.85; margin-top: 1px; }

  .search-wrap { flex: 1; min-width: 260px; display: flex; justify-content: center; }
  .search-box { width: 100%; max-width: 480px; position: relative; }
  .search-box input {
    width: 100%; padding: 12px 16px 12px 42px; border-radius: 28px; border: none;
    font-family: 'DM Sans'; font-size: 13.5px; background: rgba(255,255,255,0.96); color: var(--navy); font-weight: 500;
  }
  .search-box .icon { position: absolute; left: 15px; top: 50%; transform: translateY(-50%); color: var(--gold); }
  .search-box .hint { position: absolute; right: 12px; top: 50%; transform: translateY(-50%); font-size: 9px; color: #7A7A7A; background: var(--gold-pale); padding: 3px 8px; border-radius: 10px; font-weight: 700; }

  /* ===== Nav principal (3 lugares) ===== */
  .mainnav { background: var(--navy); border-top: 1px solid rgba(255,255,255,0.1); padding: 0 26px; display: flex; gap: 2px; }
  .mainnav button {
    background: none; border: none; color: rgba(255,255,255,0.6); font-family: 'DM Sans';
    font-size: 12.5px; font-weight: 700; padding: 13px 18px; cursor: pointer; letter-spacing: 0.4px;
    border-bottom: 3px solid transparent; transition: all 0.15s; display: flex; align-items: center; gap: 8px;
  }
  .mainnav button:hover { color: #fff; }
  .mainnav button.active { color: var(--gold); border-bottom-color: var(--gold); }
  .nav-badge { font-size: 10px; background: var(--urgente); color: #fff; padding: 1px 7px; border-radius: 9px; font-weight: 800; }
  .nav-badge.cinza { background: rgba(255,255,255,0.18); }

  /* ===== Telas ===== */
  .tela { display: none; padding: 24px 26px 70px; max-width: 1240px; margin: 0 auto; }
  .tela.active { display: block; }

  .legenda { display: flex; gap: 16px; margin-bottom: 18px; flex-wrap: wrap; font-size: 11px; color: var(--muted); align-items: center; }
  .legenda .sq { width: 11px; height: 11px; border-radius: 3px; display: inline-block; margin-right: 5px; vertical-align: middle; }

  /* ===== Split layout (Op&#231;&#227;o A) ===== */
  .split { display: grid; grid-template-columns: 1fr 310px; gap: 22px; align-items: start; }
  @media (max-width: 840px) { .split { grid-template-columns: 1fr; } }

  /* ===== Ficha viva de concurso ===== */
  .ficha {
    background: #fdfcf9; border: 1px solid var(--line); border-radius: 12px; margin-bottom: 16px;
    overflow: hidden; box-shadow: 0 1px 3px rgba(40,30,10,0.04);
  }
  .ficha-head {
    padding: 15px 18px; display: flex; align-items: center; gap: 13px; cursor: pointer;
    border-left: 5px solid var(--urgente); user-select: none;
  }
  .ficha-head.urgente { border-left-color: var(--urgente); }
  .ficha-head.importante { border-left-color: var(--importante); }
  .ficha-head.naourgente { border-left-color: var(--naourgente); }
  .ficha-head:hover { background: #e4e2d4; }

  /* Selo de prioridade: so cor (semaforo), sem texto */
  .stage-dot {
    width: 16px; height: 16px; border-radius: 50%; border: 2px solid rgba(255,255,255,0.7);
    box-shadow: 0 0 0 1px rgba(0,0,0,0.08); cursor: pointer; flex-shrink: 0; padding: 0;
    transition: transform 0.12s;
  }
  .stage-dot:hover { transform: scale(1.2); }
  .ficha-tit { flex: 1; }
  .ficha-tit h3 { font-family: 'Cormorant Garamond'; font-size: 19px; font-weight: 600; color: var(--navy); }
  .ficha-tit .meta { font-size: 11.5px; color: var(--muted); margin-top: 2px; }
  .chev { color: var(--muted); transition: transform 0.2s; font-size: 13px; }
  .ficha.aberta .chev { transform: rotate(90deg); }

  .novelty { font-size: 10.5px; color: var(--urgente); font-weight: 700; display: flex; align-items: center; gap: 5px; white-space: nowrap; }
  .novelty .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--urgente); animation: pulse 1.6s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }

  .ficha-body { padding: 0 18px 14px 23px; display: none; }
  .ficha.aberta .ficha-body { display: block; }

  .linha { display: flex; align-items: flex-start; gap: 11px; padding: 12px 0; border-top: 1px solid #c9c6b2; }
  .ntag { font-size: 8.5px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.5px; padding: 4px 8px; border-radius: 5px; white-space: nowrap; margin-top: 2px; }
  .t-gabarito { background: var(--gold-pale); color: var(--preto); }
  .t-recurso { background: var(--gold-pale); color: var(--gold-dark); }
  .t-fase { background: var(--gold-pale); color: var(--cinza); }
  .t-nomeacao { background: var(--gold-pale); color: var(--preto); }
  .t-inscricao { background: var(--gold-pale); color: var(--cinza); }
  .ntexto { flex: 1; }
  .ntexto .nt { font-size: 13.5px; font-weight: 600; color: var(--grafite); }
  .ntexto .nd { font-size: 12px; color: var(--muted); margin-top: 2px; }
  .nacts { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }

  .mini { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.3px; padding: 5px 11px; border-radius: 5px; border: 1px solid var(--line); background: #fdfcf9; color: var(--muted); cursor: pointer; transition: all 0.15s; font-family: 'DM Sans'; }
  .mini:hover { border-color: var(--gold); color: var(--gold-dark); }
  .mini.gerar { background: var(--navy); color: #fff; border-color: var(--navy); }
  .mini.gerar:hover { background: var(--gold); border-color: var(--gold); color: #fff; }
  .mini.feito { background: var(--gold); color: #fff; border-color: var(--gold); cursor: default; }

  /* ===== Lateral temas ===== */
  .side { background: #fdfcf9; border: 1px solid var(--line); border-radius: 12px; padding: 17px; position: sticky; top: 18px; }
  .side h4 { font-family: 'Cormorant Garamond'; font-size: 16px; color: var(--navy); }
  .side .side-sub { font-size: 10.5px; color: var(--muted); margin: 3px 0 13px; }
  .sitem { padding: 11px 0; border-top: 1px solid #c9c6b2; }
  .sitem:first-of-type { border-top: none; }
  .sk { font-size: 8.5px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.5px; }
  .sk.juris { color: var(--cinza); }
  .sk.concorrencia { color: var(--gold-dark); }
  .sk.tendencia { color: var(--preto); }
  .si-title { font-size: 12.5px; font-weight: 600; color: var(--grafite); margin: 3px 0 7px; }

  /* ===== Caixa de entrada (triagem) ===== */
  .inbox-grupo { margin-bottom: 28px; }
  .inbox-grupo > h3 { font-family: 'Cormorant Garamond'; font-size: 18px; color: var(--navy); margin-bottom: 4px; display: flex; align-items: center; gap: 9px; }
  .inbox-grupo > .gsub { font-size: 12px; color: var(--muted); margin-bottom: 14px; }
  .triagem-card {
    background: #fdfcf9; border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px;
    margin-bottom: 11px; display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  }
  .triagem-card .tc-main { flex: 1; min-width: 220px; }
  .triagem-card .tc-tit { font-size: 13.5px; font-weight: 600; color: var(--grafite); }
  .triagem-card .tc-sug { font-size: 11.5px; color: var(--muted); margin-top: 3px; }
  .triagem-card .tc-sug b { color: var(--navy); }
  .tc-acts { display: flex; gap: 7px; flex-wrap: wrap; }
  .btn-conf { background: var(--acao); color: #fff; border: none; font-size: 10.5px; font-weight: 700; text-transform: uppercase; padding: 7px 14px; border-radius: 6px; cursor: pointer; font-family: 'DM Sans'; letter-spacing: 0.3px; }
  .btn-conf:hover { filter: brightness(1.08); }
  .btn-rej { background: #fdfcf9; color: var(--muted); border: 1px solid var(--line); font-size: 10.5px; font-weight: 700; text-transform: uppercase; padding: 7px 14px; border-radius: 6px; cursor: pointer; font-family: 'DM Sans'; letter-spacing: 0.3px; }
  .btn-rej:hover { border-color: var(--urgente); color: var(--urgente); }
  .btn-outro { background: #fdfcf9; color: var(--navy); border: 1px solid var(--gold); font-size: 10.5px; font-weight: 700; text-transform: uppercase; padding: 7px 14px; border-radius: 6px; cursor: pointer; font-family: 'DM Sans'; letter-spacing: 0.3px; }
  .btn-outro:hover { background: var(--gold-pale); }
  .sug-monit { background: var(--gold-pale); border-color: var(--gold-light); }

  /* Menu "outro concurso" */
  .outro-menu { display: none; position: fixed; inset: 0; background: rgba(26,40,66,0.5); z-index: 60; align-items: center; justify-content: center; padding: 20px; }
  .outro-menu.show { display: flex; }
  .outro-box { background: #fdfcf9; border-radius: 14px; padding: 24px; max-width: 420px; width: 100%; }
  .outro-box h3 { font-family: 'Cormorant Garamond'; font-size: 21px; color: var(--navy); margin-bottom: 3px; }
  .outro-box .obsub { font-size: 12px; color: var(--muted); margin-bottom: 16px; }
  .outro-list { max-height: 240px; overflow-y: auto; margin-bottom: 14px; }
  .outro-item { padding: 11px 13px; border: 1px solid var(--line); border-radius: 8px; margin-bottom: 7px; cursor: pointer; font-size: 13px; font-weight: 600; color: var(--navy); transition: all 0.12s; }
  .outro-item:hover { border-color: var(--gold); background: var(--gold-pale); }
  .outro-novo { border-top: 1px dashed var(--line); padding-top: 14px; }
  .outro-novo input { width: 100%; padding: 9px 12px; border: 1px solid var(--line); border-radius: 7px; font-family: 'DM Sans'; font-size: 13px; margin-bottom: 8px; }
  .outro-novo button { width: 100%; padding: 10px; background: var(--navy); color: #fff; border: none; border-radius: 7px; font-family: 'DM Sans'; font-weight: 700; font-size: 12px; cursor: pointer; }
  .outro-novo button:hover { background: var(--gold); }
  .outro-fechar { text-align: center; margin-top: 12px; }
  .outro-fechar button { background: none; border: none; color: var(--muted); font-size: 12px; cursor: pointer; text-decoration: underline; font-family: 'DM Sans'; }

  /* ===== Concursos monitorados (cadastro) ===== */
  .add-bar { display: flex; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; }
  .add-btn { background: var(--navy); color: #fff; border: none; font-family: 'DM Sans'; font-size: 12.5px; font-weight: 700; padding: 11px 20px; border-radius: 8px; cursor: pointer; letter-spacing: 0.3px; }
  .add-btn:hover { background: var(--gold); }
  .monit-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }
  .monit-card { background: #fdfcf9; border: 1px solid var(--line); border-radius: 10px; padding: 16px; border-top: 4px solid var(--importante); }
  .monit-card.urgente-top { border-top-color: var(--urgente); }
  .monit-card.radar-top { border-top-color: var(--cinza); }
  .monit-card h4 { font-family: 'Cormorant Garamond'; font-size: 17px; color: var(--navy); }
  .monit-card .mc-meta { font-size: 11px; color: var(--muted); margin: 5px 0 10px; }
  .kw { display: inline-block; font-size: 10px; background: var(--gold-pale); color: var(--gold-dark); padding: 2px 8px; border-radius: 10px; margin: 2px 3px 2px 0; font-weight: 600; }
  .mc-foot { display: flex; justify-content: space-between; align-items: center; margin-top: 12px; padding-top: 11px; border-top: 1px solid #c9c6b2; }
  .mc-count { font-size: 11px; color: var(--muted); }

  /* ===== Modal ===== */
  .modal-bg { display: none; position: fixed; inset: 0; background: rgba(26,40,66,0.5); z-index: 50; align-items: center; justify-content: center; padding: 20px; }
  .modal-bg.show { display: flex; }
  .modal { background: #fdfcf9; border-radius: 14px; padding: 26px; max-width: 460px; width: 100%; }
  .modal h3 { font-family: 'Cormorant Garamond'; font-size: 22px; color: var(--navy); margin-bottom: 4px; }
  .modal .msub { font-size: 12px; color: var(--muted); margin-bottom: 18px; }
  .modal label { display: block; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.4px; color: var(--muted); margin: 13px 0 5px; }
  .modal input { width: 100%; padding: 10px 12px; border: 1px solid var(--line); border-radius: 7px; font-family: 'DM Sans'; font-size: 13px; }
  .modal input:focus { outline: none; border-color: var(--gold); }
  .modal-acts { display: flex; gap: 10px; margin-top: 22px; }
  .modal-acts button { flex: 1; padding: 11px; border-radius: 8px; font-family: 'DM Sans'; font-size: 12.5px; font-weight: 700; cursor: pointer; border: none; }
  .modal-salvar { background: var(--navy); color: #fff; }
  .modal-salvar:hover { background: var(--gold); }
  .modal-cancelar { background: #fdfcf9; color: var(--muted); border: 1px solid var(--line) !important; }

  /* ===== Toast ===== */
  .toast { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%); background: var(--navy); color: #fff; padding: 13px 22px; border-radius: 30px; font-size: 13px; font-weight: 600; box-shadow: 0 6px 20px rgba(0,0,0,0.25); z-index: 100; opacity: 0; transition: opacity 0.25s; }
  .toast.show { opacity: 1; }
</style>
</head>
<body>

<div class="mockup-banner">
  PROTOTIPO v7 &mdash; dados ficticios, nada conectado ainda. <b>Navegue pelas 3 telas no menu para validar o fluxo completo.</b>
</div>

<header>
  <img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAANwAAADcCAYAAAAbWs+BAAABCGlDQ1BJQ0MgUHJvZmlsZQAAeJxjYGA8wQAELAYMDLl5JUVB7k4KEZFRCuwPGBiBEAwSk4sLGHADoKpv1yBqL+viUYcLcKakFicD6Q9ArFIEtBxopAiQLZIOYWuA2EkQtg2IXV5SUAJkB4DYRSFBzkB2CpCtkY7ETkJiJxcUgdT3ANk2uTmlyQh3M/Ck5oUGA2kOIJZhKGYIYnBncAL5H6IkfxEDg8VXBgbmCQixpJkMDNtbGRgkbiHEVBYwMPC3MDBsO48QQ4RJQWJRIliIBYiZ0tIYGD4tZ2DgjWRgEL7AwMAVDQsIHG5TALvNnSEfCNMZchhSgSKeDHkMyQx6QJYRgwGDIYMZAKbWPz9HbOBQAAB5NElEQVR42u1dd2AU1dY/987M7mbTAyH0XgNiAbGbxIqKYtvos5cnPHv/1GfZrL0rT54Kigg+RHcRKSF0NqElgfTee+9l67Tz/bE7sGAIQQET2PNeJNmdnZ25c3/3nPO7pwB45a8K6e1NRKQAAMuXLx5aUpT5aV1dcVl7ezV2d9djbW1xS2lpzjcrV64cAgCg1+vpqbxQ5VoKCjL/09nRuGvbtvUXAwAYjUamj58niEi8j9wrp1X0ej01m82sMoGPN8F37951UWNjWSmiAxvqS7CmpjCnuqqgoL2tChFtWF1dlLd27eoJhBBAPDWgQ0QGAGDfvl1Xt7dXIyKPhYVZX7jfY0/sXMe/d6945VSBjOlNGxiNxhHV1YXNiHYsLc1etWnT2snuQ6jZvP3y8vL8bEQB8wvSknU6nepUTGREIIhG5sEHH9RUVOYUtbVXYUtLhVBekZsFAMfVWohICAFYu3btoPDwcNUh1U7IISB7xSsnzQw7GgRbEraMSk9PfKi+oTSuvDwn3mg0MkdPWkVr5OWkvocoYklplrkn02z16tVjGxvLmp3Odty/P/5mT210ssRsNrMAAGlpe/+NiJiStufzkpLshO7uBmnz5s1jj2fOKvdfWJi+raWlsqisLO/N5OS953lNTa+cMklI2DouNzft8erqvM0N9aXdiAIiWrC0NKd0+fLlQ92rPfGchAAA5eW5ByTJKh9I3XM7IQRycnJUHseoAADy81O/RpTk/IKMr/6MideHBYOsX2+c2NZeY21qqex65ZVXAouK0j9DlDA7+8D9ipl4LM0OALB+vXFcXV0Rz/OtiOhEh70Za2uLktOzDjx89D17xSt/eqLu3x9/W0tLpbm5uRwRBRTFdmxuKsOystzPcjNSLpo7d666N+Kksak0z2ZrxPSc5PPc5hv19IX0ej09cCDhSUQey8vz1p9sDaecq6AgbSMij2lp+54FAMjOPnA/ooDl5TkrevtOBfw5OSlPShKPhYUZ29LT9y2ory/e7eRbENGGRUVZ6z755BNfZcy8s8crxxS3Ocj0RAwAAGRmJj2JaMfa2qL0vLyUN6uq8nOtthZp48a1c3pb1QlxvVxZmZchil2YlpZ4g3tCMp6TGRFpRsb+pxEFuaQka93JBJxynsTEhDscjhasqSlo2Lx5wyXLli0LXbHiu2nt7dVCZWVejV7/oEa57GOZkyUl2btldGB6enK08l5iYvy1NbXFpYgS5mYf/MF1/LEZTzcb6gWkV3oEDgEAWLx4sV9s7O/nKX9nZSW/h4hYUJj+DSLSlJQU7jia5X+IolyQl7ZcMSPNZjPrJmA4AIDi4qwNiCJmZCXpT5ZJ6far6E+LFgVUVuZXORwtaLM1Ic+3YX19KVZXF9Q0NJSIbW1VuDtx10UegPA4h8ucXLNhzdSWlkqppqagc8mSJYNd1+66b+PvxnNaWiu7W1sr5G3bYqd5grQX8YLubASY2Wxmi4uzozIykh7oA3HAmM1mdv/+HXOs1gasqsove/rpp9XH0nDKSp+SkhRltTZie0eNIysr5bajj8vISHrEYmmUGxpKHdu3x473nOgnQ7ulpe35CNGBxaWZB7Ozk14qLc1eUlaRu6uquqCirq7Ywgvtck5eyms9AV35u7Aw40VECfPy0371PLeyYJSWZsciOjEzM/mhY/mDKSkp3O7d2+cCAOsF3dljOlLPFdi8ZcvUtrZqrG8oEX767aeRiEiOBp2iKRQmTq/Xs5WVuYUORysmJOy8sifNcLQ5lpOT8g2iHVtaK7C4OHNZTubBfxQWpj1QXp5rdDpdmiczM+XB3s51ovcKALB//+4IQWhHi6UJzea4i48+LjV1/9OIDrG0JHtzT5rJvZjQ8vLcg6JoxYMH9873ZD0RkUFEUlKSZUQU5MzMpCePBpyyIC1atCigvDyXr6zKTzObY4d6mc0zWJMdSVS4JolbA5GyipzfEEVMT9+/6Hj+k7LiZ+Uc/BIR5bzc1E+PYwIq383k5aV+2t5eY3Oxmzy6/rVhfX1pcWLirjtOFtiOWFDM269pa6v/PDV17+uKRnJtWrvMwbi4deFOZyvW1xeLP//8c5gnQJRz7NgRN7OjowarqvNbVqxYMQgRGaPRyCi+b0REBFtVVVDO8624e/eWuUePobKAbd78+9j6+lJnU3M5rlmzZozne+5n5N1MP5Nk27a4i7du3ThD+Vuh5/fv33FBR0e92NBQ2m00Gkf3pOWONhN3794V4XC0YVVVfp5er2ePt1IrBMqBA3vG5+WlPNbYXPlqaWnOi1lZaddERLgIi1M92Y66RkVbq3JzU5c0N1ekb98ee40n6A/tI+alvIkoiQUF6f/p6bypqfuf5vkOubq6oPb777/3P1pzKefbu9c822ptxKqq/FJduE7Vi+/slQGs2YjZbB5ZVpa7qqmpHJuaymzFxZlvf/HFF0EeKzEpKEhbiyhjTk7K8bQcIQRg0aJF6urqvPKurjo5Pn77+X3RTn1g707FGFC35jju+ZcsWcIdDchFixapS0qy6xGd2NBQUlZcnPVRdvbBSHPi5rHx8ZvOyc5OebuluYpHtGJWVvLTPY2d8ndq6v6bEa1YXpGXoDwf5b51Op0qryBDn5WVNMtNJnlBONBE0VJmc9zI+oYyvqOjRmppqUBEHuvrS0rT0xMfBgAKAJCQsGVOW1uN3NRc0b19+/bRR5uhR04gl39SUpL9FSJiTm7qf/vKLCLqqTsOUflh/u7J1cs1kEWLFqmzslLuLS/PiW1sLHMiiohowbbWKrR01yOigFZrI+blpnx6rIVDGa+crNSFiBIWFqavdL1epAYAWLVqVXBZWd4uRMSKirymNWvWjOnNyvBKPxbFuc/OPfBvRCsWFaZV5OWlxCF2IWI3Vlbmxyce3BPpMjEP/oKIWFSU3quWUzTVvn3brm9trU9Nzzq48HSYhH+/Sb5teH5+6iPl5Xmmmuqi1Kqq/JTKqvylu3dvu7o30/Aw05n6NqKMLhPVNVa///772Kqa/AxEEaur8/P27Nly7vHYYq/0A/bx6LCqo83K74zfhdTVlTbabK32uLi1l6dlJt1ZX19Sgshjd1cdlpXlfLNp04YbOjvrWltbq6z79u0c05uWUzTAWWKaM33NiujNIigqzvgRkcfMzOQHAQB27YqdVVdfXIkoYkVF7rZlxmWhXrCdCVrO/cDT0xNfR5SwrCx7DwDAqx+8Gpyfn25oaiqzIMpYXV3QWFmZWy9JXZidfXDp8RhLxaE7Vf5XfzTR3f4VoyxkiGb2+L6rC4wVFfnbZOzCnTs3T9kRv+OKlrbKDkQb5uam/AgAXG++rPv7GLc5znh9vL+JFAEAkpKyR9fQUJ68c+fm6W5NR3vUct99FlJVnd9os7Vidnby9cr7sbFrp1VWFqyy25rR4WjGtvZK7O6uR3d0CXip6r8kRGFoq6oKCppbKqx79ux4sam5gueFdszMTFa2K3phho8Z4+kNEzvdpg4AkIKCdCMiYn5+6vZjPSDFl0vPTHoZkceysqy9EAEs4uHI/cTE+Btr64rSm5rKu/Pz0776/ffVo7ybsydlUQS9Xu9XVV1Q39RUzlss9djeXuPcvz/h3uOQNoc+/9tvPw0rKEi/tbuj6e6srIM3vv/V+4MUC8P7fE6jieM26UJraooaBKETk5MTHuoJdApwvv/+e//6uqJKp7Mdd+zYeBuAK+RIOT4iIkKzZs3/xnhH9+Q9IwCAtWtXT2huqUTETmxsqqjeuzf+Ek9C5RgmJAUAkpt70NDcUtGCaEUXS2rDxqay1qysxDfBnfzrBd3p1XKQnLz3IUHoxJragqaff/45rCcTRdFy+TmpT8uyA8tKc1I/M37mo5imnv6D1084Wc/HtZ+3IWWDNq8g7ePqmqLdsbGuKJPetlGUZ5GdfeB/iDa02RqxtDw7qagk8z8lpVm/NDSU2BEFLChI3aLX67XetKCTC6pe64cooMsvTNuBKGJe3sGVngA7ctXU08WLF/tVVuU1t7VV47ZN62Z7+mne/Z/TI70RLYeDrvc+Lkmd2NJaYc/McQVFK7Jx45pJ5eV5yYgy5uam9ong8kof7P8eCBDaEyAJIbBhw4apzc0Vlu7uRnRHpfdgWrrAtHfvzuu2x8ddCH2o5+GVk2eN9GFBI4hIlixZoq2szK+QJAumpye+fvjzh+NAf/75++EtLZVN7e3VqITseQmuP/1wDj+UdevWTUnNSLx99+4d4Z5+QU/mYmr63tcR7VhZkVt4rAxkL8D6v+bbv3/XZVZrI1ZX5zctXrzYD9HIeD53BXSF+Wm/IIqYlZX01PHMVK8cx6TQP/GEX3Fp5redHbWIaMOOjmrB7Sgfi/pnnn76aXVpeU4GohPT0/d/cCxTwzNVxyv979lXVBTNQ7TJ1dUF24/xvFlEpNnZB5YiSnJ6eqLBC7g/aUYCACxdunRyZWVBFiJiVVVBXkV5/raOzhq025vw4MH4G3s2F12ro9m8/fKOjjqhq6sBk5P3XXk8n8Er/U/D7duXcKUgtGNlZW5GD8+aKMCqqMhNRrRjSkriQz357V7pRfR6PTUajUxKSuLl9Q2lNU5nC+bnZ32upK+UlGT8F1GQSsqyf3I9hD8OrvJg0jP3L2tvry9JSztwaR9CtLzSjxZcQgi4iK38equ1WYyP3xLlodUO1Z85cGDvfVZbk9zQWNa1Y8fGEV534YQGGgiinq5cudK3rDy3ClHAvLyDijmhAgDIzT34OiJiYWHG570AjiAiWbx4sZ9Op/PzPoSBa1YePLhnIaIdm5oqqhVLRZHU1MS7m5oqOhDtWFCQ9s6xXAev9D7QFAAgOTnh+paWSt5ibRRSU/e+4DIxdt3U1dVoa2out2zatHZyX7XWqSob7pXTY1rm5Bz4CLEbrbYmLK/ISygtzVpRXp53wGppREQ7FhZmbtHr9aqeCvEqboZHKhTrnQ/HGOiDB/c8Kojt2NBY6kxL3b+kpbUC29pq5N27t8/ryYk+lqbzjujAX4BTUvZENzeWpzocLYjoQFnuxMbGstbc3NRP586dq+4pvEuJQupNg3rFg4ECAEjL2P+hILQh72zF5ubySrN5w+VeAuTsBB0AQGri7pkHDsTfnpmZfPV3330X5rm4HuszKSn7rq+sLIxpaan6sKAg/bktW7ZM9LoZf5RDLFR+ftpqxG6sqSnMXvLhh4GISL2RIGefT9eTsuopW0AxGbdt2zippqbQ7HS2IKJ06KetrYrPykp+TwGmF3hHmoT06aefVpeWZu1DFLGkJDvp6aefVntXqLN2ET4iH68nbYiIJCEhYVxdXXE1ogMbGkpac3MPLs3NTXumsDDjy4aGsjZECYuK0pb0xTU568wJdzbA0JqawhJEBxYWZv6ycutKXyWS3DtKXvGcL3oAWlyctRvRgVVVhZn795sneh4TGxs7vqamOFeUrJicskfn9en+MIgufy0+fvs5LS3l3Q5nCyYk7LzUuzp5xVM8yLZIm60Jm5oqOrdt2zgJwFUO0aUZXUWL4vdsuRaRx4qKvF3eedSDKNEDSXt3zU9L23end5C80oN2c/v86TGIkpyfn+YOjjjc90FxUz7//PNhVVUF5YVFGTmzZs1S3vdaS57iJUq80hfAlZbnfIyIckFB5v+59+Z6DPXS6XSqZcuWhXq5gOOYDb0VU/XK2Qw4pbVY4hMuUiTjOwCAoqIitV6vp4heDeYVr5x0C2jDBuPopqZyR1tHbfu2bdsm9aIRvYERXvHKXzQr3Zniia8i8ljfUFpyMG3fnbuTd09evHixn9dX84pXTq4QBXQZGYmLEbtQljtRENoxNzflGU/T8+8U9hTdOAEgSAigdx545XQpOUKI5E7xeepg2t7tYYMHLQgOCplJCB3iOiTyzJ6P7qgAFhEZL9PoldM47w7Ntblzn1brdIfbY/WmKFwEy+E529+3nxT7mO7cuXmKxx7H4QPI4WIwx0qp8IpXTuJiT/sKrgF3g8puf15OyjsdHTViXV1xaXV1/rrCwsx/p6cnXff777+P8k4Dr/wNwCOKMlBq3xzjUBobu2ZMWlriDXl5afrGxrK4jIx9j3jO7ZOplU6KGieEYElJ9r6xY0dd0tzcZA8ODvZRqwcBgAXqGxplSZJznQ4+k5fEZFu3JaOsrD6vvb29e+HChYJ3anjldFpjv/3224jx48PCAwICLmBZdhbLcuf7+KgnBAeHgiDYgOM4qKqqte3dmzbyvvvua5dlmRBC/rIPeNJIE5PJRABALigoeTp0yJBkQZDLtm5NWDh9+qRLVSpVBMdx5wcF+p3jNyLoHADVfTZbK4yfMNZWVFjxOACsdKVhEMk7F7xyKnw6Qohs3rtt9oSxY18DWZrIcVx4cHAgSwgApQw0N7cI3d22pNrarFhE+frw8HOusNlsK4qKijplWe6fc1NR19m5KZ8jIipl7QAA9u3btcDp7MTKyvzE4uKsVW1t1ZXl5bnJ69at8/duRHrlVJuVrtSdzcNqaooaER1YXV3QUFmZ22S3tYrJyfHvL1myZDAAQFycaabV2tJdV1dS99lnn4UQQvpvaKESIPrFF18E1dUV13RbmtFs3jbbaDSGtrXVtbS2VncqvtzKlSt9H3xQr/Gws73ilVMmCmjWrFk1qaAg46rF+sV+W7fGntfR0eBsbKgoMBqNqueff96nurqwQpZtuH9//D2eSqRfs0MAAPv377pXkmxYWppzsLg4ayeigImJ5vvcx6hOhR/pFa/0gUBxTTp3GnlKyp7nEBELC9J+yMzcvwhRxoKCY7c+69egKyxKNYtiB/JCK+bnp/wIcDjdxmtGeuXvmZuHtgOIMhdLS3O28HwrtrVVYXNLhW3btthpp6qeKf3zF27srQMlEkKgqrLhmfb2NofDLogNDe3fH0ETEYIng/XxildORAgxyIQQiRCCzc3NiIgkKengcx0dHQ5/fz+oqa7/4rrr5uXHx8czhBC536nmY4myemRkJH6E6MSysuxko9Ho5y2P4JX+ZollZSW/J4rdclV1QZ6731z/C8pISNj+IADQYwFIUcmLFxv9qqvySxElTErad78nGL3ilb9LlM1ss3nbxW3tVdhtacDExPgb+pXvplxkZkrSFaLYidlZBz50OZ4pXG8rSFra/geqqooKjEZjiNd380p/sdIQkezZs310Y2OppbA4bcPxlIHS3+60zV8FcMnJu+d2dNTydnszJiUlPOq+mGP2a16yZAn37rvvDuurOeoVr5wm0FEAgLi4Dddt2bJh6jEaRhJ3eb6jC86eHrNTAV1iovlhm60J29urnUqHk+OZil6weaU/arre3vMsmf7bb78NKSxMH6HX67WHjzkNm+KKNktP3/9/iFZsaCxrWmc0TvEEpBdsXhlImq6XTrk0Ozvtn3V1xbvr64u7mprK+IbGsqqSkqwlP/7444hTBjr3RbGIyBiNRiYnJ0cFAJCVkfwFooiVlfm5S5YsCfQ2pvfKQBfFT1uyZMng8vK87TJ2IqID6+tLsLa2qL29vQYRZayvL6pYt84Y7mmeniwhvZEi+flpvyAilpRkbgEApl/Sql7xyon5drSwMGMrohMbGss6c3MPvrF79/bJa9euHRQfH3dhWVnuKkQHlpdnZS3XL9ectO0uBbm7d++6rLw877mMjOTrd+yIm7ls2bJQADhkPhYWphoRHZidm/LtqUC8V7xymsDmZtaT5jud7djUVNmxd2/8FT0dW1iYvgFRwIyMpEf6wmEAHCc9x62l0Gg0howeHRY3Zsy0AEnqBIvFClOnTrBWVuXWoEwqCEJRW0d7VkNDbeTE8WMX5uenlQDAZ0ajkYmOju43aQ0IQOL1ESe8xxIPANOnDznxqBjTkX/qwsMRACDmmB8wAIAeAAwQE9OHejDkLy+p3kifY1hzWq36dpXKH5ubS768/PLIPYhF6piYVUJMTAympqays2bNErfuin1z9OgxN/oH+t0FAD9ERh6/Zgo5ni1rMBjk7ds3TZ4wYexrIOMghmNGMAyEUcqG+vv7qfz8/AFAAwAADkcbaDRaqK6ubk9Kyh6h0+kcAK4wLu9z7I+rORAAPTGZ8v40bnWeC1NuEzn+4hUpGwwGuf+OiSsvs76+fF1Y2KD5GSnZl51/4aVJJpOJKMrDzV7inj3bR888d1plW2tX3rhx4TMQUQmKxj+l4ZSBufbam4oA4GHl9eeff94nIuLC4JCQIYN9/NRhWq12KCOTsZyaGx4cGDimtbVzXXR0tL2/JJWiu5yK/ul7AyaHWaLVrBwKsgw9VeQlBNApAREFXgjwVzd0dAtgt/Lg48N1hwwJ6QJRAtF9rCSJ7iEUQRRFEEUAUWTdv4sAIoAgS8ijnWhYlX3KiMEdNtFOZImik+fBwgPwPA/dTh54HoDnncDzAGGjBncF+Y7nASxgsVjAYukGiwXAAhawWACg2wIWAOAYSYy45RILpAKket5EKkDqka8cEj8/P7RYppDU1KVICIgAhtO8GCYMiMXIYum2Dh06FK1Ouy8AkPHjx1MAUOYyQUTYtOl3X0GQmyXAWjfIGI9jTlzDHWVaMgCAlFIJcWApLKNRx0RHmyTjuzetmjJKe4/VIQLp4daJe9SQIAACIALIeHi9wj/81/0pPPya2wr3+Iz7f4iA7nVdBvdHZALyofddLyIAMAy1M0AF5XMyovta0P0drouiQESNiu1Uznnoa9F1VkAAhbo6wsYgBAQRkRKmlRdFq5+WawNA10mO43lT5fplGSQEkCQEXpSBlxAoYWy+Wq5NFCSQRBlEGQBRBpZlXIwCA22VrbLp+Y/j8t3KoN9NJLPZzEZFRYmZackPzjx/1o9FRdnLpkw5/589YYIQgl9++WXYuedODY6KmlugvPaXAdfTl8XExJCYmBgAABIfH08iIyPd3k6ka872l0hrDw0X9+ktWQG+ZJrNIcgMpczxphchhFAKIMuIkoQyulF6IoNGCAAgAXR97MjPk54eAoEjOn724KeRQyf+47n6fl0EGOL691j9sA8vM+To5w9HrLl/uI8jXyDuNUijZqGiyVG1t9ER/umn223ur+1XoFPYdZPJ5HvJpefmhA4ePKagoPTfWVkpy+6///EWz3ndF4CdkEnZy8NCAECDwTAgnGDieqgcL0k+ssyyhFLZ6pQlUSIOIDIh7vUfAV2/I1IghBKCDgDaQVAO8fVh/F1q46g2t6C86lY/Ryg/0qMxgJ6/YI/vIiHAHDZ58Y9HEiDgCoRABJDJIf15AmwJ9kDBkF6gi+6VgxKq1bAEgICMslu9/vEOEV0aTpQQJBkEi50nPhwdPVLkRhMC+TqdjjGZTFL/miwE3WSfJSVl78O+Wu2Wc8+98H0fX/VthJCLPEFGCEFEJDEQQwykb37p2RS1zwqi5CvLFLQqjtZ3OJ//fUepadDgAKbVJkkAAFoA8NEyjLWtw7E6ocj2z/mXTp4zMyQ61Jc+TID4yeBCACIgEJAIIqGEMAxLgKEMoRSAkiPnLTlBzaMg2uYQQZJdE5djGcLQI0/EizLIMgIiEI2KUhXH9KCLelJ/6EmaHDJZZdn1I8kIMqKMQGQABOL6PyUECBKCDEOJ1Y4dnQ5+t4qlPAEMQCTEZZC6bVKUAQgFWcZAjmG1siyP9FVDCALIBAGdUrd/f54o0dHRktFoZGbPvty8Z4/5+unTmc9ZSpvcg0c9B1FRPidDw5G+nMitgml/r7gVERGBao6V3EsTBAX6tvy+p6C+p5t88smrB/1207RX/FXwqK+GjhBECXhRBperhZKaZRiVmmUlGcFql8DhlDsQxRZRkh0sq2q2OwSUZBn8NGyLimOdR4zk0b/DH/+WEYksyfN9VNQfEcDmlOtFGTo8D+cojuFY4sMylHQ5MZvyUoYsIpUQZEmWASUZZESQAECSZDcUKDAMARVDQc1RsDnFUA3HqSRZDmFZ9AMggwghQb5qlqo5QikhIEgy8IIEkixLgARllKlGzagkJ0ilzfJ/n/9oU/zxxn6Z/tb7QgLIT3YHL1NCWH8/FSoMp6kfg85d7SseAGatWLEi5HiMu16vpzExMRQA5GO5VGxvqlWWd7EAkXJv/pj7Avot2JRV/5YLLiAcW0YRJbfzL1NEIKaYcK693gcXLk0VAEb6rH73gqeCtOT/QvyZwXanAFa76FquAWWOpVStUjGt3Xyjtdu5nReZ7RYrn13SzlZ/vnRT+8kcB+O7cwv9NIw/xzJgEcSY+S9vXKoQVwAgx31+W5yak29gGQbKGhyxj74X9++/uCSxT97PBk4cRoYEqdnhaoaGq9VcOMtJ57MEpgdoOT+OIeBwisCLok9YoOq2Qf7kto0f37y3rlt6a+E7cWZEPV26NJZZsGCeZDLlkfHtwfTChUsFXw3aFfaGEAI+HDcgTCJCiGw0Gpm77rpLevDBB1v/YCJ4KByFtzjelkdvuT9ASJTo/l0JW5E9ER4REcEuWfL5JkmSU43G2LdiYmKk/rbnFqMHAgbA6qaUoMnD/IM4hro8LkIQCIDOrJNJlEFc/PLcq8eEqj8ZHMicb3Py0GmRRABgCCUUZZB8NSzT5cC26jb+g+0l9uU//bSztSdnUX5LTw/tbJvySF/2phQpqreQBcP88JnkWoYSwigDyVHX4O/SR5Dm6UMgOtoEhFHcMASCfJBZH8Ee2qPoozRPH4K63HCkbxtkxATxvz9BKwC0AkA+AOxUjnv3+atGjBukvVjDwc0altwQ7KseIogi8IIsBPuzl/to6K5f37vl60su2fpSUlKqPTh4PBMdbZL0+giCAGDnRYqgOqTFuQHkyLj33oh7jw098EEB4ikhRFQW2s2bfx87ZcqUB7u6uqznnXfxp0o9zGMCTtnojo/fNmnypHG/Wuz29RVlVUZCSP7hLzKzqan+ZNasWWLCvp1XTJky87qqqmJfF1tsov1V2w0bHkwolV0UAwGQEVkCgBAVS1a9c+OnQ4NUL7IEwWJ3yj5qltpliQUAkCWU/bQc02bD9KJqR/SLX2wrAQBAo46JyW0iAJFyTIwBXX0TAInBIMOf55LIv9yuVcQVk7BHoqWnxZGgFGVIEM36CIgyJIh/9rsRAWJi9GT69DwS6l4oImMSJEJ21QLAbwDw2wv/iBh83jS/24O0zOMhfqrzBF4AWZKFsUM0T7yhG3Jh2nnX3x8dbSo06yNYxdbstjgohqpgAFfWQBc7daQ2AwB5wYIF2ieffOzagADt/T4+6pvCwiZoqqoL+J9++mkpAHQfzWQeAbjIyEhqMBhkf3+/64cNH3M+AJwf4Of3VkVV/saOtu6VX3/9/RZComzK8fn5KQtlWYSaysYPDQaDHBnZf0snaLWH3SdKCKjUbMcjt9ziP/8KsnFoEBvRbXXwjIpRiTIVK5uEtEH+5CKWgqxVs0y7RS5OyXdcY1i2rW3JklncwoWpIolW2LUEGBhkbV8mFcAfNsINrogUk0lHdQBAok0tALAUIOKHH9/xv3+IluqD/VVjOi0OR4gve+EFk9X7P3n2+juiDFvjl+sjNAAg+qvUnYgIBAZ2QLun+xQfv/2cESOGRWu1qruHDRs2kRAfaGmuclRV5f3WabWsuuiiQc6efL4jABIVFSUiIklNTf0hOzujIDDI/0EfjfrWMaMmzB8zyjE/JualkldeeXp1aWnFT/n5WR2hQ0LnNzRU1qSkZ213I7l/lyqnAIQAEUUJ6hod5994MbwxPFgzu6Pb4fD3VWmau6SDNa3kIa0P3Bvoq77Y6uDRLoJYVGO7x7BsR5tZH8FGLUw46/oguDaoXQsMApAYfQTz9tsJ4kNvwvIn779ww5XTwxaNDFLfa3M4eH+ODTlvgjru29dvvv1hw8Yter2ehgXkd8jID+zVCJHs2bN5cFDQ4OtDQgLv1ahVcwcNHg083wENDQ0ZXV2WX5qbW0xXXHFt2Qn5cG5E2gBgBwDsiI2NHTNlUtedPr7a+0NDB52rUgW/6eOjfm3y5PE5QUGBmvy8xlXPPvus85lnnjkh/+FvwxwhYHOKODSA6P1VDHRYnLyvj0ZT08x/e+fr+c/dd5+Wveu8MU85nIKs9VGxFY32pS98uSPFrI9g/4K5duaADwDBPQ7uMWn9L8B93711feHkMN+3RYGXOArq8aE07osXrrv9eYNh3Xf66zXjB2ncWyoEBHGggc3MEkLE7OzUd2fMuGABgAMaGuo6y8sL1na0dP58wZxLzYrmU/iOYymfY5qARqOR0el0QAipBIDPAODLtLTEq4KC/B7Qan1vHTNm9HltrQ1QW97wo/sj8gCaNKBiCDolSdRqVKryRpvhXv3mGACAq8bNvS5AywbwPC/xNlGut6gWIwIxRQ/xBmAfJVGGBBERCJh0lESb3vnm9bllE8NUPxBAqmKRho/Wrvro5ZvOk7u6W8lgzYA1KE2mZgQAkGXR2NJSPb2xsfmnrJLCjffcek+dcozZbGbj4+Pl40VYscdhZ0Cv19PIyEgaFRUlXnDBJdsBYPu2bduGjxtnfdhms4yae8stSgyZPDCgpoSDoOSv5bjiWseiBwybY8zLH9REPbzCqVYxsziGoMxQptMmlb34kSX/xY8AAUyyF2LHNjddvu2WVYv+fb1l+lDNOpRk3lcN2kmDIS69GP5vnChJDCVURhnswsCyyhUsnHvuRTs92VtEZEwmE0RHR8tRUVF90tvHTRI1GAzKyYi7aylz3XXX1U2adM575557yb/+TDzZ3yE+Wu2hGC8AkPx9OLaqRTA/YIh7Ds0RrD+fIwEAqhkch4iEYygA0BKABNFds8Kr4XqRhQtTBaNep3r2/a3rK5qFl300nMpqF4RQP27iueN9lggyurdECQgD2AtWSqQrnIUbjH2eG/SokzGIZvYYRVEwOjpaIoRIrpoPZlZpwti/h0h/JEuJACxLaKdN7MqrFB4hBCAmPlKetWCeBAAgSxim5DWJgtjhsinyvOUi+qIJDCbebI5g//lO3KeVzfa4AD8VZ+UFcbC/KlTNEtYVbUpAcCPONADvkRCCUVFR4p+d9/Sok0mERInu+utwrF7cBoNBdh1HBoyZFawKQEIIyDKiVs3S1m5cYViytWLXWxGsZ3QAEsIq1qeDl7xlIk5Q4uMjZUQgBY24sM0idWlYhvKiLCtB3IjygNZwRyunE+1V7zmhSEZG4udlZTkLN27cOEmJNHFrNXQXBhqwxYH2HSwOkSRZzVCQnQKCxSFtRgQSr0yUmHgKAKBWMS1KwDGjol6/7QTFYDDI8TERjOGrLTVNneJnGjX7B3NcgIGPOEU5Kfhwb4wzSmU7d+W6P2BFMQvl9ZvXh58zM/x5SvxAq9VARUX+AbuT32KzdG/58cdf0wghTk87dsCVTXB2qwB8KSFEEiUEix1thAAadUeyj3ZBbiWEAVFC4Fg6HAAAcsO9/tsJSGRMgoQxQP75z7bFg/wGPRfgwwQ7BQkZSgBlBNk5sEtuLFq0SB0VdelbnJqtau3ozGqsaS294447mnraCjh6m4B1vYZk82ZTU0Fh0eNajc9cjUZ9xZgxI+cA+M5xOFre+vdrz5a8+OK/dnZ0d207mJS8jxDSCH3MJugvovFXufJHEQEJgMwLPWpqpyAXARIQBBE1lM54/PHLg4nB0N5fM5T75+oPaNZHsMuWJbRd+968VUMC2ad4QRYJAAcAwtRJo1oAAMLDB95C5lZQzvLy3DvGjg2f4rA3Q/uozq6qyvwKQRRzRVFIbe3ozLVbrPlXXz2vnhByxI4/66GpmgHgWwD4du2KFYOmXHzORT6c6iaNRn11QID/lKG+4yaOAdvC8WNGd86effE3559/8Wv9pWZJX8SX44DQQ4MGNuFIwDXnuTSdzSYk23kOJEQpMEAdNDsseC4ArI6PiWAAvBvffRX3eJJWi7gmLIB9ihKk7mx2ZHxUA95Ub2rteEmrLb9XEMRxKhU7JyDAf6avr99MAJ9/AAhgsbRDdXVBJaFMXlNLS+wF517yNSIS1tNMjI+PZyIjI2VCSCsAxAFAHMyaxR345j/nBQc3Xstx7HWhoaERKhVjURazgTJAHKdypTsgUBkAJ4wMa3FZi65VNtpkkhGARDYKGc8NEmuCtcwIlCUIZKVXAeCX5uneje8TkWiTSQYAzMxmD44KFuoDtHSYILoTajlmwI6lQhReNPuyWACIBQCoqMjdxbLsFWlpB+8PDQ0OUqm4KQxDh7IsuWHE8OE3EMQbFi1a9D9CSBf1OJFCd8ruVj4MIjKQmirMmXPZwUmTzn1/7NjpkXv2JE5qarIscn+q32u3GPe/KhWnLA8EEUDlrzo6uA/j9RFMwooER7dTXumjYYndKfDDB6tnLntr7iPR0SZpyYJZnBdKJ2B96fV0aWysTZQgR8UyQAAlRGAL82qCAADy8gbudou79L8aEYndye8OChrMqrRsxbRp5307YcL051va2vao1RpwOK3QbbGuCA4OlvR6PaXHQDG627IquUDKvhuZO3d+SVRUlEWx1QfKAGk5zqPcAYJgs5MenX0EUtopLWruEjs0LMM4BVEaGaL59I1/RU1ZuDRVMBp1jBdLfZN4cDG/ooBFlFIAIDKhlMooas8AllKOj4+XCCHY0dZ5gBAWfNWqa3bu3Dy9prYoadb5F/+XAFGXFJf9c+rU8x964IEHrAaDoU/lyJEQouy7obsp+YBbmXx9Va49EHdVO57v2dk3mXTU8Nn2ptp2PkatUTGigKKvGoIuGKeN1c09LzQ62iR5QXdiQjm2Uinvx1ACY0b5igBHFpEdiBIZGSkhIpOTU5zU0FBtCQkJ/teUqWP3jBg+4aKamuID6Rk5c8455+JlHh1VXYA7kf0196b4gLPBOQ4OlYNDROD5ntNFFEAtfHfrotJG25ZAf7Xaahccw4LUE++6MizuppsuD46ONknuDGuv9EFsTl50B3YhyxCwWMRgOBMQ59J0UmCgJlSSJMuQIYNHhAQPDi4qyl50330LI6699qZss9nMehKL1F2X8A+lERQQIiJ1NykYkPa2abrLTyiqbBlCCQECIAMC6XYeOz9LpzPJqNfTA8W2fzR0OHMDfDlNp9XhGDlIO3vBlcG7Xv/nFePcGdYsDuA05tMlKpZBVzEz1wYx7yT+A/2eEPU0JiaGycxMfujKKy/ZP2JE2NC2ts7G3Nwi3ZQp5z23e/duByLSo4OaKSLCnj07HtXr9So30IgSmOn25ZTg5QHN0lEC7KGqWIDQC96AEMAYAFi0IqEjtdpxY6tVLgnyVWs6rbxjcCBz3mXTBiX+95W5N0cZEkQCgHqvtjuOGmBClDhWCgC+Ppw8sMGGhNK35YqKCjYoyP+LsLAxIeVlNeY9e1IvvfDCy9YgIivLPWfQ0AMH9t1/+eWXf6+7a/4itxZjCSGYnZ3yeF1d8e7amqLNOXkpTwIAc1obip9k8fFhEQ6ZlK46/r2JwWCQjTodY/jPjqrE4varmq3yweAAtcZmE50+KjksfKRmg+m9m//7qO7iEIMhQSTElZDZU7+CPzVH6cBXnJHurRSWkcccUTRzgC9Prop2Ml2xYoXDbnfuLC0t+GL8hOlX3XnnnWVuE1I8FqFIhwwJfsnptKIsY677ZEJGRtJrM2ZM+XrYsOFXDB8xau70aTMWZ2cfXOoO8h2QM0GtZg8HjiKA3e447n1Em0ySUadj3vtmT/XK9R1XVTXyq7RaTo2IIIiCMDJU9cTtFw9J+1/MvPsRgYtyAQ/RqGP+bBdYPKxl3b8AeLThG1iic+UQsgSmi7IEAEiQADBnQPiAor327k19eOLEc14ghIBer6fHy4tjff18ZjQ2N5bOnDFrMSIyW3ZvGTJ02ODX7XYHVFbWfuFw8AUjRgz+cPTo4Y9s377pS0JIdk/lv/o94BjGo+4qysgo5SAMxwWdu5qZxZQA9y15Y+6uMYPU7wX7ckM7bA7RR0XGBISpVm78dN4r3TxdmlVLV5FoU6ti55ui80j0nyjnTcjA1nB6PVBCQP73wqtGcAyEC7wMcCjW58yRf/7zn91Kmlpf2nBRluUoQ6hVYVzGDx3xf2FDRvlWVtVsnzbtvBfOP3/O0ra2zm8CAobgoEHB5wAAxMfHD7iB0/hqZXQloFI1x3RfMmtyGwD0qfGhwWCQEYC42MstP+wssc6qaRN+IIRhVCwHDodT8NfQ6eMGsYsiJmKO8f0bv1z02txwQgxytMkkIQJx+3l9RJEOGErgcGMCacBNxEiIoIhAJg/1uTrIT+UjypIEcOa1ofaodtAnjoNtb+8sDQsLPSc3P+XfDpvDMTg06ImurhaoKC/XK5pMFtEJAMBxrHOgDkyXze47SMMeNtu6T1DjACC4twyio011APDoEv2Ny4Zo4Z1AP+4qjgJY7E5RRcnQ0YN8ng3Q8E+s++jmDR0O8l9CNpiVOExXvcYE2WDorQaMDlhmFQ6gMjE9AC5SJiQBf30XHiLE1YzhTKRzT7h7Tktz6xuDQoJXh0+d+R6AEwBYyM3N+uGGG25PRESyY8fGEQFBfg91djWS6uq6fACAyMjIATMTct0FTTvaLINJSNChrO/uE0WcYmJGuzSWu3DOfgC4+qtXr543MlDzjK8Pc62PmoVuu0NmKdDQQO6OQC3csfGzmxNbu+Qff0lu/iXKkNAF4CokazK5TNajv2PWgh2UEGAGKi9s1OkYiDHIX/LXzgj2Z6+w2QUEQhgAlOAsF3bOnCt/2b8/3jpyVNdjDGVCrVb7FtOTce8hIksIEXPz0z4dMWLSuMLCtB033nhr/kDz3yLdXhrHMjI5bAZAV9dfWdVchXNQr6cQE4OEkFgAiP1Gf+ucIRr+OT81c2uAH+PjcEogSLIYqGEuCfHlLnnq2rDXH7ny5h8rWx0/kmhTueLnQQwAcZmtQABgUMVef8eEccFqn4Hp8oSGNxFCAFe/w70d4MOwnVZRpIR4t04AgEXUU0IiNwLARs83ppumMwAAzY0Nn1doCiZUVdU9AdBLR6T+7sNxzEm/cldZcwMYdTpGFx6OxGA4AAD3vP/k1ZMnjlDd66tm/+Hvw06iBMDucEhajhk9yE/1VqAGXvj9k/lrK+ut3xFi2HuIYDHlEYg2SdMDRwJLgMg48FSc0ahjoqJN4pcvXHftsCDVbRarU2IpZQ91eT3bAUeIQXbXoFT6XFFCiKyUBouMvPEgAMzxsFkHpGPBqdw9DE/BuRWzUK/X0+nT80h0tKkIAPRjIiI+ePfawBv8OXmBj4q5TqtmwWJ3AKHEd6gvecBPpX1g3Se3rG+2sZ8QYtinTNiShDZCiOpwy9QBxEzqcsNx/vxzg8YPV3/DUJQlSojFIQDHMKjy5lq4tiAVcPVEibl21SnKskwGYgylIvSIxAg8cdakD6LQwno90EiIoFGGBMf9CfA7APz+zRs3nRvqEP/lq6Z3B/mxQXaHCIiyGOqvmu+vluev/Wje75XNzNvR0aaM71++hSdEHlD55QhAYLqOkGiD9Mt7N/4QGsBN6LbxIgFKi5v5D8LDfF6CQz1/zl45rpOgtFUdyGADANAA46HfTq3eMBhAdpdFJ0ajjkHU08ff3ZR552sbH08pds6saRPfsglY5++jZnleBEkSxaFB7G3TR2HyqrfnfZTd2j0cKEgDRb/p9UDBqKMk2iStem/eF2NCNbd1WXlHsL+GbbGK/6mr7Vyp1nAcGYj7G6cbcB4qYWDfKPO3RGtgdLRJIsQg6/V6ikYd8+Y326p1r258Z1Nqx4zqNuElp8hUBmhVrM0uIEGJHRfG/V/UVG0KA+CPIEv9PdJEr49gDQaQSbRJ+t97874aP1j9XLfV6fTTsJqaFkfRt7z15RGD/YJk2VsA7UQAN+BFRRWF8ffUPjIYDDJxbymY9RHsNz/vbY9+beNn5kTu3Ipm59sS0C4/HxW12nk+QEP9OAY4GYi7n7OMJytG86StJHo9NRp1jMGQID5691Vhpg9v2jAhVPWUxeZwajhG3enAzrzy7jsSDAmiiMgBektXH/LhzgphPHU1AYC/J0PEtaWQICIAiddHMFEGUyeYQP/Oi9f+OG2I+uMwf9WdoiSBKMkSuFKnwOaUWEIAjfpmCjDkbzPLDvWJyw13NZ4EgGVv3nTbsCD2ixA/OqbTyju0alZjF4iluMkx//VvE3IAgKDk3X876wBHKQP9qbKf0vbpMPC2lwOAbuXbc+8O89d86e/DhFnsPO/gRWQYeGbBvAhTtCGhxWicroJT7wv9oRuqK3LEICt94pa/e/PFIT7MK0EaciuiCF1W0Rnkq9G0WoT60mbHHc9+uDVx0dNz1c9+tcXphdnZqOEOaxgQJIQDBbX9Cnh6PdCY6TpCok2/vPPUtclTRqp+Gx2iOb/T6uCH+HNTb4kK3kxCrro1OtpUi3o93YJZHiYmQ9GoY+Jzm1izPuKEvj9y+hBUavzrdEZZKWX3x26oCfDRy9cMHx/kd5VaJT2oUdFrfNUAVoeT5yij8vNVq2s7+K2p+c7H3vtxW7XRqGPABN6ygmcr4DiWQQQA6soz60otGtsNcBD6S1C+wQCyAUzuJofby8Nmzrziy7tG/jw+VHtLh8XhHOTPzp47w3fH0Cevnk8MhqK4L26TDkGWsp3uFsjSX4Y/AMDEiepXr5/hNzJYGurL4VQ/FTmfI3A5x5HzA7U0ABHBwYuig6dsgNZH1W6TqqtqHB8++E7c1wCu0K7oaFdqEwAA640xOfsAx4sy46MiQCkBhsgteXkmoT9WU44yJIg6nY5Zs8Zk/UdW1q0/v3fDrxNCfXSdFsEZ6sdMvXCsJnXdx7ekUBCn8yIAFRGGBFDdmg9vHM0L0MJQpknkeXCIKA8O9mtQUZBkRFLXYhmKgKyvhgOtrwZsFnuYn5pTiSAHAiH+KpZheaczVMWxVAYcxDKiPyUk2FfDAssQkCQJnIIMDl4ADUdBq1GxnTaptrlJXLan0PKf//60sxURSUwMIdGGI+ND1WoVAvFSJmcV4BpaLMMCR/sDAQCGoXYAwJgY6Jd930wmk6TXA42JQSSE3PPLuzeEjgn1iey0OkVfDfVTczTSwUsgyggSIPj7kIkhrGqiqwEeAQDGHVWjROIRCPb1O2xTAwD4+QAiASAyILriS9FHDYgIMiLIMgKijLyARJYJcCwDGhUDnTbRYuVxt90hrNme1bJ+mSmpDcAVIeMulnNoPHPDmwgCkJ/8tX4qlgFBFNALuLOGNSGUEAKiJKMgwsQxYyI0MRDJx4DBo09j/xFX+k4MJQTEzcW2225T0bRBfuw4u1OURFEGBOKqiYQAgMQNEPS4EXQBDl3ZEa4EGRfeCCFACQVCZBdACbj/dsf2IYCMCA4BCC9IFjuPlU5JTBUEiMup4/e/98226kPfYtQxEG2S3Sbtkf6h20f9iQjXqDkOeJ7g2V5y6awBHEtZIARAkoAPCWCHvHGv5h1iMLycsmQBBwuX9sv+SQaDQXbn33XM/L+r7vZV++1nGQKiDJS4mERUsZRYeVLsFMQaSZACKcsgSykwDAKlBAgQ1u4QQxjqCm8jhFglSbYgS5plEUVREAihaFdxbGuXQwCNmmkUnNCNwNTaBKx2OEj5059srPO8LlfFqngaY0iQegIagBLeFik/eT87KMiHedTOC4gEzvp6nmcN4FQq6l5ckTqdojRpuPalxa/emDZ74dLVKUtmcbMXpvZL0Ck1MKMMuw78+NYNn0wZ6fNqt9UhAaEMEJB8NCxbXN+5/rH3d77cy2nUABNh4sSJUFKyhT9RM5oAgGzUMSYAyM01oWt7AOTeilNEQgQlBoP409s3vhsaqB7UZeNFcgZmfHsBd6wZp1K5OXhCRBkppYI0ZRi38r+v3WCfvXDzOldT+NR+WQ4w0pAgoV5P79//+7uB14y+f5AfN9whiDIAcZWeI6hF1FNYWs/AgiWiZwwmdR3iBCiBkpISt4ZybWADHK7FGu9O1FUkHgCm5w1BndEkEwJwLE3WkygpOp88e3Xk8GBugdXOS8QVeiB7AXcWaTiFQ2AJIYKIwLISnTyUW/v9Gzc/8s+FG390mUpA+lIM5nQKAUAzxDP/255lnXvFiC+GBqs+dQiSBwCITIhBNusjaFTPQeZHgElJoO2zejsB0euB6nRG+dn5kUFTRvj+oGKA2ESQKfEWzAU4i2IpW9utw1z6DYCXUORYlgGklIAsTR7GLv/fOzd9TIhBNhgMcn8sYx4PCTIAkIp2+ku7RbCylLCAfdbGeNTPKREEIDHTdYQQApfM8fs5LIgZZ3EIkkbFsJR48XZWAY4hwKGMoGIpywuYXdJgf0REpsNHzbECLzgnDFG/vPGTW3a8/fiV06IMCSKiK7Wm/xAoICMivPHFplqbgNlqFeMq295PBBGIkqKz4u3rvx0Xqr6hw+rkA33UbH27UGTjsZWhhKI3H+4suVHqetaIADKC72PvbVu+r9h6SUOXsMdXq1Jb7LwQ7EevnjM5MPnXd+c9Q4iOugoGYb8BXnxMJAMAIIrSAYaSflO0wFX0FoFEm6Qf9Nd+PXmo74IuK+/0VatUzV181bbMjlsoIQ5Czmg1R7yAO+JG5UNDIkoyO3fuRLVh8baCO/4vNrKkWXobCMuJkgQslX1HhaoWxX7KJ3735tybCSHunDZX7ld/SJMRBcySkfSLwnOufDiDTAihP70995fwEf6Pd9ucPMcSldUp2wubpZtHhe8vRpD9zlTtpiRo96UNwFkDOAnhEJ3AUAJbtpSIer2eol4P972xXp9f57zawpMif42KWu1OPkCLF04aqtmw/uObzUtev0mHCNTgUcoc/2Qp878iSttjm0DKnYIEQJD+fZPMlddnMCSI/7rv2iG/f3Rz3ORh2ru6rE5exVBWQCIX1Fr/8X+fbskaDvNCKAERz9CMODfYqGdVBKWLMCHE3S3V6IorPXusZ+IE180DwzAAoAODwSAbwDVxogybd91z0+UX33ZZ0OehQaqHZEkEXhT4Qf5sZIgfG7nxk1vS2q3898nNGuPhUuZA4mMimOMXdj05kpvr6kdud2CDk5dlhp7+0uHKPROSIAIkiF//+6brx4VyXwdr6fjObqdTpWLUokyl8maH7tlPd67X6/U02CdPcEXTnXGajRJC5N27t08eN27Cqqb2pn/NmnlxqtFoVMLcJDcgZdfxenrWAM7fT9MA7lAnhiEI+nBU2gpEGRJEo07HRJtM7T9vgodXGuavHeQLiwcHakZbbE6QUOQDtMwFIf4+Xw/2l/QRH803tVuE/xESl6xUVFbK3OmiTfKpCxVzXXBbp71LGMrwLENUp2nbkBh1OqrTARBikgASxH8vuH7YzNGcITSAfYxlEDqtDmeAVqXusEmW4hbL3c+8v2OTUa9TRRsM/CsLI4KvmOjnzzJEJgD0DMtGJUOHDvlm5Mhxs7ssbS8BwD06nQ70er3q/vt1b2m1qrmiKLXW1jYuISRy7VkDuG47Twb5q0FGBJQh4OmSzX5fAXQhuuILo00mCeEQ07bxvttmJt9y4Zjn/X3oP4O07GBBlMBm50U1S8IC/VRPBWnwqQ2f3pRs4eGX0jq6nhBD+aGVr5eqyn9FYgyABgDoEMBCKVgIwKBTATcEIDF6PQGIpzHThyCJNknRJpMEJoB3Hr9u1NRRPg/5qfHpIH8utNvqFGREJsjfR93UKSSlVHU9ZvhPQo6rpHu4CAAwKjiAJVRm0RUlDYLoHPDkiaLd4uLiwoeEDbqqtragtLy07Hl0paBI2dlJKyZMmHAvgAAAaggOCbouMzPpnrPHpCRyE7iCcpEh1HdcaLAvAHQdRTN59g9o+t/vWa+99ujNX547WviHny/5h4Zj52g1FOxOAShKUpAPe9Egf/aiYLX03tqPbt7p4GH1zormrSTa1KaYXyaTjkZHm/rc7KEvNFgGjHVci20OJXiZAlI06pji+m4GjToE+GPkyLFE6eEGunCEGABqMLg0tMGA4A7fCg+P8Hv+Du0lwVrmXh8Vc9sgPybA6uCh2+oQ/LQqzmqThfJm8eO7/137FkCqoESaGHVDGAAAdNiDOMaHAKAkI1BEcAz06eRuaCMPGRY0IzAgDOtrGn+cNy+6ARFp3PYNl40ZM+betrYmZ0VF7UsalcpnzLhRH/r7+71/xgMu3v2vk8dW2UVUyoSCFgBCAKA+JkZPjsxsdvcPOKztGgHgSwD48ps3r71sqK9Wx7HkNn+tejRDEewOATgGffy0zM0E6M23a0Mbbnh/3sbGbnEVIVsSlIgON4hPCvBKdxYScVowARULhBAQZWo9OQmoLnlFd03g6Ik+YwLUcL5WS69kiXhNgIYbreEArA4JLXZR8tFwDC8A19Ipb8qvs8a88uWOFEIA3npLT6OjDa7r0AGACSAoUO2nYikRRZGIEoKvj6oVwPXeQBfqqt0BDMdqFX+tqChL7+8/CAoLcxfPmnXpYgCAioq8+4NDAs854wE33b2CSzJtFCQZAED2UTNMkD+MBIDc6e4e4D1oEwR3la34mAjmqrcTxMff2b4PAPbdcsulb945KyTSzwfuUjN0bqCWHQQog4PnZQ1Lhgb7cY8F+7GPbfzk5r0tVlz6cEz9L9HRJoEAwK8u4P0lYNRADcgYBAwlYHeKGOLP3fKT4frRLBCCBBARAWUiB/urG5yCKNmdInAadauKYWyCJIAoET+U5GAfNaicTinMx1cFvFMcquEIg0hDKCGhDIOhfj4MUEKAFwhIsigLIqW+PizptApyS7e8sbqNX/zUB1t2KmY0iTbJnmFxoW4tKxMcxbIURBFAFFFu63K1RxvIeFMa2tTX1OeMH9tEgkMC/pWelVSn4VRTRo0adk1DY3V3TU3rJ26mUqKUaEVBks94wCnMXmuXvW5EEJU4BqiaJaBWcxM8J8WxKV9XlS0AV+mA0PAmEmVI6N6wATYCwMbXHr0qbNoo9Y0BWlanYek1gb4cdfACyBKKQb7M5cF+9PLYT0e81G4f9fn9b1b/HB3tyjSPidH/+ZjNmhoAmAGEEBAEEQcHcFNYlk7xbAl16KaICgBUbr+DAAAHcOg4GQA5F72q4QABQUaXF4cIIEsyUIaCilNBl02gNgFSbZ3OuOo2xy8vf7YrD8BVLi8GAIii1XrSAkQexxACAIRKsmRts/ItAADh4aYBu09ACJHdflx2du6BJTPCz1k4eNDwrwBEQFmEutr6d6+55ppGACAZWckvhIUNnVBTU5PdZ8AhuvZ8BlpvAYPLF4Hs1taaaSN8W9QcDQMgoKLyjBM9lwcJQoxGHdUBgNvkXA4Ayxe9eFV4WIjmbq2auSfEj5sgSzI4eJ7317Azg33pjxs/G/FCY2fYp4TE/QRgQNd2RIJ0wmbmyJGH6A1UKFGEQzneCmDwSCIEADx37YkrGVX5l6Wuz0gIDgGBF7FLRrkSqJxqsbPJLd3OxGc+2pZ5eD64ursSw7GBpviHapZMRUBgGEIkHptjD7a1u57NwM4e8KhK/q+8vJRUf3+/mymlvnV1TaYLL7z825SUFG727NmiRqMao1Kpoa2z64M+OdZGo5E5qv/AwGKU9HpKDAb59w9v3BMawF2OANDSLSTd9krcJX+1jHtPtRpnzZulfXLO0LsHaZkng7XMBQgy8E6RV3OsirIUWrrEhJom6bmnPo3LUPyePmo7AgA4cuRIn6+eOqc4xI8bISNgh1UsUnHMAYZS0m3jQyQJNZS6SgOyDABDGCAMORTlIEoIkiyBhDLvw3HN3U5BIpTUA9IGmxXreUmqaGnmq15ftqvxKG0Pu96KYOMhUu7L9So1Y9Z9PC85xI+ZQwGgpVuKv/WV2CjlmZwRfBzx7FZ7SEEpkWyo0+lUTz/92F1XXnndT+zxB83VJ27v3vhLNFruntkXXPb0QOs1EA8uRskh0BSGZS6z2XlgKZ32/KPXhRBC2tw7A/jnBvtwqoter6eREE+jDAm2R2LhBwD48dtXr9YNDdG+ONhfdaEkSWC3887QAC7CV0WTfn3/5vfu+nfdhwaDQTCeiG83ciQQQkCSZdBqVKS41hm74MO4l076RAJX4ml8bhNRQObqmZDQp60FQgAff/ymYEpgkiBIqPVREV4UCzyfyZkAOEQEs9nMejQqJe6Nbxc3ZDLxJpPpJ0Q8dpM8d1wYJYSI+/aZ/zFt2sSlsiz5LV++/D1CSMNAAt2hkChe3C2IqudkBMHPhwmcOlxzLgCYTUYdhei/vmfmjlyRFZPzrrtM0r8+3PkrAJh+eHv+3UN85VdC/NQzbXYeCSA7NlTz9vqPR9ycVDLogehoU4HbxDxuLceR4KpBoiSgqlj0cacUsZHThwigCz/yucT0cJIYAJPJVeT1aFbXI/EUDyeeJpzQWJh0OgomkxQ+mEz1VTPBsiQKiMA5Bcw4E7mCqKio3p4bcft7EnssE1IJTcnNTXl71Kjhb/r7+0NxcemOiRMnOl3R4QMnME6nM8kAACV1/P6wAMaiURGtRkUhQCNcAQDm0D7uWZ3IoqdoK6NRx9x1l0l65K31P0N4+Jqf7x73dIg/81aADwnosDgcoQHchVdM0+wLeOrqu6IMO3f0BXSjRo0CSg5vZRGKcpQhQTTrI/qemW3og3r7CxIa7hrTYA4u9tFQsNkALHYBm63SQQBXD/ATBXE/1GxMTEwM9sG8RkXj/SEWz2w2s9HR0dKHH74SWFaWZ5o4cdybkiTJ+flFn02efN7cK664oj3G1WZ3wADOZWLr6QfLdjU6BEjUqFgqijKoWHKt6+EnnDLTxt0T3FVGLi+Pv+etTZ8dKLfNbu6GHQG+ao3VwfNaDkNmTfaPXfL69fdHGRJE7EM6kNs9cLGAtP/V5omMiXSTn3gtygAMpZzdgfWJFZ35AADEYBjwkcyEEMmdKQGIZtZsNrNucvGYyxU92l+LiooSt23bNu2eex7dPW7c+Ds7OzoAAUl1db0JACSz2cwOxF5x8THxFABAlMk6hjLAC6Lkw8KcD569fiwxgKzXn9rMCWUz3ayPYN/6z47i+S+vv660yfmhWqVWiRKIDEjc1JHald+/ecMLxF04qK/nZpj+lfTh8t8M8qv3XB6sZslFDqeAGhULThn3mkxJdveCMqABZzQamT17dlwbG7tqvCucK0qMiooS3Sw+IiLTEwDpUayKmJSUMP+88yYljho1aWZJScGOosKyZ4KDQsi4cSPvQEQSGRk5IAcoHlwrbn5d97Y2Cy8gAgT5caoRQXSeS8tFnI5Zi1GGBFdaEOrJfW9seq2kjn+UUIYliCjxojB5uPazJW9cd0+UIUHsa+Ir18+SrGL0EQwAkMkTAq4M8uNCZERBBgCrA2MB+h521k/NSAoAEBoaeM4ll8zeNnv2RUXVNYUplZUFizJzUu5JTIyf5OY+JE8AHgE4t0NHMjOTX58+fdI6lYrzLSnJMUyadN61l1957X+bmpscKhV7XUxMDAEAYjabB9yGucFgkFGvp4avE0rsDtynUbOMJErgq2Hud5lACdLpvBZCDJiyZBb3yLubfiistT+AlGUQgKAkiGMG+3z34bM3Trsr2iTpPfLulKcWHq5FSg9bGSzbvxAX4yKp0FdDdQwlQChh27sFS1Ets+10j/WpsiZZlmOaWlrNkiR3DwsLnTV69ORnZk6fsWrixDFFNTVFOcXF2T/m56c+snPnlnM/+eQT30OAc5uIck5O6jszZ059VxAkR0Fe6WcbNmz7LjY2dmhERISqq7NrV1Bw8Eye5wcRQoTjMDL9WMu5zEqbQP5HCQsOQRADfJnZi/XzzwPiiiQ5nYvl7IWpQsqSBdwTH279qajatoDjWFYQZSlAQ7UTh9LlCMBMz8sjR/sEHRUVGhkkDSIgEASuH7lwCEBItEla8I+IwT4qMs/mFGStiqM2J+74YNnGRldJdBjQESYAgFdccVXq8KETrtqyZc/k3LziiOLCrNeqq8s2CqJYHxISNG3ixGkPTp0aviwq6tKMm26K+kzhR+hhE1HuAqDAcaxm5nlTX3niiftrzr9gct1P/1tS7uvnO4sSgo88cvfykpKMtw4ciH9Ep9OpTg6fdRodeVdUBySX2tY1dzk6GEqpn5rQwZz4MgHAQ0UaT6PMXrhUSFmygHv8423fVTQ7F/lrVWqLjXeOHKS+6Lt/X/9ktMkkodFVQzJG7xrroUGaIEASAIgyAICKY/vNfla8y5yEi6do7hgcwAXKsiwKCNDugOUAALkD2Jw8yrQkiEgfffTR5nPPvWj35KnnfTh6dPgta3/bOjk1NfeSgoL0Z8rLS1Z3dXXXCoJYCAAQGRkJrEJXlpWlfSUIUrpKxU5gGGaKj49mNKVkIkPoaIahgZIswcSJk28CUN80YQKB556Ta00m01ZEIyVkYEShEHCVRyDRptaLDXNNYUHaxyw2Xgz2o3e+91TEOzqdqVCn0zGmk5zH1gfQiWjUMZP+nf7KJ4+FRw3y485xOp3S0BDu9Q9f0a0AnanLHX8JAAbw8+VCOIaqZJAFAsDYedGv3yxqLrqfDfJhnhQlCdUsy7V38aU7Csu3uCJPEs6I/FM3cYiISEwmE9XpdAQAZEKIBQCS3D9fzZs3Tzto1izZ/RmRVVyDW25ZaAOAbUef+OWXX/aPjLxk2NChYaPU6vrRhDDjhgwJm6rx1Q5xHaEbUAMV7Q5Rr7WJX4XaxYdZAAzQcqpxYb56QuAfaAQgpz+MHU0AUFJS4qzqmPhMoFZllkSUQwPUQ8YJjn8RAh+Z9RHs9Ol5CACg4eQhao4FnpcQEMBiFQcDeOS2/U2i10ewxGAQ//t/198a6q8+x+HkeX9ftaqzlf/aZMrj42MiWCUQ/EwRN/AkT83nAmAoAYhEQogNYmOhR5bSg8pklQIon3zySfdNN91eNGvWZTtnzJizfPr0WW+Fho6MnnXexT+5v3BArVgmkyvB9NWPdmR3W+V1vj4cZ7E5hbBAVvf5yzfMJh6NBE/rQhBtktCoY579YEtCU6ewVeuj4uxOUdYw+Mx9913rG2lIkNp3lFEAAC1DRnMsBXA3XeNYpl+YlDEuJpgMC+L0AIgcS5mmTr45v7B7OSIQxaQ/A0xJxmg0Mj25U64qb9ESIVEiIURyR2z9cVuAEIIeVKZysPsL9BTReCQYB/KomVzOfW2X+LHV4UpI0agoMyaE+RIAiE73t10WIAJpttOPnCKCKMpyiD83/MoJ3E0EACcP83MlO1KcQgkFJK6QWdIPSBOzS7vJK16/6YGwENVMu4MXfNQqpstGPv3w573t8TERDAE4Eza7kRAiuYP5EfHQXluvpucfANf7FxhkQqKPBOMAHrRok0kyGXX0uQ+3HGyyCEZ/rZqz2gXnsBDusu//PffxE914PplaDgDg6fe0ezosQiHHUZYhgEEqvBUAIPLmKRIAAMPQc2SUgLg7cf8NxbuOMiVdraleffzy4MHB9ENRFCS1mmUbOpzVuyrJN3q9nkYNcO2GiIyL9El5ubq6+JuUlD0XAwDr1mSHok16A1+fANfLBVBEZPpS/LI/Sm5uOCICKW2wv95hERwsyzC8IIojQ9UfvvHMNZOuetu1QX3aWb6YCAbAJPECjVNzLDhFkag4MjsiIoIls5cKT+gi/FQMnMcLkqucCfz9NLG7NZU8fVjAZyGB7FCHIEscy9Imq/TqDz9s6HZn1Q/kNZoAgPz0oqfVvn7ap0aOnPCvadMmJ9bUFqWVVRS8tXevebYSbeJOTCUKQP8S4BCRuPftwI1qaSCGeQG4Np9NJh198ytzaaNFfN9XzbFOHqRAH+I/c6j6R0Qgka59u9M6n+Pd/9p5cZ8oySBLiCyBEZePk4cBAEwb7Tfbz4cJFURZBuJy4ujfqOD07mDrL1+69rbRg1UPd1sFPsBHrappde56zLD5Z+NJKCnx92s3IyWE4D8uvO3ikODBo2tqCvObm5uLRgwbes64MZMM55wz+WBtTVFSWVnuS3v2bJ6umJ1/FnDEaDQybhIFo6KiRESE5GTz7Jqa4m9T0hMe8lS5A8q0jDbJRqOO+WGn7YPaFmemn5ZRd9tE5+jBmktXvTPvvShDgrhkyazTalrm5blTiexYbHNKMgAQFcuwc84ZowYAGBxE5vlqGEAAGdBlU/5dFfv1ej19++0E8dkHI8ZOGKb5DmRZYhlCO2yio7zR9gQiEKXExcAWHQEAGDJk0B3+/iFQX9/yxtixM87JyMyeV1qa+73NZq8bPmLYRePGTflkytSpOdU1BbtzclIec2s60ifA6fV66g7hcrMuRIqNNQ4tLExfWFlVsG/ylEkHR4yYuDAkcNCLAy1dx3PxAhNAQkKCWN0iPmxzoMByhLHYnMKIQdxrX796zf0LF6YKSxbM4k7XBSl1PuoarN1OQZYpJSAjEEGWBYiIYH3URMfzIgAg/XsHDkjM9DyCCPSyKf6rBvuxgxyCJPqoVWxNi/TyK4vMhSaTjhoGflY3IYSIc+fOVatUbHRTc1VnZmZBPADw559/2aaJE2c+tmNH4rSM7Lzbq2uLV4qC2DJyxJQr1Gr2Prf1R/sEOIPBILtDuOiBA3uuqazM/3727Fl5kyeHfzt61KhL7TZ7Q2lpzuLubsdjHmzMgCRQ9PoI9tnPt6RXtTme16o4FgARRFEaP1S7/INnr45cuDRVON0kilW2u4rSUwKChNKvBzLql0X63DokgBvt5CWJwN/LlMTrIxgSbZJ+fvumpWNCVZd2WXlHsK9aXdniMD72/qbFZn0EO9BNSYWrAAD48D39TaNGhYdZLdaNjz32WJuLrXdZfvfff3/X+TMv+n30yGkPbt4cF15QkP1IbW39q4fXpsPS0yQiiAiJifETQkKC7ggM9LsrJCT4fJUqGFpbqqG2tnJLS0vb6oMHs2Mfe+yxNjgDxOBK3mSjDFv/+/N7N82cGOazoKPL7vRRMapzx2h///iZqyKiDLuy+pqR/RcNNQAwwJjQQRzLMoShBCiVq231Ib6hc7iPRFlEJAT/Tord1RM9QVhpuPGNcUPVj3Z1804/rUpT3yEV7MuVHkPUUwCDdNwk1wGDOSR79+60NzfXJbe0tBrdfj0qEVZKdQS34mkGV1EpRT3KvQLOnaYjFxSkr5gy5bxLJakTmppbi+22+l9qyxvXXHnNNVlHUaU40Cp59SRRhgTJHfb1+K8f3DR23GD1de1dTqe/Dxc0Y5zflrefiLo2ymDOPdWgU+pkhoYwwzUqV+8HSaRt/7jO/4uwIG58R7dTVKsYVpIQpNPf/4mkLJnFzl6YKqyImfv0hDDNO1YH7+RUVNVll9sza4Xbv1q1pes/Ey+ixHBmtMrxmNub3T8UPDK4Pay7Q+CLj49nIiMj5Z5wQY9BgYIo4tKGpuoN6emZty75duXMCRPOeevKa67J8twOcLOUZ0qjdASdSUZEXP57oa62Xdgd5K9WWx28M0BDhs2ZHLD9/cevvDDKkCCeSp9OKffAMXi5Vk3BzksiQ+XZYQHsgx0Wp+Sn5djGDjHL6hTrWYZSPE2g0+uBolFHZy9MFZa9cd2L40J9/+Nw8jxLCCvK1JlZ0fmPtxbF5RuNOuZMqcZ1tGnp1mTycQCKHnlwx2cpFeTOmHHBimFho+dfeGHEeoPB4FCyV4+1HeAuFcYM7NUMMCaGkK0HSrp+3ma/pa5dSgzx16itNtHpx8Gw8yYG7vrspevmevh0J50fjHSHR3EsuUOSZAAZiUbFMICy5MNR0mGTWpOrxLtYlrUQenocZ6NRxxgMIJNok/TLe/PenjLC71On6BQpAUaQCc2rsd/76qL4rWeK33YsTXcyeIreQlKO2NzuDbWuDGZX15Dj7bT3f38O5Lf0emrasaNzYyFe39gtJQQHqNVWp+jUqonfjFE+m75768aFUe7mjCcz7lLRDp+/NPfaED/NuVanKAMhjCQjEkoQKUsr650Pfb50W4EsQyDKpxxuRAGRbu7E0DUfzfttTKj6TbvTyTOEMILMMHm1zruf+Xjr2iULZnGn3r8d+EJ7UaHH3dxWNNrtunm3t7U1HEhO3hNNCJHdgZ0DGHQGWa/X0x9+2ND9yvrieTXtwrpgfx+1IEgCAzJMG6751vjhvK+HDp2ljTYdCgP7q9ruUMm60YOZD1hGBoLEFStJUPTXqtnyFmfME59si3355Uf8iasP0inVauAuCbHoteui7r96atLIYNXtXRaHU61iVU6JOPMa+Juf/Xir0ayPYBcuTRXOJGAo+W5uhcMiIms2m1n3njQ9Oij5LwPuGOwl6cnfkyVpZnBw2IXnnx/+a1ZW8gvR0dHSmQK6/N15ltv/b+PtRXWORWqNiqOA1OZw8mNC1I8veWHk3k9evPEi98qOfa1B0jPzt4CNMiSIK9++UT8iWH2B3SFICMBQACHAT82VVNuWPvRWnAH1QCsquk+ZJtHr9RTd0SG3XHqp/6/vz/sofJhmR7AvM77T6nAE+qrU3Q6oza5wXvXch5tjTw9z+7eYkOihcERCiBgVFSW696Rlz3w4Nyj7hCXyJ5BPD7dQdQFw27a1ocOGjbht9OhRHyCSoMLCggsvvviq1IFeIt11j0AgRk+IwSB/+/p1D48L9VnspyZai11w+PmoNBa7KLVY4IOP1lR+mJWVZUW9npry8khfmzHq9UBjIiMoiUoQv3jpusfOH6ddKguiJCASlgJqfTRMWYPjP/e+telZZXLrdDqfhy5yFPuoYIRaxUBLt7Ru/ssbb3OzrNKfBVrM9DyifP6HN2+ePySIfBzqz03utjolBJAD/dRcXbuwd09B932f/WCuPBPBphQ4Xrfu5+EzZs78kWHYLpHnq0RRrLTZbA2yLNe0tLS1dHfzzdHR0d1wgpZGnzdzt27d6puSEq917zO49yEOmZpNALAkJWVP96xZF64aMiT0FQCI1ul0Z8BKBwhgANfk2rb8i5duTJ8ynF0+KEB1XqeFFxkKZPxQ9Rvv3j9a19Q9/E1iMJgUoB6nGSMx6yOYKEOCaDAkyEvfuuHJcaHqxShJMi/LoGIpZVgOCuudbz6k3/Quop7GxJx89u8w0AySAQAWvTL3/NGBrD44gJnPUIQOq8Pho2I1MlKmrNG+6O434l4GgENNF89AN4sBAHHixMmPThgXfq3F0gB+fv4A4AcAPEiyDTo7usHpdHZWVxe0SJJcq9VqmqqqavfNnn3Fl54K6U+ZlEqFrrCw4Jv/9fhzefv3J9yLiGA0HmYk3aQJ89VX36+tr69s5VTsdXq93s8jAW/AL3zuysbs85/GZbxtVF9a3Sr/h6Ecq1GzTLfF6QzU0imTh2qN6z65ZcdXr143j5BD1ZfRrI9gjTodgwBEr9dTt8+HUYYEccFtVwz75b15y6cO0yymsiQ7RVny81ExvMQ4c2qsDz6kj33XrI9gCTGg4STtben1QM36CBb1riYiJNokffTyTZPWfHTrt1OHqQ4MHczO5wVBcDglKdhPrbHyUJZba73t7jfiniMEBL1eT89UNhIAJEQkjY1NP9fVlZUyjFrOzs5en5d38OWy8qLVtbUN+xwOZ6Usy/6DB4VMGDNmzJWhoePupJRY+mI1HhcMSjOPrKwDb51zzoWGwsLsF6dMOecLAGAIIeLR5ykvzynVarVjGhstw2fOnNk40Bp/9EUjKPGBP7x50xVDAtlPB/mzcxw8D4Io81qNSiVKCO0OYXeXTV7yW0rnxg0b9ncffZ4H50cEXT/H9/5ADX09xJ8Ls1gFAQmSQF8129gpFmdWdj/05lfm/UeZbQRc3VhO1KQker2eREI8jYyJP4IE+/zla2aPDNI8FailukAtq+2287KMKPn7qLlup4hdNvxmS1rzm8tMSW1K00UAQDiDxaN/9/kXXzwzgWU5Ydfu3TfeOk+XDACg0+lU8+dfP2zMmNHDBg0aNLbTYg25ZM6VX58kkzJeuYwmABFF0c65+2Id0o4pKSnshRdeKKxbt25cQEDACLvd1r1zZ6rzTHwYbrARdLUj3gMAl618Z97CUF/yarCfeqTVwYMkoRCq5a4M9SVXLriSqbj/0lt+a7EKv+dU8AXjh6uGjwjm7vbnyAPBvnQkL0rQbXVKWo2KQ6BQ0yb+uCOz7YVvft7b/md9JMXvjId4Gjl9CJJok2QwGNAAIIOBwCcv3jxuVDBe7aPCu1UcvSpIyxGLzYlddonXqjmVhECbuqXtde341hPvb0xSWEty5mq1owkT2Ww2s1FRUel79+6874JZ56+/4pI55ri4tdfdeOPte41Go0AIqQSASnAVC+r7uY//8PSUEIO8Zcv6iRddPCvfbrfXrVzx28xXX32182jtVVSS8fukCeG3lpYWx06cOP3m49mzA12MOh1z1xpX74An77960MUTVM+FBqofDtTSEbwgAi9IAstQTqPhwGKTwMpL7RyFoJAAFeF5CZyCKBEChAArOWU5q7FTev8RQ9xaRCSb/3ODyqfNLilL3vTpQzA3t4lMnz4E403gc9NFjgJFw7VaxPX+3d13hk4fQs+JNvE9qB/6zRs3nROgonM1GrjJl4XZAVrWR0YEu1MQCQDx8eEYQQTosEmJXTbhgwf0WzYCuFoJQ7RJJme4VuvNuktM3PXixRfP+bSuvr4h5WDeFfPnzy9JSVnCzZq1QIqPd9U67WutVtLHL2YIIVJ27oFvZ4TPXlhdXRzf0NDywpw5X2bNmhVMv/zyzlkjRgx/a9ToETe0tbVjTXXdnNmzr0j59ddfBzxL2TczM4I1uDXRCwvmDT5vJHk0WIsP+2u5KSqKYHdIgChLhBIGEWQJUCYAlACAimNJYyfGZlVZ9e9+uzO9r9+56bNbqnw4HKVRM9DYJZtue3lDtPLeaw9fHjp+XOBoLSGzVRxcrGbgIhVLpgVqORAlCZy8KMsAspqhrEqtgk6rINoFsqWtW/r60XdiNwMcbiVsOAPDtHpjJ49+3d3FVMjNTflPePh5T1fXFOfHm82RDzzwRJOne3HSNJwH/U9M/zVpZ90wZcv48TMua22tAafdWQgEqFarmRQUPBTa2hodOTk5T0dE3PD9ma7d/jBGACTezTq6XpmoXvH2lKsDVOw9Kk6+zk9DQ1UsA6IkglOQAWWUkBAEQIpIiAyAgoCNQGmhIMiNMkKVKEIrEGyRUGpnkbW2dDuIIEhoF1n1zHGaH7QqMogQgl1OrOYF3KOiMJgADKMMjFGxTKCvxsVrCYIIoihJSICoOYZyHANWB4Kdl/ItgrSuvp1Z9cLHG3PdrCz8+uvAz9L+M2DrCXQefRIhPz8tdurUmXPLygv3rVxhvCYmJsbpNkHxpALO86L0+iXaB+6/zOAf4Pugn59vKABCd5e108kLW8vKqj6MjLw2HY1Ghhyl2c6EPbm++k/xMZ7AA3j+0etCZgxjLvX34a7XcBDJsTAtQEsZSilIkgyCKIEkIzCUAEMpMAwBSggguFrZyoggywAywqHWtpIkASIgAgLHUKJRMyDLCJKEIEgyAKJECBCGoVTFsoAA0GUVBR4h3cZjvEVQbXhM35qs1IlUenZHm84eoHnKPY/fE/zzNz+3H4soi4mJwaVLlwZcP/eK3WNGT55ZUpa79n8r1+piYmJOqO99n/fhPFYAm8EAL3/99dfvX375BeNFkdLq6pKq+fPvaVSAdTTYlMyCM42x7HmcAN2TmBiNOqoDABJtagOAWPcP+eKNG6cOc5ALGEKvUDF4MUthnJqjARzHAAUAGRF4SQJJQpBRlgkQRI/F0b3kUkKBuOvLgCDIwDAEOI4BFceCU5AZKy85JAFKJFE8YOfJvs5uec/jn2wq9rxes97Vs5sQg3y2gUyxwtLTD84JDNIui77+qkvnz3/UcrTWMhgM8vTp05mFCxd2rl27+vYAf/8MjuPYE1VaJ3zwUSr2aFApCXg9PrjYzWuumXfDnTvOxtVT2QQPzW0iPbGO7z1zVdjoQYGjKZVmcIDnMpw0hhAyVkUhDCgJAUQ1BQBCKLiKHCIgosxSxiLIEhIg7TKQDkkiDaIoVYkiFlt4yOlwQP7Ln8VVedL4igZuzhuC0aYzn+LvCzdRUJixfsrkc2/JzU35ZMaMC//PzVD+4TkpVtrOnZuuYhiuPjLyuvwTdZ3IX7hYYjKZKACATqfrMXXBaDQyOp1OTkjYMWPGOVMzautr/nXujEu+QzQyA6UfwalQgnq9ngDE00gAuOrtBPFYKW333Xet75QAbjCiReuvUoHKXwU8zwOACuxWpxQ2fHR7bmUZbtk7pCsvz8Qf6wuV8hDxECmfLSRIn1hmN4D27dt2dXj49DhREmlGevqc6667Nf3XX3t2gf4MUXJaVxAAgMLC9A2IiIVFmb97vu6Vw1rHqNMxZn0Ea9ZHsOhq6XSipiwguqJYzPoI1mjUMe6ursQ7wsefo9nZiV8hOqWiokwzuAL1md5M0T9bs5Q9lTdCCJFSUvZGDR8+7Obm5or2yorqfx8m9bxypN/3R7ICAYirRZW+x8/FxLj6ZLvABuj2w7wa7ARYSQDA77//3j8wMOgyUbTQUaOHRx44sPshQshyZQ73wGfI/fGmKADQsrKsZES7lJOXss79Out95F75O8Gm/FtUVKQGAMjLO/A9ohPz81N/b2urcjQ0lNYbjcYQj7y3gaGmDx7c84DN3oSdXbXY0Vkr7t699U4AV0C0K+DZzJ4hwc1eGViiNBOFxMT422XZhuXluQcBgM3NPfhfRMSsrOT/KnO1368giEiNRqNPVXVBKc+3Ynp60gfNzeVtrW013atXr57sfd5e+ZssLtixIy68tra4pKYqf2dubsoHDQ0ldR0d9e1ffvTRJMVirK8vybNYmnH79rgLFXJlIGi3VxBFLCnJ3AcAkJGdeIfd0S5VV+envPLKh4Hbt2+aXFFV8M+tW7cOUdgf77TwyqkEHCKS7OwUkyx3IaIVESUUhDZsbCzhq6ry9hUWpn+WlXXw2s2b10bbbO2O8vL8JE+w9jtx58WRVRtWDa5vKGnr7Kyz/PbbbyMBAEaOvNgnL+/gZqezBasq8yvr6ooREbGsLCf7p5/iAgaMveyVAe2/bdmyJcRs3npeTk7Kg6Wluf+prMxNrK0t7BTFdkSUENGBdTVF1vq6Qpssd2JmzoEnT6ZpeVLt05iYGEIIkfMKUl8dGjY6+ODBpGUTJow5r7q68COGodcHBPoNQgAYNDhkdFdnd31leW6S0+lMVastakJIlxdwXjmV4t4rbnP/ZADACgAAY5wxdFzo0Mm+vv7n+/io56hUzBzKMGPb2ttlh9VaCQDQ3Nzcv5h1xSTcuXPnhIamUuzsqMbGhjJE5BGxG+vqi9vLyrO3FRamvbZ//+7L9IsX+3mngFf+Lo7Bo5tvj4v81oSt43bt2jqrXy8giEhzcnL8yspzdjQ1VXTW1RVvLS/NeS0pJf6KL774Iqgnu9qtqonHYHi1nFdOOwhd5e/M7t72R77Xry/cre20y43GoT07rodq+5HeHNyBXmbPKwPb+nTzEXSgrRwUEY8LMEWMxu9CVq1aFez5ee+z94pX+mYnn1CuHQBAQWHGrq7uus6amuJtiYnxN3pB5xWvnBqAMgAAeXkp31osDU5J6kCeb8OUlIQ3XZrPa156xSsnXZYsWaJNSNg8LD19v66lpaJRFDoxIyP1dk9QesUrx3BdvGRb74Ok73WQdu/edrXN1ipWVeWXffb88z5/tnmCV858sHlH4S/4fnq9nqakpHAAAOXlOUmC2I4JCVuv9JqWXjmW7280/hS+cuVKX++20jHks88+8zGbt0z8/vvv/XsaIKUnXUVF3q+IvFRQnH0XwACI3PbKaRMX8w0kJWXfTZ2dDY68vNRV7rlzShplniw5repYiUYJCwsbMnnyhPzLLjs/gRCivE48Vy1CCCEEZoqinba2tVu9U8wrnqLT6ZAQQFEEuygKOHXqxHsyM5PfIISIKSkp3oXZBSZXjzm9Xq+qrS2p6eioF1auXDkOACAnJ0fl3ulnAQDS0xMfcjhasbqmoHP58sVDPcHoFa94uhi7d2+9s7urHu2OZkxKMt/voem8opiFOXkpryEKWF6es/vndeuGex6TlpZ0Z2NjeReiHXPzUw2KmekdPa/04H6wAAAZqclPW21N9uaWCue2bXERXhfksBB3ERZVRUXe74iIDY1ljXl5Kcvy81M/KCvL3tHZVYuINiwtzV6n1+tZNxNFjnaYDwegHgpE9TJWZx/gGACAZcu+Ht/QUNokyZ1YV1fcuGbNqkmeWvBsHyTi9t3YvILUL1taKkREEV3iwNbWSmtZWfanERERbE/MU2/Acj8Ar+l5dswjCgCwefuGS5pbKls7OmqwtDQ7H9GClZX52UuWLAn0MpceoFN+X79+/cSCgrRH6+vLXi4oyHh0y5YtE93ECRwLbE888YRfdvbBB0pKcv5bUJC5orw874X4+G3Tejq/VwaeFaTUvekNbIhINm5cM6mhoazVbm/F9JTEhz788MPAsorcPEQBCwvTNysWlXc+HDYvmb5qKgVse+M3X9HQUJqPaDmUpYsoY0d7DRYXZS568MEHNT2B1SsDc470ZkoWFWWuRhQxKyP5RQAASgmsXLlySE1NQS2iA/PyU77vTxzA3+1UIiFEcjVLiKQAkRAP8RAfEy/3VEqdUiLv2LH5golTJ28OCxvqW11dVdrS0v6DIEvF/lrfCUFBvk9MnDTzmTfffHFyQEDArQAgICKc6f0MzrAFGDZu3OgzZdq45zvbW+PmzIlM76mcOKWu+aHV+pwrSRap22pJBwCQZYRp08bf7h8QENLe3mzlRXG3Un/SO7wnaK/r9XpaWp6ThOjAsrJc81dffTXI85hly5aF1tQV7UaUMCcn5a3+tLJ55fiisIrZmQdeRkQsK89OXrBgAacEQvSk4QoL0/6H6MSyitz8wsLMxwoL0o2IPDa3lNdt3775kuP5/F7pxXw4eHDPtXZ7CzY0lrb89NPSkQDK/h0ySlHPrVu3zujsrOerq4ualyxZEuj15waWX4+IZMeO9WFVVQWFiA7MzEx83/XekRS/EotrNseNrKkpqEB0IqKAiDxWVxckGDcYR3uC2Csn9iBcK1/2gQ8QJbm4OPN7z9ePIkpIVVV+vs3ehJmZ+y8A8FLD/dV3Jz00UDi8mb0roq29RurorBX27DFH9vQcD8dSGkeUlWW9V1qavTwlff+94I6g8j73vwy45G8RUU5N3WNwZ5H/AXCzZs3iKqtyS+2OZszNzbjIO/AD0LR0a7OUlH0fITqwqjq/xE3x0x5MS9ITy+Ktc3oSAJefn/YsooiFhRn/c7+uOrTK5RhVAADx8dsv7OioE+vri6xG448joA+Dj9i3EhBeOTkmo9FoDMzLS/0yLy9Foe1JD8cxCxYs4EpKspMRBczJObiyJ9NSeb7uwAfGsyiVV/70g3IBZsuW9RObm8v5rq76roSEHRf0ZKqUlGbHI/JYWJjRp9ZYXof61ALM7K6EpQALEcmyZctCq6oKrIhWTE9Puqmn56RYJfHx26Y1N1dY7PZW3Jdk/kdfnqlXTiJxkpax71VEBzY1VzQmHth936JFiwJ0Op1q166ts8rKcmNluRMbGoqtm9evn67UIOzFj6Auf2H73N9++22Il2A5hU6ba19UKYsISUkJzyE6sLIyN9HzWRxhWrqPTUnf86QkdWFdXUlLbGzsUO9CeZpEWfXS0va9a7M1I6INa2sLW6oq8ytbWisQUcDm5grr3r3b5/f2UBSTBQCgqCDjVVFsx9rawsKioqIAbyjQydFsAACvvPJKYGZ28hv5hekLPN0DIxqZjz76yL+mprCc57swPn5b9LE0l2JCFhSkb0SUsLQ0a98nn3zi21N8rVdOzcOkAABbd266qrq6cH1DQ2lnW2sV39RU1lhdXfTzli0bz+2NKPEEW3r6/k8kuQs7O2uwqqrAsmzZslCvljtpftroiorcKkFoR4ejGSuq8vbs3r0twvM5FhamL0C0Y2FRWvasWbO4noPUXfT/+vXrw2prC1tLSrK2fP3118HehfG0PtTDYFq+fHkQIg4xGo2Bx/PLPE3MvLyDXyLyWFSUUV9bW2irqSmqX7BggdYLuOOLi6SAXgv5Llq0SF1cnHnAbm8W6uuLHYhW7Oqqw7KynO9iY9eMcZuMmurqggpB6MZ9+1z+WU/7ZsrzWL169VhPK9X7JE6zeXk0sNwRCfTYk8TVlbWgIO1bRBGrqgoLv/vumzl1dUWW6prCSo+HSPrw3YdSg/pa6PZMMhf74m/v3Bl7pcXagA2Nxa1pKbu/bWws60KUsKmprKWgIPUVAGCysw/chchjeXl2ljuqhPb2ve69Oy/Y/m4TpreJoLTRAgCak3NwNaKINTXFFf/73/djzObYod3ddVhalp0PALS3B3q873ED/oxn0Fav/mHU999/79/b4qSMQ1l5TiyigElJ5td+/3312MrKQqPV1oiIDmxsKE3Zvj12fllZdrYkWTE1dd/dx9JyynP0gm2ArMjh4eGqiorc1YgC1tWVlK5e6+rEun/3rsuczlasqSvcczxzVPl9z57tl5eUZD6bnZ34flFRxr8SE3ddNHHiRPUZPo4MAEBW1sH3LJZOS1JS/CPgTqPp8Xi3D52SknhRZ2e9XF9fYl21ZNVg12v7rq+pKUpCdGBnZx1WVOTWW63NQlV1Qc7+/fu9JREHuvZbterr4Pz81K2IAtbWlZZt3LhmkrJiJicnXI9owcKiDPOxAKdsnK9Z878xJSWZu+z2JndakOtHFNuxtq6oqqQke1la2r47t27d6tvTpHFlpv+9sX2em8TuDX/GnTlPjjOWrAtwia8iIhYXZ63xBGJvvnZxceZGRAkzM5M+83ibzc1NebyhobQOsQtt1ga0O1owJWXf9Uf76V4ZQH6eTqdjsrNTfkZEbGgoLdu2bdskxWEHAMjMPPgPRCdWVxf90tMEUoiWFStWDCovzylCdGJtXXFNSVHOu+XlhQ9lZx98vbw8J7aurrgbsRurqgukn9f/HOYJ1BO5XrPZzOIpCks6nrnb2/Uq78XGrhnT0lLprK8vbjcajSG9+XVGo5EhhMDu3bsu6uyslxoaSjs3btw4wl0SgwIAfPnll2EFhenfVFcXJeTnZ0fFxcWpvaTVwBWi1+vp3r17h+fkpD6flLQj/LCv5S5mlJP6FKKIubkpq3sGnOu43NyD7yMKWF1dmKO0UfaU5OTdjyE6hbKyrI2emlKZqGvXrp5cXJjxQ0ZW8gtGo9Hnz5ARfxFsFABg1apV48vL856rrSv5pb29ekNtbdGK/Pz0h3U6nc/xQKeco7Qsa48s2zE5eU90bz6X53jm56duRJQwLy/to8Pa3hsxcsaLMqEUE6mwMD1GkiSxrCL3C8/XFcAiItHpdKqyspxSQejCAwf23A4AUF5ernGbYmpEJKWl2asQRczKSnrK8zwKkH766aeA2toiG6ID9+7dcYWSUmQ2m1ml0vTu3bsuqyov+YfSefNkgdAjYOCZ5uYKK6KMPN+GXV11iGhHRDvW1RWnbdq+aXJvoFPuKTMz6WVECQsK0n8+nuZUtFxc3G8Xt7fXYltbNZ+RkTzZ9TkXmaXku50NQeZnRa6Qa+LGMwDxMiGGIzKHCZLhlFLGabfLPYCTEELk1at/HOWj1Yxpbm60tLR079Hr9XTs2LHOsWPHAiFE/P777/2vu+7y69s7auXa5vbt7o/LAK6+0u5UlK7ikszfALi7fX19LyWE7AGAQ1ntn3zyie+YMcNNo0ZPGNbY0vgqAKSCK81E+qtmJCFEykpPemrGueGL2tra5JKS3Lfb21vX2Wy8ZdCgoAtCBgW9PnzY6POdTmGT0WicnZub2+0u9HR0lrQMANDY2BLb3lH7oZ+f77WLFi0KIIR0GY1GJjQ0lERGRkqen4uOjpbcGdtJefmp64YODZsgy+jjQfOjxz1KXpVwhhMqublpk2pqylZlZSW/cPRqrZhQ69YZpzQ1lctVVfltH330kb+SJqI49qmp+64WhA4sK8tNB4CeUkjchMOBBTzfibW1RXXl5XnG0tLstwsLMx/avXtXRG5eyqeIdiwry/GMLfxLGk7RVKtXrx5bV1/s6LY0SImJu+46+ri4OGNodU1BCaID8/NT3+hB0x9t+pKKirxkUbTggQN7bj5eXKMy1hkZGb4AoIyv10/zmpo9+1X6JXptZVVevdXaIm3ZselaAICioiJ1UVGRGhFJQVHmp4goFxVlvdvTZFUm5JYtW6Y2NpYK7e1V2NlZi64iSAJ2dddja1sltrZWybt2bb3M0wz8a4uKWTEBn0TksbQ0a6f7ejij0ci4GEtXpnxa2v5HEW1yWXlOJgAwx6LmEXNUiMjm5BzQI8qYn5/6vevefh+VlXPg4f37d4w4njnsJUXOcqD1Tm0r+08HPkQUsaq6sGD79k3neB5TXpmX63S24b59CVe6PvMHsCgb5rSsLCfPbm+WEhK23xG/d8cVOTkp95aX55bIsgWzshK/Ox4JcYJa3BX4W5T+H0SUs7ISP1HaQB8+xuVH7d0bN6G5uUyqqy9u+/3334OOB4wVK1ZM6+io5WtrC+tLS7M3NzaU8YiImZnJhmNpSJemO7s121lf78FgMMgGg6G3Q2REpJ9//rnB11cze/z4aVdr1NzBktKsle1tnT+3tXUJwYEB0xqb6uvXrt2Q6vZcjvYH0T3WoiAI2zSawdNCQ4PDwsNn/7ZtW+y0Sy6ZM6SxsbEhMTHrNbc2PNm+DA8AwDAsC3+oXhWDAACSRGyCIEmUMuyECyb01NGIxMfHMw5H15TRo0dcExQUMJ9S4IYNHzpUFMS5LS1tBRVVBRusVsd6N1D/cA/e6mleOQHSBeDBBx/U5OSkftzYWNbhKlrTjbW1xbwotkmFxRmremPsDuV2peybh+jAkjLXxnFpWfZ+RAH379n14PEYvz9rUqan730M0Y7l5TlJrtdTOCX+Uym+tGfP9mscjna5qiq/ZO7cuWp3uZFDHY0IATAavwuprMhzICJarY1YW1eYVlWVp9+/P2GOh2/mFa+cPNABACxevHhoRkbi3dXVRcvr64srBbEdMzOT7+nNHFQ+//XXXw9pairjKysLsrZtjtUh2rCkJNN8ssHm6ZeuWbNmTENDic1ma5LS0vY80JOlU1qaHe/aJ0v90hOsR19/Ts7BL4qKMt7ZstOVBnW0Catk53vFKycDdX+oFH3ffS/6pqTsjYqNjQ3uA1lAAQDKynL2tbVVOaurCpvb2+v4rVs3ziCEnJJiR4dz/5LedmXKl/PZ2ckxZvOWqWZz3MjExJ03VlTkxiMKWFVdUPXzzz8PVzrRHu/crixuV7YEeFlHr5xKbfdnMgMUIiEnJ+1NSe5EQWjDtIx9H51MoqQnXLgBQbNzDnxjsTQgog1bW6uwvr4EJbkDES1YU1ucuHv39kMb0r0tGt5ORV7S5LSK52atwj4CgHw8UsBkMiEAQEND09bQIcEv2ezWlj0J2947RUTJIYxER0fL7pLvj6enJ8UOHhxyL8dx4YiE1tY05juszg2Tp838FQDEnsqKH3XvMrg3wL1y4vL/ECq9lJRIdjAAAAAASUVORK5CYII=" alt="Silva Pinto" style="height:52px;width:auto;flex-shrink:0;object-fit:contain;">
  <div class="brand">
    <h1 class="serif">Painel de Oportunidades</h1>
    <div class="sub">Silva Pinto Advocacia</div>
  </div>
  <div class="search-wrap">
    <div class="search-box">
      <span class="icon">&#128269;</span>
      <input type="text" placeholder="Pesquisar concurso ao vivo &#8212; ex: PMERJ, Receita, TJRJ..." onkeydown="if(event.key==='Enter')buscaAoVivo(this.value)">
      <span class="hint">enter busca</span>
    </div>
  </div>
</header>

<nav class="mainnav">
  <button class="active" onclick="showTela('concursos', this)">Concursos monitorados</button>
  <button onclick="showTela('inbox', this)">Caixa de entrada <span class="nav-badge">5</span></button>
  <button onclick="showTela('gerenciar', this)">Gerenciar concursos <span class="nav-badge cinza">3</span></button>
</nav>

<!-- conte&#250;do das telas vem na parte 2 -->
<div id="telas-container"></div>

<div class="modal-bg" id="modal-add">
  <div class="modal">
    <h3 class="serif">Monitorar novo concurso</h3>
    <div class="msub">A coleta vai encaixar noticias neste concurso automaticamente (voce confirma na caixa de entrada).</div>
    <label>Nome do concurso</label>
    <input type="text" placeholder="ex: PMERJ 2026 - Soldado" id="add-nome">
    <label>Banca</label>
    <input type="text" placeholder="ex: FGV" id="add-banca">
    <label>Palavras-chave (separadas por virgula)</label>
    <input type="text" placeholder="ex: PMERJ, PM-RJ, Policia Militar RJ" id="add-kw">
    <div class="modal-acts">
      <button class="modal-cancelar" onclick="fecharModal()">Cancelar</button>
      <button class="modal-salvar" onclick="salvarConcurso()">Monitorar</button>
    </div>
  </div>
</div>

<div class="outro-menu" id="outro-menu">
  <div class="outro-box">
    <h3 class="serif">Encaixar em outro concurso</h3>
    <div class="obsub">Escolha um concurso ja monitorado, ou crie um novo para esta noticia.</div>
    <div class="outro-list" id="outro-list"></div>
    <div class="outro-novo">
      <input type="text" id="outro-novo-nome" placeholder="Criar novo: ex. PC-SP 2026 - Investigador">
      <button onclick="criarEEncaixar()">Criar concurso e encaixar aqui</button>
    </div>
    <div class="outro-fechar"><button onclick="fecharOutro()">Cancelar</button></div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
  // ===== v7: API-CONNECTED JS =====
  // Constantes de integracao com sistema de marketing
  const SELECIONAR_ENDPOINT = 'https://silvapinto-comercial.onrender.com/marketing/selecionados/adicionar-externo';
  // TODO: preencher quando tiver a rota do endpoint de concursos do marketing
  const CONCURSOS_MKT_ENDPOINT = '';  // ex: 'https://silvapinto-comercial.onrender.com/marketing/concursos/adicionar-externo'

  const PRIO_COR = { urgente: '#c0392b', importante: '#BB904C', naourgente: '#2e9e5b' };
  const PRIO_LABEL = { urgente: 'Urgente', importante: 'Importante', naourgente: 'Nao urgente' };
  const PRIO_ORDEM = ['urgente','importante','naourgente'];
  const TAG_CSS = { gabarito:'t-gabarito', recurso:'t-recurso', fase:'t-fase', nomeacao:'t-nomeacao', inscricao:'t-inscricao' };

  function esc(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
  function tn(s) { return String(s||'').replace(/[^\w\s]/g,' ').replace(/\s+/g,' ').trim().toLowerCase(); }

  // ===== TOAST =====
  let _tt;
  function toast(msg, ms) {
    const t=document.getElementById('toast'); t.textContent=msg; t.classList.add('show');
    clearTimeout(_tt); _tt=setTimeout(()=>t.classList.remove('show'), ms||2800);
  }

  // ===== API helpers =====
  async function api(path, opts) {
    try {
      const r = await fetch(path, opts);
      return await r.json();
    } catch(e) { toast('Erro de rede: '+e.message); return {erro:e.message}; }
  }
  const GET = (p) => api(p);
  const POST = (p,b) => api(p, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});
  const PATCH = (p,b) => api(p, {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});
  const DEL = (p) => api(p, {method:'DELETE'});

  // ===== NAVEGACAO =====
  let telAtual = 'concursos';
  function showTela(qual, btn) {
    telAtual = qual;
    document.querySelectorAll('.mainnav button').forEach(b=>b.classList.remove('active'));
    if(btn) btn.classList.add('active');
    if(qual==='concursos') renderTelaConcursos();
    else if(qual==='inbox') renderTelaInbox();
    else if(qual==='gerenciar') renderTelaGerenciar();
  }

  // ===== TELA 1: CONCURSOS MONITORADOS (fichas vivas + sidebar temas) =====
  async function renderTelaConcursos() {
    const cont = document.getElementById('telas-container');
    cont.innerHTML = '<div class="tela active" style="text-align:center;padding:60px;color:var(--cinza)">Carregando...</div>';
    const [cData, tData] = await Promise.all([
      GET('/api/concursos'),
      GET('/api/oportunidades?status_triagem=transversal&incluir_lidos=1&dias=90&limite=20')
    ]);
    const concursos = (cData.concursos||[]);
    const temas = (tData.itens||[]);
    // Para cada concurso, busca suas noticias confirmadas
    for(const c of concursos) {
      const nd = await GET('/api/oportunidades?concurso_id='+c.id+'&status_triagem=confirmado&incluir_lidos=1&dias=90&limite=30');
      c.noticias = nd.itens || [];
    }
    // Ordena por prioridade (urgente primeiro)
    concursos.sort((a,b) => PRIO_ORDEM.indexOf(a.prioridade) - PRIO_ORDEM.indexOf(b.prioridade));

    let fichasHtml = '';
    if(!concursos.length) {
      fichasHtml = '<div style="padding:40px;text-align:center;color:var(--cinza)">Nenhum concurso monitorado ainda. Va em \"Gerenciar concursos\" para adicionar.</div>';
    }
    for(const c of concursos) {
      const novas = c.noticias.filter(n=>n.eh_novo).length;
      const aberta = novas > 0 ? 'aberta' : '';
      const cor = PRIO_COR[c.prioridade]||PRIO_COR.importante;
      const headClass = c.prioridade || 'importante';
      let nots = '';
      for(const n of c.noticias) {
        const cat = (n.categoria||'').replace('elim_ativas','gabarito').replace('taf_fases','fase').replace('recurso_anulacao','recurso').replace('radar_volume','inscricao');
        const tagCss = TAG_CSS[cat] || 'ntag';
        const tagLabel = cat.charAt(0).toUpperCase()+cat.slice(1);
        const gerado = n.selecionado_marketing ? true : false;
        nots += '<div class="linha"><span class="ntag '+tagCss+'">'+esc(tagLabel)+'</span><div class="ntexto">' +
          '<div class="nt">'+esc(n.titulo)+'</div><div class="nd">'+esc(n.descricao||'').substring(0,200)+'</div>' +
          '<div class="nacts">' +
          (gerado ? '<button class="mini feito" disabled>&#10003; No pipeline</button>' :
            '<button class="mini gerar" onclick="gerarConteudo('+n.id+',this)">&#9998; Gerar conteudo</button>') +
          (n.link ? '<a class="mini" href="'+esc(n.link)+'" target="_blank" rel="noopener">Ver fonte</a>' : '') +
          '<button class="mini" onclick="excluirItem('+n.id+',this)">Excluir</button>' +
          '</div></div></div>';
      }
      if(!c.noticias.length) nots = '<div style="padding:16px 0;color:var(--cinza);font-size:13px">Nenhuma noticia encaixada ainda neste concurso.</div>';
      fichasHtml += '<div class="ficha '+aberta+'" data-id="'+c.id+'">' +
        '<div class="ficha-head '+headClass+'" onclick="toggleFicha(this)">' +
        '<span class="chev">&#9656;</span>' +
        '<div class="ficha-tit"><h3 class="serif">'+esc(c.nome)+'</h3>' +
        '<div class="meta">Banca '+esc(c.banca||'-')+' &middot; '+esc(c.vagas||'-')+' vagas &middot; '+c.noticias.length+' noticia(s)</div></div>' +
        (novas > 0 ? '<span class="novelty"><span class="dot"></span> '+novas+' novas</span>' : '') +
        '<button class="stage-dot" title="'+PRIO_LABEL[c.prioridade]+'" style="background:'+cor+'" onclick="event.stopPropagation();ciclarPrio('+c.id+',this)"></button>' +
        '</div><div class="ficha-body">'+nots+'</div></div>';
    }

    let temasHtml = '';
    for(const t of temas) {
      const kind = t.tipo_transversal || 'jurisprudencia';
      const kindLabel = kind==='jurisprudencia'?'Jurisprudencia':kind==='concorrencia'?'Concorrencia':'Tendencia';
      temasHtml += '<div class="sitem"><div class="sk '+kind+'">'+kindLabel+'</div>' +
        '<div class="si-title">'+esc(t.titulo)+'</div>' +
        (t.link ? '<a class="mini" href="'+esc(t.link)+'" target="_blank" style="font-size:10px">Ver fonte</a> ' : '') +
        '<button class="mini gerar" onclick="gerarConteudo('+t.id+',this)" style="font-size:10px">&#9998; Gerar</button></div>';
    }
    if(!temasHtml) temasHtml = '<div style="color:var(--cinza);font-size:12px;padding:14px 0">Nenhum tema transversal recente.</div>';

    cont.innerHTML = '<div class="tela active">' +
      '<div class="legenda"><span><span class="sq" style="background:var(--urgente)"></span>Urgente</span>' +
      '<span><span class="sq" style="background:var(--importante)"></span>Importante</span>' +
      '<span><span class="sq" style="background:var(--naourgente)"></span>Nao urgente</span>' +
      '<span style="margin-left:auto;font-style:italic;font-size:10px">Clique na bolinha para alternar</span></div>' +
      '<div class="split"><div class="split-main">'+fichasHtml+'</div>' +
      '<aside class="side"><h4 class="serif">Temas do momento</h4>' +
      '<div class="side-sub">Jurisprudencia, concorrencia e tendencias (transversais).</div>' +
      temasHtml+'</aside></div></div>';
  }

  // ===== TELA 2: CAIXA DE ENTRADA =====
  async function renderTelaInbox() {
    const cont = document.getElementById('telas-container');
    cont.innerHTML = '<div class="tela active" style="text-align:center;padding:60px;color:var(--cinza)">Carregando...</div>';
    const data = await GET('/api/inbox');
    const encaixes = data.encaixes || [];
    const novos = data.novos || [];

    let encHtml = '';
    for(const e of encaixes) {
      encHtml += '<div class="triagem-card" data-id="'+e.id+'">' +
        '<div class="tc-main"><div class="tc-tit">'+esc(e.titulo)+'</div>' +
        '<div class="tc-sug">Parece ser de: <b>'+esc(e.sugestao_concurso)+'</b></div></div>' +
        '<div class="tc-acts">' +
        '<button class="btn-conf" onclick="confirmarEnc('+e.id+')">Confirmar</button>' +
        '<button class="btn-outro" onclick="abrirOutro('+e.id+')">Outro concurso</button>' +
        '<button class="btn-rej" onclick="excluirEnc('+e.id+')">Excluir</button>' +
        '</div></div>';
    }
    if(!encHtml) encHtml = '<div style="color:var(--cinza);font-size:13px">Nenhum encaixe pendente.</div>';

    let novHtml = '';
    for(const n of novos) {
      novHtml += '<div class="triagem-card sug-monit" data-id="sug-'+n.id+'">' +
        '<div class="tc-main"><div class="tc-tit">'+esc(n.nome_exibicao)+'</div>' +
        '<div class="tc-sug">Apareceu <b>'+n.vezes+'x</b> &middot; '+esc(n.ultima_evidencia||'')+'</div></div>' +
        '<div class="tc-acts"><button class="btn-conf" onclick="monitorarSug('+n.id+')">Monitorar</button>' +
        '<button class="btn-rej" onclick="ignorarSug('+n.id+')">Ignorar</button></div></div>';
    }
    if(!novHtml) novHtml = '<div style="color:var(--cinza);font-size:13px">Nenhuma sugestao de concurso novo.</div>';

    // Atualiza badge de contagem na nav
    const badge = document.querySelector('.mainnav .nav-badge');
    if(badge) badge.textContent = (encaixes.length + novos.length);

    cont.innerHTML = '<div class="tela active">' +
      '<div class="inbox-grupo"><h3 class="serif">Encaixes a confirmar <span class="nav-badge">'+encaixes.length+'</span></h3>' +
      '<div class="gsub">Noticias que parecem ser de concursos monitorados. Confirme para entrarem na ficha.</div>'+encHtml+'</div>' +
      '<div class="inbox-grupo"><h3 class="serif">Concursos novos sugeridos <span class="nav-badge cinza">'+novos.length+'</span></h3>' +
      '<div class="gsub">Concursos que apareceram varias vezes. Vale acompanhar?</div>'+novHtml+'</div>' +
      '<div style="margin-top:20px"><button class="add-btn" onclick="rodarTriagemRetroativa()">Rodar triagem retroativa (acervo antigo)</button></div>' +
      '</div>';
  }

  // ===== TELA 3: GERENCIAR CONCURSOS =====
  async function renderTelaGerenciar() {
    const cont = document.getElementById('telas-container');
    cont.innerHTML = '<div class="tela active" style="text-align:center;padding:60px;color:var(--cinza)">Carregando...</div>';
    const data = await GET('/api/concursos');
    const concursos = data.concursos || [];

    let cards = '';
    for(const c of concursos) {
      const cor = PRIO_COR[c.prioridade]||PRIO_COR.importante;
      const kws = (c.palavras_chave||'').split(',').filter(k=>k.trim()).map(k=>'<span class="kw">'+esc(k.trim())+'</span>').join('');
      cards += '<div class="monit-card"><h4 class="serif">'+esc(c.nome)+'</h4>' +
        '<div class="mc-meta">Banca '+esc(c.banca||'-')+' &middot; '+esc(c.vagas||'-')+' vagas</div>' +
        '<div>'+kws+'</div>' +
        '<div class="mc-foot"><span class="mc-count">'+c.noticias_count+' noticia(s)</span>' +
        '<button class="mini" style="color:#b23b32;border-color:#b23b32" onclick="excluirConcurso('+c.id+',\''+esc(c.nome)+'\')">Excluir</button>' +
        '<button class="stage-dot" title="'+PRIO_LABEL[c.prioridade]+'" style="background:'+cor+'" onclick="ciclarPrio('+c.id+',this)"></button>' +
        '</div></div>';
    }
    if(!cards) cards = '<div style="color:var(--cinza);padding:30px;text-align:center">Nenhum concurso monitorado. Adicione o primeiro!</div>';

    cont.innerHTML = '<div class="tela active">' +
      '<div class="add-bar"><button class="add-btn" onclick="abrirModal()">+ Monitorar novo concurso</button></div>' +
      '<div class="monit-grid">'+cards+'</div></div>';
  }

  // ===== ACOES =====
  function toggleFicha(head) { head.closest('.ficha').classList.toggle('aberta'); }

  async function ciclarPrio(cid, btn) {
    const cur = PRIO_ORDEM.find(p=>PRIO_COR[p]===btn.style.background.includes(PRIO_COR[p])) || 'importante';
    // Detecta prioridade atual pelo titulo
    const curTitle = (btn.title||'').toLowerCase();
    let idx = PRIO_ORDEM.findIndex(p=>curTitle.includes(p));
    if(idx<0) idx = 1;
    const nova = PRIO_ORDEM[(idx+1)%PRIO_ORDEM.length];
    const r = await POST('/api/concursos/'+cid+'/prioridade', {prioridade:nova});
    if(r.ok) {
      btn.style.background = PRIO_COR[nova];
      btn.title = PRIO_LABEL[nova];
      const head = btn.closest('.ficha-head');
      if(head) { PRIO_ORDEM.forEach(p=>head.classList.remove(p)); head.classList.add(nova); }
      toast('Prioridade: '+PRIO_LABEL[nova]);
      // TODO: quando CONCURSOS_MKT_ENDPOINT estiver definido, atualizar etapa no marketing
    }
  }

  async function gerarConteudo(itemId, btn) {
    btn.disabled=true; btn.textContent='Enviando...';
    try {
      // Busca dados do item
      const d = await GET('/api/oportunidades?incluir_lidos=1&dias=365&limite=1&concurso_id='+itemId);
      let item = (d.itens||[])[0];
      if(!item) { const d2 = await GET('/api/oportunidades?incluir_lidos=1&dias=365&limite=500'); item = (d2.itens||[]).find(i=>i.id===itemId); }
      const payload = {
        nome: String((item&&item.concurso)||'').trim() || String((item&&item.titulo)||'').trim(),
        banca: String((item&&item.banca)||'').trim(),
        orgao: String((item&&item.orgao)||'').trim(),
        zona: 'yellow', vagas: String((item&&item.vagas)||'').trim(),
        destino: 'pipeline',
      };
      const r = await fetch(SELECIONAR_ENDPOINT, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      if(r.ok) {
        await POST('/api/oportunidades/'+itemId+'/marcar_selecionado');
        btn.className='mini feito'; btn.innerHTML='&#10003; No pipeline'; toast('Enviado ao pipeline');
      } else { btn.textContent='Erro'; setTimeout(()=>{btn.className='mini gerar';btn.innerHTML='&#9998; Gerar conteudo';btn.disabled=false},3000); }
    } catch(e) { btn.textContent='Erro'; toast('Erro: '+e.message); setTimeout(()=>{btn.className='mini gerar';btn.innerHTML='&#9998; Gerar conteudo';btn.disabled=false},3000); }
  }

  async function excluirItem(itemId, btn) {
    if(!confirm('Excluir esta noticia permanentemente?')) return;
    const r = await DEL('/api/oportunidades/'+itemId);
    if(r.ok) { const card = btn.closest('.linha'); if(card) card.remove(); toast('Excluido'); }
  }

  async function confirmarEnc(itemId) {
    const r = await POST('/api/inbox/'+itemId+'/confirmar');
    if(r.ok||!r.erro) { const c=document.querySelector('.triagem-card[data-id="'+itemId+'"]'); if(c){c.style.opacity='0.4';c.querySelector('.tc-acts').innerHTML='<span style="color:var(--gold-dark);font-weight:700;font-size:11px">&#10003; Confirmado</span>';} toast('Encaixado'); }
    else toast('Erro: '+(r.erro||''));
  }

  async function excluirEnc(itemId) {
    const r = await DEL('/api/oportunidades/'+itemId);
    if(r.ok) { const c=document.querySelector('.triagem-card[data-id="'+itemId+'"]'); if(c) c.remove(); toast('Excluido'); }
  }

  let _outroAlvo = null;
  async function abrirOutro(itemId) {
    _outroAlvo = itemId;
    const data = await GET('/api/concursos');
    const lista = (data.concursos||[]).map(c=>'<div class="outro-item" onclick="encaixarEm('+c.id+')">'+esc(c.nome)+'</div>').join('');
    document.getElementById('outro-list').innerHTML = lista || '<div style="color:var(--cinza);font-size:12px">Nenhum concurso monitorado.</div>';
    document.getElementById('outro-novo-nome').value='';
    document.getElementById('outro-menu').classList.add('show');
  }
  function fecharOutro() { document.getElementById('outro-menu').classList.remove('show'); _outroAlvo=null; }
  async function encaixarEm(concursoId) {
    fecharOutro();
    const r = await POST('/api/inbox/'+_outroAlvo+'/confirmar', {concurso_id:concursoId});
    if(r.ok||!r.erro) { const c=document.querySelector('.triagem-card[data-id="'+_outroAlvo+'"]'); if(c){c.style.opacity='0.4';c.querySelector('.tc-acts').innerHTML='<span style="color:var(--gold-dark);font-weight:700;font-size:11px">&#10003; Encaixado</span>';} toast('Encaixado'); }
  }
  async function criarEEncaixar() {
    const nome = document.getElementById('outro-novo-nome').value.trim();
    if(!nome){toast('Digite o nome');return;}
    fecharOutro();
    const r = await POST('/api/inbox/'+_outroAlvo+'/criar-e-encaixar', {nome:nome});
    if(r.ok||!r.erro) { const c=document.querySelector('.triagem-card[data-id="'+_outroAlvo+'"]'); if(c){c.style.opacity='0.4';c.querySelector('.tc-acts').innerHTML='<span style="color:var(--gold-dark);font-weight:700;font-size:11px">&#10003; Novo criado</span>';} toast('Concurso criado e encaixado'); }
  }

  async function monitorarSug(sugId) {
    const r = await POST('/api/sugestoes/'+sugId+'/monitorar');
    if(r.ok||!r.erro) { const c=document.querySelector('[data-id="sug-'+sugId+'"]'); if(c){c.style.opacity='0.4';c.querySelector('.tc-acts').innerHTML='<span style="color:var(--gold-dark);font-weight:700;font-size:11px">&#10003; Monitorando</span>';} toast('Concurso monitorado'); }
  }
  async function ignorarSug(sugId) {
    const r = await POST('/api/sugestoes/'+sugId+'/ignorar');
    if(r.ok||!r.erro) { const c=document.querySelector('[data-id="sug-'+sugId+'"]'); if(c) c.remove(); toast('Ignorado'); }
  }

  async function rodarTriagemRetroativa() {
    if(!confirm('Rodar triagem sobre todo o acervo antigo? Pode demorar alguns segundos.')) return;
    toast('Triando acervo...',10000);
    const r = await POST('/api/triagem/retroativa');
    if(r.stats) toast('Triagem: '+r.stats.auto_confirmado+' encaixados, '+r.stats.transversal+' transversais, '+r.stats.sugerido+' sugeridos',8000);
    else toast('Concluido');
    renderTelaInbox();
  }

  async function excluirConcurso(cid, nome) {
    if(!confirm('Parar de monitorar "'+nome+'"?\n\nAs noticias encaixadas nao serao apagadas, apenas desvinculadas.')) return;
    const r = await DEL('/api/concursos/'+cid);
    if(r.ok) { toast('Concurso removido'); renderTelaGerenciar(); }
  }

  // ===== MODAL CRIAR CONCURSO =====
  function abrirModal() { document.getElementById('modal-add').classList.add('show'); }
  function fecharModal() { document.getElementById('modal-add').classList.remove('show'); }
  async function salvarConcurso() {
    const nome = document.getElementById('add-nome').value.trim();
    if(!nome){toast('Informe o nome');return;}
    const banca = document.getElementById('add-banca').value.trim();
    const kw = document.getElementById('add-kw').value.trim();
    const r = await POST('/api/concursos', {nome:nome, banca:banca, palavras_chave:kw, prioridade:'importante'});
    if(r.ok||r.concurso) {
      fecharModal(); toast('Monitorando: '+nome);
      document.getElementById('add-nome').value='';
      document.getElementById('add-banca').value='';
      document.getElementById('add-kw').value='';
      if(telAtual==='gerenciar') renderTelaGerenciar();
    } else toast('Erro: '+(r.erro||''));
  }

  // ===== BUSCA AO VIVO =====
  async function buscaAoVivo(termo) {
    if(!termo.trim()) return;
    toast('Buscando: "'+termo+'"... (busca sob demanda sera implementada na proxima versao)',5000);
  }

  // ===== COLETA MANUAL =====
  async function coletarTudo() {
    if(!confirm('Disparar coleta completa? Demora 3-6 minutos.')) return;
    toast('Coleta disparada em background...',6000);
    await POST('/cron/manual?tipo=completo');
  }

  // ===== INIT =====
  async function init() {
    // Carrega contagem da inbox pra badge
    const inbox = await GET('/api/inbox');
    const badge = document.querySelector('.mainnav .nav-badge');
    if(badge && inbox) badge.textContent = (inbox.total||0);
    // Renderiza tela inicial
    renderTelaConcursos();
  }
  init();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    app.run(host="0.0.0.0", port=port, debug=False)
