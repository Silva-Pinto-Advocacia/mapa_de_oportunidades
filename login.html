"""
Silva Pinto Advocacia — Painel de Oportunidades
================================================
MVP — coleta diária (2x/dia) de:
  1. Concursos abertos para inscrição
  2. Concursos em fase de recursos/anulação
  3. Decisões judiciais sobre concursos (jurisprudência)

Fluxo:
  - Endpoint /cron/diaria é chamado por cron-job.org externamente
  - Internamente faz N buscas via web_search (anthropic.tools.web_search) e Haiku
  - Filtra/sintetiza com Claude Haiku
  - Salva em SQLite (oportunidades.db)
  - Interface lê do SQLite e mostra o briefing

Acesso: protegido por senha simples (ACCESS_TOKEN env var)
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
from typing import Optional

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import anthropic

# ─────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────
APP_VERSION = "v1-2026-05-04-mvp"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

log.info("=" * 70)
log.info("🚀 SilvaPinto Oportunidades %s INICIANDO", APP_VERSION)
log.info("=" * 70)

# Database location — Render persistent disk if available, else /tmp
DB_PATH = Path(os.environ.get("DB_PATH", "/tmp/oportunidades.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Configuration via env vars
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "silvapinto2026")  # senha simples
CRON_SECRET = os.environ.get("CRON_SECRET", "altereestasenha123")  # cron auth
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL_NAME = os.environ.get("MODEL_NAME", "claude-haiku-4-5-20251001")

# Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "altereestasenha-flask-key-456")


# ─────────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────────
def init_db():
    """Cria as tabelas se não existirem."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS oportunidades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            categoria TEXT NOT NULL,
            titulo TEXT NOT NULL,
            descricao TEXT,
            orgao TEXT,
            estado TEXT,
            prazo TEXT,
            link TEXT,
            relevancia INTEGER DEFAULT 5,
            lido INTEGER DEFAULT 0,
            arquivado INTEGER DEFAULT 0,
            data_coleta TEXT NOT NULL,
            hash_unico TEXT UNIQUE
        );
        CREATE INDEX IF NOT EXISTS idx_categoria ON oportunidades(categoria);
        CREATE INDEX IF NOT EXISTS idx_data ON oportunidades(data_coleta);
        CREATE INDEX IF NOT EXISTS idx_lido ON oportunidades(lido);

        CREATE TABLE IF NOT EXISTS execucoes_cron (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_execucao TEXT NOT NULL,
            categorias_processadas TEXT,
            itens_novos INTEGER DEFAULT 0,
            duracao_segundos REAL,
            sucesso INTEGER DEFAULT 1,
            erro TEXT
        );
        """)
    log.info("DB inicializado em %s", DB_PATH)


@contextmanager
def db_conn():
    """Context manager para conexão SQLite."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def hash_for_dedup(titulo: str, orgao: str = "") -> str:
    """Hash para evitar duplicatas — mesmo título + órgão = mesmo item."""
    import hashlib
    base = f"{titulo.strip().lower()[:100]}|{orgao.strip().lower()[:50]}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


# ─────────────────────────────────────────────────────────────────
# Categorias e queries de busca
# ─────────────────────────────────────────────────────────────────
def buscas_para_categoria(categoria: str, hoje: datetime) -> list[str]:
    """Retorna queries específicas por categoria."""
    mes_ano = hoje.strftime("%B %Y").lower()
    mes_pt = {
        "january": "janeiro", "february": "fevereiro", "march": "março",
        "april": "abril", "may": "maio", "june": "junho",
        "july": "julho", "august": "agosto", "september": "setembro",
        "october": "outubro", "november": "novembro", "december": "dezembro",
    }
    for en, pt in mes_pt.items():
        mes_ano = mes_ano.replace(en, pt)

    if categoria == "concursos_abertos":
        return [
            f"concursos públicos abertos inscrição {mes_ano}",
            "novos editais concurso público Brasil esta semana",
            "concursos públicos com inscrições abertas hoje",
            "edital concurso público publicado Brasil",
        ]
    if categoria == "recursos_anulacao":
        return [
            f"questões anuladas concurso público {mes_ano}",
            "gabarito definitivo concurso público recursos",
            "anulação questão concurso recente Brasil",
            "concurso público polêmica questão prova",
        ]
    if categoria == "jurisprudencia":
        return [
            "STJ decisão concurso público recente",
            "TJ anulação questão concurso 2026",
            "jurisprudência concurso público nota corte",
            "decisão judicial concurso público candidato eliminado",
        ]
    return []


# ─────────────────────────────────────────────────────────────────
# Cliente Anthropic + busca + síntese
# ─────────────────────────────────────────────────────────────────
def coletar_categoria(api_key: str, categoria: str, hoje: datetime) -> tuple[list[dict], Optional[str]]:
    """
    Para cada query da categoria, pede pra Claude buscar na web e extrair
    itens estruturados. Retorna (lista de dicts, erro ou None).
    """
    queries = buscas_para_categoria(categoria, hoje)
    todos_itens = []
    erro_msg: Optional[str] = None

    descricoes_categoria = {
        "concursos_abertos": (
            "concursos públicos com INSCRIÇÕES ABERTAS no momento — "
            "ou seja, com prazo de inscrição vigente. Liste apenas concursos "
            "que ainda permitem inscrição, não os que já encerraram."
        ),
        "recursos_anulacao": (
            "concursos públicos em FASE DE RECURSOS ou COM QUESTÕES POLÊMICAS, "
            "onde candidatos estão contestando questões, gabaritos foram "
            "alterados, ou há discussão sobre anulação de questões. "
            "Esta é uma oportunidade jurídica para impugnação administrativa "
            "ou judicial."
        ),
        "jurisprudencia": (
            "DECISÕES JUDICIAIS RECENTES de tribunais (STJ, STF, TJs) sobre "
            "concursos públicos — anulação de questões, reclassificação de "
            "candidatos, contagem de pontos, nota de corte, posse, "
            "convocação. Foco em precedentes úteis."
        ),
    }

    descricao = descricoes_categoria.get(categoria, "")
    client = anthropic.Anthropic(api_key=api_key, timeout=180.0, max_retries=2)

    prompt_extracao = f"""Você é um pesquisador jurídico do escritório Silva Pinto Advocacia, especializado em concursos públicos.

TAREFA: Pesquise na web sobre {descricao}

Realize as seguintes buscas, uma por vez, usando a ferramenta de busca:
{chr(10).join(f"  {i+1}. {q}" for i, q in enumerate(queries))}

Após as buscas, retorne um JSON com lista de itens encontrados. Cada item deve ser uma oportunidade ou notícia ÚNICA, RECENTE (últimos 30 dias) e RELEVANTE para um escritório que atua judicialmente em concursos públicos.

FORMATO DE RETORNO (JSON puro, sem markdown):
{{
  "itens": [
    {{
      "titulo": "Título objetivo e descritivo",
      "descricao": "Resumo em 2-3 frases do que é a oportunidade/notícia",
      "orgao": "Órgão/instituição (ex: PCMG, TJSP, STJ, TRF1) ou vazio",
      "estado": "UF (ex: MG, SP) ou 'Brasil' se nacional",
      "prazo": "Data ou prazo relevante (ex: '15/06/2026' ou 'até 30 dias') ou vazio",
      "link": "URL da fonte mais confiável (oficial > grande mídia > especializada)",
      "relevancia": 1-10 (quão útil para um escritório de concursos)
    }}
  ]
}}

REGRAS:
- Máximo 8 itens por categoria — escolha os melhores.
- NÃO inclua itens duplicados ou muito similares.
- NÃO inclua itens antigos (> 30 dias).
- Se não encontrar nada relevante, retorne {{"itens": []}}.
- Retorne SOMENTE o JSON, sem texto antes/depois, sem markdown."""

    try:
        log.info("[%s] iniciando %d buscas via web_search", categoria, len(queries))
        msg = client.messages.create(
            model=MODEL_NAME,
            max_tokens=8000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt_extracao}],
        )

        # Extract text from response (skipping tool_use blocks)
        text_parts = []
        for block in msg.content:
            if hasattr(block, "text") and block.text:
                text_parts.append(block.text)
        raw = "".join(text_parts).strip()

        log.info("[%s] resposta da IA: %d chars", categoria, len(raw))

        # Strip markdown fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)

        # Find JSON object
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not json_match:
            log.warning("[%s] nenhum JSON encontrado na resposta", categoria)
            return []

        try:
            data = json.loads(json_match.group(0))
        except json.JSONDecodeError as e:
            log.warning("[%s] JSON malformado: %s", categoria, e)
            return []

        itens = data.get("itens", [])
        log.info("[%s] %d itens extraídos", categoria, len(itens))

        # Validate and clean
        for item in itens:
            if not isinstance(item, dict):
                continue
            if not item.get("titulo"):
                continue
            todos_itens.append({
                "categoria": categoria,
                "titulo": str(item.get("titulo", "")).strip()[:300],
                "descricao": str(item.get("descricao", "")).strip()[:1000],
                "orgao": str(item.get("orgao", "")).strip()[:100],
                "estado": str(item.get("estado", "")).strip()[:30],
                "prazo": str(item.get("prazo", "")).strip()[:100],
                "link": str(item.get("link", "")).strip()[:500],
                "relevancia": int(item.get("relevancia", 5)) if str(item.get("relevancia", 5)).isdigit() else 5,
            })

    except Exception as e:
        log.error("[%s] erro: %s\n%s", categoria, e, traceback.format_exc())
        erro_msg = f"{type(e).__name__}: {str(e)[:200]}"

    return todos_itens, erro_msg


def salvar_itens(itens: list[dict]) -> int:
    """Salva itens novos no banco. Retorna quantos foram realmente inseridos."""
    if not itens:
        return 0
    novos = 0
    agora = datetime.now(timezone.utc).isoformat()
    with db_conn() as conn:
        for item in itens:
            h = hash_for_dedup(item["titulo"], item.get("orgao", ""))
            try:
                conn.execute(
                    """INSERT INTO oportunidades
                    (categoria, titulo, descricao, orgao, estado, prazo, link,
                     relevancia, data_coleta, hash_unico)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item["categoria"], item["titulo"], item["descricao"],
                        item["orgao"], item["estado"], item["prazo"], item["link"],
                        item["relevancia"], agora, h
                    )
                )
                novos += 1
            except sqlite3.IntegrityError:
                # Duplicate hash — already exists
                pass
    return novos


def executar_coleta_completa(api_key: str) -> dict:
    """Executa coleta para todas as categorias."""
    inicio = datetime.now(timezone.utc)
    categorias = ["concursos_abertos", "recursos_anulacao", "jurisprudencia"]
    total_novos = 0
    erros = []

    for cat in categorias:
        try:
            itens, erro_cat = coletar_categoria(api_key, cat, inicio)
            novos = salvar_itens(itens)
            total_novos += novos
            log.info("[%s] %d novos salvos (de %d encontrados)", cat, novos, len(itens))
            if erro_cat:
                erros.append(f"{cat}: {erro_cat}")
        except Exception as e:
            log.error("[%s] falhou: %s", cat, e)
            erros.append(f"{cat}: {e}")

    duracao = (datetime.now(timezone.utc) - inicio).total_seconds()
    sucesso = len(erros) == 0

    with db_conn() as conn:
        conn.execute(
            """INSERT INTO execucoes_cron
            (data_execucao, categorias_processadas, itens_novos, duracao_segundos, sucesso, erro)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (inicio.isoformat(), ",".join(categorias), total_novos, duracao,
             1 if sucesso else 0, "; ".join(erros) if erros else None)
        )

    return {
        "sucesso": sucesso,
        "itens_novos": total_novos,
        "duracao_segundos": round(duracao, 1),
        "erros": erros,
    }


# ─────────────────────────────────────────────────────────────────
# Auth helpers
# ─────────────────────────────────────────────────────────────────
def is_logged_in():
    return session.get("logged_in") is True


def require_auth():
    if not is_logged_in():
        return redirect(url_for("login"))
    return None


# ─────────────────────────────────────────────────────────────────
# Routes — UI
# ─────────────────────────────────────────────────────────────────
@app.route("/")
def home():
    if not is_logged_in():
        return redirect(url_for("login"))
    return render_template("index.html", version=APP_VERSION)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        token = request.form.get("token", "").strip()
        if token == ACCESS_TOKEN:
            session["logged_in"] = True
            session.permanent = True
            return redirect(url_for("home"))
        error = "Senha incorreta"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ─────────────────────────────────────────────────────────────────
# Routes — API
# ─────────────────────────────────────────────────────────────────
@app.route("/api/oportunidades")
def api_listar():
    if not is_logged_in():
        return jsonify({"erro": "não autorizado"}), 401

    categoria = request.args.get("categoria", "")
    estado = request.args.get("estado", "")
    incluir_lidos = request.args.get("incluir_lidos", "0") == "1"
    limite = int(request.args.get("limite", 200))
    dias = int(request.args.get("dias", 30))

    where_parts = ["arquivado = 0"]
    params = []
    if not incluir_lidos:
        where_parts.append("lido = 0")
    if categoria:
        where_parts.append("categoria = ?")
        params.append(categoria)
    if estado:
        where_parts.append("estado = ?")
        params.append(estado)
    if dias > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
        where_parts.append("data_coleta >= ?")
        params.append(cutoff)

    where_sql = " AND ".join(where_parts)
    sql = f"""SELECT * FROM oportunidades
              WHERE {where_sql}
              ORDER BY relevancia DESC, data_coleta DESC
              LIMIT ?"""
    params.append(limite)

    with db_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    return jsonify({
        "total": len(rows),
        "itens": [dict(r) for r in rows]
    })


@app.route("/api/oportunidades/<int:item_id>/marcar_lido", methods=["POST"])
def api_marcar_lido(item_id):
    if not is_logged_in():
        return jsonify({"erro": "não autorizado"}), 401
    with db_conn() as conn:
        conn.execute("UPDATE oportunidades SET lido = 1 WHERE id = ?", (item_id,))
    return jsonify({"ok": True})


@app.route("/api/oportunidades/<int:item_id>/arquivar", methods=["POST"])
def api_arquivar(item_id):
    if not is_logged_in():
        return jsonify({"erro": "não autorizado"}), 401
    with db_conn() as conn:
        conn.execute("UPDATE oportunidades SET arquivado = 1 WHERE id = ?", (item_id,))
    return jsonify({"ok": True})


@app.route("/api/status")
def api_status():
    if not is_logged_in():
        return jsonify({"erro": "não autorizado"}), 401
    with db_conn() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM oportunidades").fetchone()["c"]
        nao_lidos = conn.execute(
            "SELECT COUNT(*) AS c FROM oportunidades WHERE lido = 0 AND arquivado = 0"
        ).fetchone()["c"]
        ultima = conn.execute(
            "SELECT * FROM execucoes_cron ORDER BY id DESC LIMIT 1"
        ).fetchone()

    return jsonify({
        "total": total,
        "nao_lidos": nao_lidos,
        "ultima_execucao": dict(ultima) if ultima else None,
        "model": MODEL_NAME,
        "version": APP_VERSION,
    })


# ─────────────────────────────────────────────────────────────────
# Route — Cron (chamado externamente)
# ─────────────────────────────────────────────────────────────────
@app.route("/cron/diaria", methods=["GET", "POST"])
def cron_diaria():
    """Endpoint chamado por cron-job.org duas vezes ao dia."""
    secret = request.args.get("secret") or request.headers.get("X-Cron-Secret")
    if secret != CRON_SECRET:
        log.warning("Cron tentativa de acesso sem secret válido")
        return jsonify({"erro": "secret inválido"}), 403

    if not ANTHROPIC_API_KEY:
        log.error("Cron disparou mas ANTHROPIC_API_KEY não está definida")
        return jsonify({"erro": "API key não configurada"}), 500

    log.info("=" * 60)
    log.info("CRON DIÁRIA disparado em %s", datetime.now(timezone.utc).isoformat())
    log.info("=" * 60)

    try:
        result = executar_coleta_completa(ANTHROPIC_API_KEY)
        log.info("Cron concluído: %s", result)
        return jsonify(result)
    except Exception as e:
        log.error("Cron falhou: %s\n%s", e, traceback.format_exc())
        return jsonify({"erro": str(e)}), 500


@app.route("/cron/manual", methods=["POST"])
def cron_manual():
    """Disparo manual via UI — para teste sem esperar agendamento."""
    if not is_logged_in():
        return jsonify({"erro": "não autorizado"}), 401

    if not ANTHROPIC_API_KEY:
        return jsonify({
            "erro": "ANTHROPIC_API_KEY não está configurada nas variáveis de ambiente do Render"
        }), 500

    log.info("Disparo MANUAL pela UI")

    # Run in background thread so HTTP doesn't timeout
    def _run():
        try:
            executar_coleta_completa(ANTHROPIC_API_KEY)
        except Exception as e:
            log.error("Manual run falhou: %s", e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return jsonify({"ok": True, "mensagem": "Coleta iniciada em background. Aguarde ~1-2 minutos e recarregue a página."})


# ─────────────────────────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"ok": True, "version": APP_VERSION})


# ─────────────────────────────────────────────────────────────────
# Init
# ─────────────────────────────────────────────────────────────────
init_db()


def seed_demo_se_vazio():
    """Insere alguns itens de exemplo se o banco estiver vazio,
    apenas para você visualizar a interface antes da primeira coleta real."""
    with db_conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM oportunidades").fetchone()["c"]
        if count > 0:
            return
        log.info("Banco vazio — inserindo dados de exemplo (serão substituídos pela primeira coleta real)")
        agora = datetime.now(timezone.utc).isoformat()
        demos = [
            ("concursos_abertos", "[EXEMPLO] PCDF abre concurso para Agente de Polícia",
             "Concurso da Polícia Civil do Distrito Federal com 1.800 vagas. Inscrições abertas até final do mês.",
             "PCDF", "DF", "30/06/2026", "https://example.com", 9),
            ("recursos_anulacao", "[EXEMPLO] Banca anula 3 questões do concurso da PCMG após recursos",
             "Após análise dos recursos administrativos, a banca examinadora anulou 3 questões da prova objetiva. Candidatos podem questionar judicialmente outras questões com vícios.",
             "PCMG", "MG", "Recursos até 15/06", "https://example.com", 8),
            ("jurisprudencia", "[EXEMPLO] STJ decide que candidato pode contestar questão fora do edital",
             "STJ firmou entendimento de que questões cobradas fora do conteúdo programático do edital devem ser anuladas, mesmo após o gabarito definitivo.",
             "STJ", "Brasil", "", "https://example.com", 10),
        ]
        for cat, tit, desc, org, est, prazo, link, rel in demos:
            try:
                conn.execute(
                    """INSERT INTO oportunidades
                    (categoria, titulo, descricao, orgao, estado, prazo, link,
                     relevancia, data_coleta, hash_unico)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (cat, tit, desc, org, est, prazo, link, rel, agora,
                     hash_for_dedup(tit, org))
                )
            except sqlite3.IntegrityError:
                pass


seed_demo_se_vazio()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    app.run(host="0.0.0.0", port=port, debug=False)
