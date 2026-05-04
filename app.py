"""
Silva Pinto Advocacia - Painel de Oportunidades
Single-file MVP: tudo num arquivo so para evitar problemas com pastas no GitHub.
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
APP_VERSION = "v3-2026-05-04-single-file"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

log.info("=" * 70)
log.info("Silva Pinto Oportunidades %s INICIANDO", APP_VERSION)
log.info("=" * 70)

DB_PATH = Path(os.environ.get("DB_PATH", "/tmp/oportunidades.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

CRON_SECRET = os.environ.get("CRON_SECRET", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL_NAME = os.environ.get("MODEL_NAME", "claude-haiku-4-5-20251001")

app = Flask(__name__)


def init_db():
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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def hash_for_dedup(titulo, orgao=""):
    import hashlib
    base = f"{titulo.strip().lower()[:100]}|{orgao.strip().lower()[:50]}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def buscas_para_categoria(categoria, hoje):
    mes_ano = hoje.strftime("%B %Y").lower()
    mes_pt = {
        "january": "janeiro", "february": "fevereiro", "march": "marco",
        "april": "abril", "may": "maio", "june": "junho",
        "july": "julho", "august": "agosto", "september": "setembro",
        "october": "outubro", "november": "novembro", "december": "dezembro",
    }
    for en, pt in mes_pt.items():
        mes_ano = mes_ano.replace(en, pt)

    if categoria == "concursos_abertos":
        return [
            f"concursos publicos abertos inscricao {mes_ano}",
            "novos editais concurso publico Brasil esta semana",
            "concursos publicos com inscricoes abertas hoje",
            "edital concurso publico publicado Brasil",
        ]
    if categoria == "recursos_anulacao":
        return [
            f"questoes anuladas concurso publico {mes_ano}",
            "gabarito definitivo concurso publico recursos",
            "anulacao questao concurso recente Brasil",
            "concurso publico polemica questao prova",
        ]
    if categoria == "jurisprudencia":
        return [
            "STJ decisao concurso publico recente",
            "TJ anulacao questao concurso 2026",
            "jurisprudencia concurso publico nota corte",
            "decisao judicial concurso publico candidato eliminado",
        ]
    return []


def coletar_categoria(api_key, categoria, hoje):
    queries = buscas_para_categoria(categoria, hoje)
    todos_itens = []
    erro_msg = None

    descricoes = {
        "concursos_abertos": (
            "concursos publicos com INSCRICOES ABERTAS no momento - "
            "ou seja, com prazo de inscricao vigente. Liste apenas concursos "
            "que ainda permitem inscricao."
        ),
        "recursos_anulacao": (
            "concursos publicos em FASE DE RECURSOS ou COM QUESTOES POLEMICAS, "
            "onde candidatos estao contestando questoes ou ha discussao sobre "
            "anulacao de questoes."
        ),
        "jurisprudencia": (
            "DECISOES JUDICIAIS RECENTES de tribunais (STJ, STF, TJs) sobre "
            "concursos publicos - anulacao de questoes, reclassificacao, "
            "nota de corte, posse, convocacao."
        ),
    }

    descricao = descricoes.get(categoria, "")
    client = anthropic.Anthropic(api_key=api_key, timeout=180.0, max_retries=2)

    queries_str = "\n".join(f"  {i+1}. {q}" for i, q in enumerate(queries))
    prompt = f"""Voce e um pesquisador juridico do escritorio Silva Pinto Advocacia.

TAREFA: Pesquise na web sobre {descricao}

Realize as buscas a seguir, uma por vez, usando a ferramenta de busca:
{queries_str}

Apos as buscas, retorne JSON com itens encontrados. Cada item deve ser UNICO, RECENTE (ultimos 30 dias) e RELEVANTE para um escritorio que atua em concursos publicos.

FORMATO (JSON puro, sem markdown):
{{
  "itens": [
    {{
      "titulo": "Titulo objetivo",
      "descricao": "Resumo em 2-3 frases",
      "orgao": "Orgao (ex: PCMG, STJ) ou vazio",
      "estado": "UF (ex: MG) ou Brasil se nacional",
      "prazo": "Data ou prazo ou vazio",
      "link": "URL da fonte",
      "relevancia": 1-10
    }}
  ]
}}

REGRAS:
- Maximo 8 itens por categoria.
- NAO duplicados.
- NAO antigos (> 30 dias).
- Se nada relevante, retorne {{"itens": []}}.
- Retorne SO o JSON."""

    try:
        log.info("[%s] iniciando %d buscas", categoria, len(queries))
        msg = client.messages.create(
            model=MODEL_NAME,
            max_tokens=8000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )

        text_parts = []
        for block in msg.content:
            if hasattr(block, "text") and block.text:
                text_parts.append(block.text)
        raw = "".join(text_parts).strip()

        log.info("[%s] resposta: %d chars", categoria, len(raw))

        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)

        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not json_match:
            return [], "JSON nao encontrado"

        try:
            data = json.loads(json_match.group(0))
        except json.JSONDecodeError as e:
            return [], f"JSON malformado: {e}"

        itens = data.get("itens", [])
        log.info("[%s] %d itens extraidos", categoria, len(itens))

        for item in itens:
            if not isinstance(item, dict) or not item.get("titulo"):
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
        log.error("[%s] erro: %s", categoria, e)
        erro_msg = f"{type(e).__name__}: {str(e)[:200]}"

    return todos_itens, erro_msg


def salvar_itens(itens):
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
                    (item["categoria"], item["titulo"], item["descricao"],
                     item["orgao"], item["estado"], item["prazo"], item["link"],
                     item["relevancia"], agora, h)
                )
                novos += 1
            except sqlite3.IntegrityError:
                pass
    return novos


def executar_coleta_completa(api_key):
    inicio = datetime.now(timezone.utc)
    categorias = ["concursos_abertos", "recursos_anulacao", "jurisprudencia"]
    total_novos = 0
    erros = []

    for cat in categorias:
        try:
            itens, erro_cat = coletar_categoria(api_key, cat, inicio)
            novos = salvar_itens(itens)
            total_novos += novos
            log.info("[%s] %d novos salvos (de %d)", cat, novos, len(itens))
            if erro_cat:
                erros.append(f"{cat}: {erro_cat}")
        except Exception as e:
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


# HTML embutido (sem precisar de pasta templates)
HTML_INDEX = r"""<!DOCTYPE html>
<html lang="pt-br">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Silva Pinto - Oportunidades</title>
  <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;500;600;700&family=Montserrat:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --gold: #BB904C;
      --gold-light: #D4AF7A;
      --gold-pale: #f5ecd9;
      --navy: #1a2842;
      --navy-light: #2d3e5e;
      --gray-bg: #f4f4f0;
      --gray-line: #d8d8d2;
      --text-primary: #1a2842;
      --text-secondary: #6b6e76;
      --green: #10b981;
      --red: #dc2626;
      --orange: #ea580c;
      --blue: #2563eb;
    }
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: 'Montserrat', sans-serif;
      background: var(--gray-bg);
      color: var(--text-primary);
      min-height: 100vh;
    }
    header {
      background: var(--navy);
      color: white;
      padding: 16px 24px;
      box-shadow: 0 2px 12px rgba(0,0,0,0.2);
      position: sticky;
      top: 0;
      z-index: 100;
    }
    .header-inner {
      max-width: 1280px;
      margin: 0 auto;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }
    .header-left {
      display: flex;
      align-items: center;
      gap: 14px;
    }
    .header-left img {
      height: 40px;
      width: auto;
      filter: brightness(0) invert(1);
    }
    .header-title {
      font-family: 'Cormorant Garamond', serif;
      font-size: 22px;
      font-weight: 600;
    }
    .header-subtitle {
      font-size: 11px;
      letter-spacing: 1.5px;
      color: var(--gold-light);
      text-transform: uppercase;
      margin-top: 2px;
    }
    .btn {
      background: var(--gold);
      color: white;
      border: none;
      padding: 10px 16px;
      border-radius: 5px;
      font-family: 'Montserrat', sans-serif;
      font-size: 12px;
      font-weight: 600;
      letter-spacing: 0.5px;
      text-transform: uppercase;
      cursor: pointer;
      transition: all 0.15s;
      text-decoration: none;
      display: inline-block;
    }
    .btn:hover { background: var(--gold-light); }
    .btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .btn-ghost {
      background: transparent;
      border: 1px solid rgba(255,255,255,0.3);
      color: white;
    }
    .btn-ghost:hover { background: rgba(255,255,255,0.1); }

    .status-bar {
      max-width: 1280px;
      margin: 24px auto 0;
      padding: 0 24px;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
    }
    .status-card {
      background: white;
      border-radius: 6px;
      padding: 16px 18px;
      border-left: 3px solid var(--gold);
    }
    .status-card .label {
      font-size: 11px;
      color: var(--text-secondary);
      letter-spacing: 1px;
      text-transform: uppercase;
      margin-bottom: 6px;
    }
    .status-card .value {
      font-family: 'Cormorant Garamond', serif;
      font-size: 28px;
      color: var(--navy);
      font-weight: 600;
    }
    .status-card .value.small {
      font-size: 14px;
      font-family: 'Montserrat', sans-serif;
    }

    .tabs {
      max-width: 1280px;
      margin: 28px auto 0;
      padding: 0 24px;
      display: flex;
      gap: 0;
      border-bottom: 1px solid var(--gray-line);
    }
    .tab {
      padding: 14px 22px;
      background: transparent;
      border: none;
      cursor: pointer;
      font-family: 'Montserrat', sans-serif;
      font-size: 13px;
      font-weight: 500;
      color: var(--text-secondary);
      border-bottom: 2px solid transparent;
      transition: all 0.15s;
    }
    .tab:hover { color: var(--navy); }
    .tab.active {
      color: var(--navy);
      border-bottom-color: var(--gold);
      font-weight: 600;
    }
    .tab .count {
      display: inline-block;
      background: var(--gray-line);
      color: var(--text-secondary);
      font-size: 11px;
      padding: 2px 8px;
      border-radius: 10px;
      margin-left: 6px;
      font-weight: 600;
    }
    .tab.active .count {
      background: var(--gold);
      color: white;
    }

    .filters {
      max-width: 1280px;
      margin: 16px auto 0;
      padding: 0 24px;
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }
    .filters select, .filters label {
      font-size: 12px;
    }
    .filters select {
      padding: 7px 10px;
      border: 1px solid var(--gray-line);
      border-radius: 4px;
      background: white;
      color: var(--navy);
      cursor: pointer;
    }
    .filters label {
      color: var(--text-secondary);
      display: flex;
      align-items: center;
      gap: 6px;
      cursor: pointer;
    }

    .cards-area {
      max-width: 1280px;
      margin: 20px auto 60px;
      padding: 0 24px;
    }
    .empty {
      text-align: center;
      padding: 80px 20px;
      color: var(--text-secondary);
      background: white;
      border-radius: 8px;
    }
    .empty h3 {
      font-family: 'Cormorant Garamond', serif;
      font-size: 24px;
      color: var(--navy);
      margin-bottom: 10px;
    }
    .empty p {
      font-size: 14px;
      max-width: 420px;
      margin: 0 auto;
      line-height: 1.6;
    }

    .cards-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
      gap: 16px;
    }
    .card {
      background: white;
      border-radius: 6px;
      padding: 18px 20px;
      border: 1px solid var(--gray-line);
      display: flex;
      flex-direction: column;
      gap: 10px;
      transition: box-shadow 0.15s, transform 0.15s;
    }
    .card:hover {
      box-shadow: 0 8px 24px rgba(26, 40, 66, 0.08);
      transform: translateY(-1px);
    }
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 10px;
    }
    .card-title {
      font-family: 'Cormorant Garamond', serif;
      font-size: 18px;
      font-weight: 600;
      color: var(--navy);
      line-height: 1.3;
      flex: 1;
    }
    .relevancia {
      flex-shrink: 0;
      background: var(--gold-pale);
      color: var(--gold);
      font-size: 11px;
      font-weight: 700;
      padding: 4px 9px;
      border-radius: 4px;
    }
    .relevancia.alta { background: #fef3c7; color: #b45309; }
    .relevancia.maxima { background: var(--gold); color: white; }
    .card-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .badge {
      font-size: 11px;
      padding: 3px 9px;
      border-radius: 3px;
      background: var(--gray-bg);
      color: var(--text-secondary);
      font-weight: 500;
    }
    .badge.estado { background: #e0e7ff; color: #3730a3; }
    .badge.prazo { background: #fef3c7; color: #92400e; }
    .badge.orgao { background: #ecfdf5; color: #065f46; }
    .card-desc {
      font-size: 13px;
      line-height: 1.55;
      color: var(--text-primary);
    }
    .card-actions {
      display: flex;
      gap: 8px;
      margin-top: auto;
      padding-top: 8px;
      border-top: 1px solid var(--gray-bg);
    }
    .card-action {
      background: transparent;
      border: 1px solid var(--gray-line);
      color: var(--text-secondary);
      font-family: 'Montserrat', sans-serif;
      font-size: 11px;
      padding: 6px 11px;
      border-radius: 4px;
      cursor: pointer;
      transition: all 0.15s;
    }
    .card-action:hover {
      border-color: var(--gold);
      color: var(--gold);
    }
    .card-link {
      color: var(--blue);
      text-decoration: none;
      font-size: 11px;
      font-weight: 500;
      margin-left: auto;
      align-self: center;
    }
    .card-link:hover { text-decoration: underline; }

    .toast {
      position: fixed;
      bottom: 24px;
      right: 24px;
      background: var(--navy);
      color: white;
      padding: 14px 22px;
      border-radius: 6px;
      box-shadow: 0 6px 20px rgba(0,0,0,0.2);
      font-size: 13px;
      z-index: 200;
      max-width: 400px;
    }

    .loading {
      text-align: center;
      padding: 60px 20px;
      color: var(--text-secondary);
    }
    .spinner {
      width: 32px;
      height: 32px;
      border: 3px solid var(--gray-line);
      border-top-color: var(--gold);
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
      margin: 0 auto 16px;
    }
    @keyframes spin {
      to { transform: rotate(360deg); }
    }

    @media (max-width: 700px) {
      .header-title { font-size: 18px; }
      .header-subtitle { display: none; }
      .header-left img { height: 32px; }
      .cards-grid { grid-template-columns: 1fr; }
      .tabs { overflow-x: auto; }
      .tab { padding: 12px 14px; font-size: 12px; white-space: nowrap; }
      .btn { font-size: 11px; padding: 8px 12px; }
    }
  </style>
</head>
<body>
  <header>
    <div class="header-inner">
      <div class="header-left">
        <div style="font-family:'Cormorant Garamond', serif; font-size: 24px; font-weight: 700; color: white; letter-spacing: 0.5px;">SP</div>
        <div>
          <div class="header-title">Painel de Oportunidades</div>
          <div class="header-subtitle">Concursos | Recursos | Jurisprudencia</div>
        </div>
      </div>
      <div>
        <button class="btn btn-ghost" onclick="rodarAgora()" id="btn-rodar">Coletar agora</button>
      </div>
    </div>
  </header>

  <div class="status-bar">
    <div class="status-card">
      <div class="label">Nao lidos</div>
      <div class="value" id="stat-nao-lidos">-</div>
    </div>
    <div class="status-card">
      <div class="label">Total no banco</div>
      <div class="value" id="stat-total">-</div>
    </div>
    <div class="status-card">
      <div class="label">Ultima coleta</div>
      <div class="value small" id="stat-ultima">-</div>
    </div>
    <div class="status-card">
      <div class="label">Proxima coleta</div>
      <div class="value small">09:00 e 17:00</div>
    </div>
  </div>

  <div class="tabs">
    <button class="tab active" data-cat="">
      Todos <span class="count" id="count-todos">0</span>
    </button>
    <button class="tab" data-cat="concursos_abertos">
      Concursos Abertos <span class="count" id="count-concursos_abertos">0</span>
    </button>
    <button class="tab" data-cat="recursos_anulacao">
      Recursos / Anulacao <span class="count" id="count-recursos_anulacao">0</span>
    </button>
    <button class="tab" data-cat="jurisprudencia">
      Jurisprudencia <span class="count" id="count-jurisprudencia">0</span>
    </button>
  </div>

  <div class="filters">
    <label>
      <input type="checkbox" id="filtro-lidos"> mostrar lidos tambem
    </label>
    <select id="filtro-estado">
      <option value="">Todos os estados</option>
      <option value="Brasil">Brasil (nacional)</option>
      <option value="MG">MG</option>
      <option value="ES">ES</option>
      <option value="RJ">RJ</option>
      <option value="SP">SP</option>
      <option value="DF">DF</option>
    </select>
  </div>

  <div class="cards-area">
    <div id="cards-container">
      <div class="loading">
        <div class="spinner"></div>
        <div>Carregando oportunidades...</div>
      </div>
    </div>
  </div>

  <script>
    let categoriaAtual = "";
    let mostrarLidos = false;
    let filtroEstado = "";

    document.querySelectorAll('.tab').forEach(tab => {
      tab.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        categoriaAtual = tab.dataset.cat;
        carregarOportunidades();
      });
    });

    document.getElementById('filtro-lidos').addEventListener('change', e => {
      mostrarLidos = e.target.checked;
      carregarOportunidades();
    });

    document.getElementById('filtro-estado').addEventListener('change', e => {
      filtroEstado = e.target.value;
      carregarOportunidades();
    });

    function toast(msg, ms) {
      ms = ms || 4000;
      const el = document.createElement('div');
      el.className = 'toast';
      el.textContent = msg;
      document.body.appendChild(el);
      setTimeout(() => el.remove(), ms);
    }

    function fmtData(iso) {
      if (!iso) return '-';
      try {
        const d = new Date(iso);
        return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
      } catch (e) { return iso.substring(0, 16); }
    }

    function categoriaLabel(cat) {
      const map = {
        'concursos_abertos': 'Concurso aberto',
        'recursos_anulacao': 'Recurso/Anulacao',
        'jurisprudencia': 'Jurisprudencia'
      };
      return map[cat] || cat;
    }

    async function carregarStatus() {
      try {
        const r = await fetch('/api/status');
        const data = await r.json();
        document.getElementById('stat-nao-lidos').textContent = data.nao_lidos;
        document.getElementById('stat-total').textContent = data.total;
        if (data.ultima_execucao) {
          const ue = data.ultima_execucao;
          document.getElementById('stat-ultima').textContent = fmtData(ue.data_execucao) + ' | ' + ue.itens_novos + ' novos';
        } else {
          document.getElementById('stat-ultima').textContent = 'Aguardando primeira coleta';
        }
      } catch (e) {
        console.error('Status error:', e);
      }
    }

    async function carregarContagens() {
      const cats = ['concursos_abertos', 'recursos_anulacao', 'jurisprudencia'];
      let total = 0;
      for (const cat of cats) {
        try {
          const r = await fetch('/api/oportunidades?categoria=' + cat + '&limite=500');
          const data = await r.json();
          document.getElementById('count-' + cat).textContent = data.total;
          total += data.total;
        } catch (e) {}
      }
      document.getElementById('count-todos').textContent = total;
    }

    async function carregarOportunidades() {
      const container = document.getElementById('cards-container');
      container.innerHTML = '<div class="loading"><div class="spinner"></div><div>Carregando...</div></div>';

      const params = new URLSearchParams();
      if (categoriaAtual) params.set('categoria', categoriaAtual);
      if (mostrarLidos) params.set('incluir_lidos', '1');
      if (filtroEstado) params.set('estado', filtroEstado);

      try {
        const r = await fetch('/api/oportunidades?' + params);
        const data = await r.json();
        renderizarCards(data.itens);
      } catch (e) {
        container.innerHTML = '<div class="empty"><h3>Erro ao carregar</h3><p>Tente recarregar</p></div>';
      }
    }

    function renderizarCards(itens) {
      const container = document.getElementById('cards-container');
      if (!itens.length) {
        container.innerHTML = '<div class="empty"><h3>Nenhuma oportunidade</h3><p>O sistema coleta automaticamente as 09:00 e 17:00. Voce tambem pode disparar uma coleta manual com o botao "Coletar agora".</p></div>';
        return;
      }

      const grid = document.createElement('div');
      grid.className = 'cards-grid';

      itens.forEach(item => {
        const card = document.createElement('div');
        card.className = 'card';
        card.dataset.id = item.id;

        let relClass = '';
        if (item.relevancia >= 9) relClass = 'maxima';
        else if (item.relevancia >= 7) relClass = 'alta';

        const badges = [];
        badges.push('<span class="badge">' + categoriaLabel(item.categoria) + '</span>');
        if (item.estado) badges.push('<span class="badge estado">' + escapeHtml(item.estado) + '</span>');
        if (item.orgao) badges.push('<span class="badge orgao">' + escapeHtml(item.orgao) + '</span>');
        if (item.prazo) badges.push('<span class="badge prazo">' + escapeHtml(item.prazo) + '</span>');

        const linkHtml = item.link ? '<a class="card-link" href="' + escapeAttr(item.link) + '" target="_blank" rel="noopener">Ver fonte</a>' : '';

        card.innerHTML =
          '<div class="card-header">' +
            '<div class="card-title">' + escapeHtml(item.titulo) + '</div>' +
            '<div class="relevancia ' + relClass + '">' + item.relevancia + '/10</div>' +
          '</div>' +
          '<div class="card-meta">' + badges.join('') + '</div>' +
          '<div class="card-desc">' + escapeHtml(item.descricao || '') + '</div>' +
          '<div class="card-actions">' +
            '<button class="card-action" onclick="marcarLido(' + item.id + ')">Marcar lido</button>' +
            '<button class="card-action" onclick="arquivar(' + item.id + ')">Arquivar</button>' +
            linkHtml +
          '</div>';
        grid.appendChild(card);
      });

      container.innerHTML = '';
      container.appendChild(grid);
    }

    function escapeHtml(str) {
      if (!str) return '';
      return String(str).replace(/[&<>"']/g, c => ({
        '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
      }[c]));
    }
    function escapeAttr(str) { return escapeHtml(str); }

    async function marcarLido(id) {
      try {
        await fetch('/api/oportunidades/' + id + '/marcar_lido', { method: 'POST' });
        const card = document.querySelector('.card[data-id="' + id + '"]');
        if (card) card.remove();
        carregarStatus();
        carregarContagens();
      } catch (e) { toast('Erro ao marcar'); }
    }

    async function arquivar(id) {
      if (!confirm('Arquivar esta oportunidade?')) return;
      try {
        await fetch('/api/oportunidades/' + id + '/arquivar', { method: 'POST' });
        const card = document.querySelector('.card[data-id="' + id + '"]');
        if (card) card.remove();
        carregarStatus();
        carregarContagens();
      } catch (e) { toast('Erro ao arquivar'); }
    }

    async function rodarAgora() {
      const btn = document.getElementById('btn-rodar');
      if (!confirm('Disparar coleta manual agora? Demora 1-2 minutos.')) return;
      btn.disabled = true;
      btn.textContent = 'Coletando...';

      try {
        const r = await fetch('/cron/manual', { method: 'POST' });
        const data = await r.json();
        if (data.erro) {
          toast('Erro: ' + data.erro, 6000);
          btn.disabled = false;
          btn.textContent = 'Coletar agora';
          return;
        }
        toast(data.mensagem || 'Coleta iniciada', 8000);

        let polls = 0;
        const interval = setInterval(async () => {
          polls++;
          await carregarStatus();
          await carregarContagens();
          await carregarOportunidades();
          if (polls >= 18) {
            clearInterval(interval);
            btn.disabled = false;
            btn.textContent = 'Coletar agora';
          }
        }, 10000);
      } catch (e) {
        toast('Erro ao disparar coleta');
        btn.disabled = false;
        btn.textContent = 'Coletar agora';
      }
    }

    carregarStatus();
    carregarContagens();
    carregarOportunidades();

    setInterval(() => {
      carregarStatus();
      carregarContagens();
    }, 5 * 60 * 1000);
  </script>
</body>
</html>
"""


# Routes
@app.route("/")
def home():
    return Response(HTML_INDEX, mimetype="text/html")


@app.route("/api/oportunidades")
def api_listar():
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
    sql = f"SELECT * FROM oportunidades WHERE {where_sql} ORDER BY relevancia DESC, data_coleta DESC LIMIT ?"
    params.append(limite)

    with db_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    return jsonify({"total": len(rows), "itens": [dict(r) for r in rows]})


@app.route("/api/oportunidades/<int:item_id>/marcar_lido", methods=["POST"])
def api_marcar_lido(item_id):
    with db_conn() as conn:
        conn.execute("UPDATE oportunidades SET lido = 1 WHERE id = ?", (item_id,))
    return jsonify({"ok": True})


@app.route("/api/oportunidades/<int:item_id>/arquivar", methods=["POST"])
def api_arquivar(item_id):
    with db_conn() as conn:
        conn.execute("UPDATE oportunidades SET arquivado = 1 WHERE id = ?", (item_id,))
    return jsonify({"ok": True})


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

    return jsonify({
        "total": total,
        "nao_lidos": nao_lidos,
        "ultima_execucao": dict(ultima) if ultima else None,
        "model": MODEL_NAME,
        "version": APP_VERSION,
    })


@app.route("/cron/diaria", methods=["GET", "POST"])
def cron_diaria():
    if CRON_SECRET:
        secret = request.args.get("secret") or request.headers.get("X-Cron-Secret")
        if secret != CRON_SECRET:
            return jsonify({"erro": "secret invalido"}), 403

    if not ANTHROPIC_API_KEY:
        return jsonify({"erro": "API key nao configurada"}), 500

    log.info("CRON DIARIA disparado")
    try:
        result = executar_coleta_completa(ANTHROPIC_API_KEY)
        log.info("Cron concluido: %s", result)
        return jsonify(result)
    except Exception as e:
        log.error("Cron falhou: %s", e)
        return jsonify({"erro": str(e)}), 500


@app.route("/cron/manual", methods=["POST"])
def cron_manual():
    if not ANTHROPIC_API_KEY:
        return jsonify({"erro": "ANTHROPIC_API_KEY nao configurada"}), 500

    log.info("Disparo MANUAL pela UI")

    def run_collect():
        try:
            executar_coleta_completa(ANTHROPIC_API_KEY)
        except Exception as e:
            log.error("Manual run falhou: %s", e)

    t = threading.Thread(target=run_collect, daemon=True)
    t.start()
    return jsonify({"ok": True, "mensagem": "Coleta iniciada em background. Aguarde 1-2 minutos e recarregue."})


@app.route("/health")
def health():
    return jsonify({"ok": True, "version": APP_VERSION})


# Init
init_db()


def seed_demo_se_vazio():
    with db_conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM oportunidades").fetchone()["c"]
        if count > 0:
            return
        log.info("Banco vazio - inserindo dados de exemplo")
        agora = datetime.now(timezone.utc).isoformat()
        demos = [
            ("concursos_abertos", "[EXEMPLO] PCDF abre concurso para Agente",
             "Concurso da Policia Civil DF com 1.800 vagas. Inscricoes abertas.",
             "PCDF", "DF", "30/06/2026", "https://example.com", 9),
            ("recursos_anulacao", "[EXEMPLO] Banca anula 3 questoes do concurso PCMG",
             "Apos analise de recursos, banca anulou 3 questoes da prova objetiva.",
             "PCMG", "MG", "Recursos ate 15/06", "https://example.com", 8),
            ("jurisprudencia", "[EXEMPLO] STJ: questao fora do edital deve ser anulada",
             "STJ firmou entendimento de que questoes fora do conteudo programatico devem ser anuladas.",
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
