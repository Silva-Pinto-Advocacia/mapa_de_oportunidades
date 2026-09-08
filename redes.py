"""
Silva Pinto Advocacia - Modulo Redes (Instagram)
================================================
Gerencia o Instagram profissional do escritorio direto pela Graph API da Meta:

- Conexao via Login do Facebook (token de pagina de longa duracao)
- Leitura de posts, reels e metricas (alcance, views, salvamentos, seguidores)
- Comentarios: leitura, sugestao de resposta por IA, resposta e ocultacao
- Fila de conteudo: rascunho -> aprovado -> agendado -> publicado
- Geracao de legenda/roteiro/calendario por IA, com regras da OAB
- Publicacao de imagem, carrossel, reel e story (agendada ou imediata)
- Relatorio semanal de desempenho escrito por IA

Banco: usa a mesma conexao do painel (Turso em producao, SQLite local),
entao o token do Instagram sobrevive a reinicios do Render.

Variaveis de ambiente:
  META_APP_ID, META_APP_SECRET   obrigatorias para conectar
  META_GRAPH_VERSION             padrao v23.0
  META_REDIRECT_URI              opcional; padrao <url do app>/redes/callback
  META_SCOPES                    opcional; lista separada por virgula
  META_PAGE_ID                   opcional; forca a pagina a vincular
  ANTHROPIC_API_KEY, MODEL_NAME, MODEL_TIER23, CRON_SECRET  (mesmas do app)
"""

import os
import io
import json
import hmac
import time
import hashlib
import logging
import sqlite3
import threading
import traceback
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests
from flask import Blueprint, request, jsonify, Response, redirect

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
META_APP_ID = os.environ.get("META_APP_ID", "")
META_APP_SECRET = os.environ.get("META_APP_SECRET", "")
GRAPH_VERSION = os.environ.get("META_GRAPH_VERSION", "v23.0")
META_REDIRECT_URI = os.environ.get("META_REDIRECT_URI", "")
META_PAGE_ID = os.environ.get("META_PAGE_ID", "")
META_SCOPES = os.environ.get(
    "META_SCOPES",
    ",".join([
        "pages_show_list",
        "pages_read_engagement",
        "business_management",
        "instagram_basic",
        "instagram_manage_insights",
        "instagram_content_publish",
        "instagram_manage_comments",
    ]),
)
CRON_SECRET = os.environ.get("CRON_SECRET", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL_CONTEUDO = os.environ.get("MODEL_NAME", "claude-sonnet-4-5")
MODEL_RAPIDO = os.environ.get("MODEL_TIER23", "claude-haiku-4-5-20251001")

GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"
FB_DIALOG = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"
TZ = ZoneInfo("America/Sao_Paulo")

DB_PATH = None          # usado so no fallback SQLite
CONN_FACTORY = None     # context manager de conexao fornecido pelo app (Turso ou SQLite)

bp = Blueprint("redes", __name__)


# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------
SCHEMA_REDES = """
        CREATE TABLE IF NOT EXISTS redes_contas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plataforma TEXT NOT NULL DEFAULT 'instagram',
            ig_user_id TEXT UNIQUE,
            username TEXT,
            nome TEXT,
            foto_url TEXT,
            page_id TEXT,
            page_nome TEXT,
            page_token TEXT,
            user_token TEXT,
            token_expira TEXT,
            seguidores INTEGER DEFAULT 0,
            seguindo INTEGER DEFAULT 0,
            total_midias INTEGER DEFAULT 0,
            resumo_json TEXT,
            conectado_em TEXT,
            sincronizado_em TEXT,
            ativo INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS redes_midias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conta_id INTEGER NOT NULL,
            ig_media_id TEXT UNIQUE,
            tipo TEXT,
            product_type TEXT,
            legenda TEXT,
            permalink TEXT,
            media_url TEXT,
            thumbnail_url TEXT,
            publicado_em TEXT,
            curtidas INTEGER DEFAULT 0,
            comentarios INTEGER DEFAULT 0,
            alcance INTEGER DEFAULT 0,
            views INTEGER DEFAULT 0,
            salvos INTEGER DEFAULT 0,
            compartilhamentos INTEGER DEFAULT 0,
            visitas_perfil INTEGER DEFAULT 0,
            novos_seguidores INTEGER DEFAULT 0,
            interacoes INTEGER DEFAULT 0,
            insights_json TEXT,
            atualizado_em TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_redes_midias_data ON redes_midias(publicado_em);

        CREATE TABLE IF NOT EXISTS redes_insights_diario (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conta_id INTEGER NOT NULL,
            data TEXT NOT NULL,
            alcance INTEGER DEFAULT 0,
            novos_seguidores INTEGER DEFAULT 0,
            seguidores_total INTEGER,
            UNIQUE(conta_id, data)
        );

        CREATE TABLE IF NOT EXISTS redes_comentarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conta_id INTEGER NOT NULL,
            ig_comment_id TEXT UNIQUE,
            ig_media_id TEXT,
            usuario TEXT,
            texto TEXT,
            criado_em TEXT,
            oculto INTEGER DEFAULT 0,
            respondido INTEGER DEFAULT 0,
            resposta TEXT,
            sugestao TEXT,
            atualizado_em TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_redes_coment_media ON redes_comentarios(ig_media_id);

        CREATE TABLE IF NOT EXISTS redes_fila (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conta_id INTEGER,
            formato TEXT NOT NULL DEFAULT 'imagem',
            titulo TEXT,
            legenda TEXT,
            hashtags TEXT,
            roteiro TEXT,
            sugestao_visual TEXT,
            imagem_url TEXT,
            video_url TEXT,
            itens_json TEXT,
            origem TEXT,
            status TEXT NOT NULL DEFAULT 'rascunho',
            agendado_para TEXT,
            publicado_em TEXT,
            ig_media_id TEXT,
            permalink TEXT,
            erro TEXT,
            criado_em TEXT NOT NULL,
            atualizado_em TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_redes_fila_status ON redes_fila(status);

        CREATE TABLE IF NOT EXISTS redes_arquivos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT,
            mime TEXT,
            dados BLOB,
            criado_em TEXT
        );

        CREATE TABLE IF NOT EXISTS redes_relatorios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conta_id INTEGER,
            periodo_inicio TEXT,
            periodo_fim TEXT,
            resumo TEXT,
            dados_json TEXT,
            criado_em TEXT
        );

        CREATE TABLE IF NOT EXISTS redes_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quando TEXT,
            tipo TEXT,
            detalhe TEXT,
            sucesso INTEGER DEFAULT 1
        );
"""


def init_tables():
    with db() as conn:
        conn.executescript(SCHEMA_REDES)
    log.info("Tabelas do modulo Redes prontas")


@contextmanager
def _db_sqlite():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def db():
    """Conexao do app (Turso em producao, SQLite local) com a mesma API."""
    return CONN_FACTORY() if CONN_FACTORY else _db_sqlite()


def agora_iso():
    return datetime.now(timezone.utc).isoformat()


def registrar_log(tipo, detalhe, sucesso=True):
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO redes_log (quando, tipo, detalhe, sucesso) VALUES (?,?,?,?)",
                (agora_iso(), tipo, str(detalhe)[:2000], 1 if sucesso else 0),
            )
    except Exception:
        pass


def conta_ativa():
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM redes_contas WHERE ativo=1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def conta_publica(c):
    """Versao da conta sem tokens, segura para o front."""
    if not c:
        return None
    campos = ["id", "username", "nome", "foto_url", "page_id", "page_nome",
              "seguidores", "seguindo", "total_midias", "conectado_em",
              "sincronizado_em"]
    out = {k: c.get(k) for k in campos}
    try:
        out["resumo"] = json.loads(c.get("resumo_json") or "{}")
    except Exception:
        out["resumo"] = {}
    return out


# ---------------------------------------------------------------------------
# Graph API
# ---------------------------------------------------------------------------
class GraphError(Exception):
    pass


def graph_get(path, token, params=None, timeout=60):
    params = dict(params or {})
    params["access_token"] = token
    r = requests.get(f"{GRAPH}/{path.lstrip('/')}", params=params, timeout=timeout)
    return _graph_result(r)


def graph_post(path, token, data=None, timeout=90):
    data = dict(data or {})
    data["access_token"] = token
    r = requests.post(f"{GRAPH}/{path.lstrip('/')}", data=data, timeout=timeout)
    return _graph_result(r)


def _graph_result(r):
    try:
        body = r.json()
    except Exception:
        raise GraphError(f"HTTP {r.status_code}: {r.text[:300]}")
    if r.status_code >= 400 or (isinstance(body, dict) and body.get("error")):
        err = body.get("error", {}) if isinstance(body, dict) else {}
        msg = err.get("message") or str(body)[:300]
        code = err.get("code")
        sub = err.get("error_subcode")
        raise GraphError(f"{msg} (code {code}{'/' + str(sub) if sub else ''})")
    return body


def redirect_uri():
    if META_REDIRECT_URI:
        return META_REDIRECT_URI
    root = request.url_root
    # Render fica atras de proxy HTTPS
    if root.startswith("http://") and "localhost" not in root and "127.0.0.1" not in root:
        root = "https://" + root[len("http://"):]
    return root.rstrip("/") + "/redes/callback"


def _state_novo():
    ts = str(int(time.time()))
    sig = hmac.new(META_APP_SECRET.encode(), ts.encode(), hashlib.sha256).hexdigest()[:24]
    return f"{ts}.{sig}"


def _state_valido(state):
    try:
        ts, sig = state.split(".", 1)
        esperado = hmac.new(META_APP_SECRET.encode(), ts.encode(), hashlib.sha256).hexdigest()[:24]
        return hmac.compare_digest(sig, esperado) and (time.time() - int(ts)) < 900
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Conexao (OAuth)
# ---------------------------------------------------------------------------
@bp.route("/redes/conectar")
def conectar():
    if not META_APP_ID or not META_APP_SECRET:
        return "META_APP_ID / META_APP_SECRET nao configurados no ambiente.", 500
    params = {
        "client_id": META_APP_ID,
        "redirect_uri": redirect_uri(),
        "state": _state_novo(),
        "scope": META_SCOPES,
        "response_type": "code",
    }
    return redirect(f"{FB_DIALOG}?{urlencode(params)}")


@bp.route("/redes/callback")
def callback():
    erro = request.args.get("error_description") or request.args.get("error")
    if erro:
        registrar_log("oauth", erro, False)
        return redirect("/redes?erro=" + erro[:200])
    code = request.args.get("code", "")
    state = request.args.get("state", "")
    if not code or not _state_valido(state):
        return redirect("/redes?erro=state+invalido")
    try:
        curto = graph_get("oauth/access_token", "", {
            "client_id": META_APP_ID,
            "client_secret": META_APP_SECRET,
            "redirect_uri": redirect_uri(),
            "code": code,
        })
        longo = graph_get("oauth/access_token", "", {
            "grant_type": "fb_exchange_token",
            "client_id": META_APP_ID,
            "client_secret": META_APP_SECRET,
            "fb_exchange_token": curto["access_token"],
        })
        user_token = longo["access_token"]
        expira = longo.get("expires_in")
        token_expira = (
            (datetime.now(timezone.utc) + timedelta(seconds=int(expira))).isoformat()
            if expira else None
        )
        paginas = graph_get("me/accounts", user_token, {
            "fields": "id,name,access_token,instagram_business_account{id,username,name,profile_picture_url,followers_count,follows_count,media_count}",
            "limit": 50,
        }).get("data", [])
    except GraphError as e:
        registrar_log("oauth", str(e), False)
        return redirect("/redes?erro=" + str(e)[:200])

    escolhida = None
    for p in paginas:
        if not p.get("instagram_business_account"):
            continue
        if META_PAGE_ID and p["id"] != META_PAGE_ID:
            continue
        escolhida = p
        break
    if not escolhida:
        registrar_log("oauth", "nenhuma pagina com Instagram profissional vinculado", False)
        return redirect("/redes?erro=nenhuma+pagina+com+Instagram+profissional+vinculado")

    ig = escolhida["instagram_business_account"]
    with db() as conn:
        conn.execute("UPDATE redes_contas SET ativo=0")
        conn.execute("""
            INSERT INTO redes_contas
              (plataforma, ig_user_id, username, nome, foto_url, page_id, page_nome,
               page_token, user_token, token_expira, seguidores, seguindo, total_midias,
               conectado_em, ativo)
            VALUES ('instagram',?,?,?,?,?,?,?,?,?,?,?,?,?,1)
            ON CONFLICT(ig_user_id) DO UPDATE SET
              username=excluded.username, nome=excluded.nome, foto_url=excluded.foto_url,
              page_id=excluded.page_id, page_nome=excluded.page_nome,
              page_token=excluded.page_token, user_token=excluded.user_token,
              token_expira=excluded.token_expira, seguidores=excluded.seguidores,
              seguindo=excluded.seguindo, total_midias=excluded.total_midias,
              conectado_em=excluded.conectado_em, ativo=1
        """, (
            ig["id"], ig.get("username"), ig.get("name"), ig.get("profile_picture_url"),
            escolhida["id"], escolhida.get("name"), escolhida["access_token"], user_token,
            token_expira, ig.get("followers_count", 0), ig.get("follows_count", 0),
            ig.get("media_count", 0), agora_iso(),
        ))
    registrar_log("oauth", f"conectado @{ig.get('username')}")
    threading.Thread(target=_sync_seguro, daemon=True).start()
    return redirect("/redes?conectado=1")


@bp.route("/api/redes/desconectar", methods=["POST"])
def desconectar():
    with db() as conn:
        conn.execute("UPDATE redes_contas SET ativo=0, page_token=NULL, user_token=NULL")
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Sincronizacao
# ---------------------------------------------------------------------------
METRICAS_FEED = "reach,saved,shares,total_interactions,views,profile_visits,follows"
METRICAS_REELS = "reach,saved,shares,total_interactions,views"
METRICAS_STORY = "reach,views,shares,replies"
METRICAS_MINIMA = "reach,saved,shares,total_interactions"


def _insights_midia(media_id, product_type, media_type, token):
    if product_type == "STORY":
        conjuntos = [METRICAS_STORY, "reach,views"]
    elif product_type == "REELS" or media_type == "VIDEO":
        conjuntos = [METRICAS_REELS, METRICAS_MINIMA]
    else:
        conjuntos = [METRICAS_FEED, METRICAS_MINIMA, "reach"]
    for metricas in conjuntos:
        try:
            data = graph_get(f"{media_id}/insights", token, {"metric": metricas}).get("data", [])
            out = {}
            for m in data:
                vals = m.get("values") or []
                out[m["name"]] = (vals[0].get("value") if vals else m.get("total_value", {}).get("value", 0)) or 0
            return out
        except GraphError as e:
            ultimo = str(e)
            continue
    log.warning("insights indisponiveis para %s: %s", media_id, ultimo)
    return {}


def sync_midias(conta, max_itens=60):
    token = conta["page_token"]
    ig = conta["ig_user_id"]
    campos = "id,caption,media_type,media_product_type,media_url,thumbnail_url,permalink,timestamp,like_count,comments_count"
    itens = []
    url_params = {"fields": campos, "limit": 25}
    path = f"{ig}/media"
    while len(itens) < max_itens:
        body = graph_get(path, token, url_params)
        itens.extend(body.get("data", []))
        prox = (body.get("paging") or {}).get("cursors", {}).get("after")
        if not prox or not body.get("data"):
            break
        url_params = {"fields": campos, "limit": 25, "after": prox}
    itens = itens[:max_itens]

    novos = 0
    with db() as conn:
        for m in itens:
            ins = _insights_midia(m["id"], m.get("media_product_type"), m.get("media_type"), token)
            conn.execute("""
                INSERT INTO redes_midias
                  (conta_id, ig_media_id, tipo, product_type, legenda, permalink, media_url,
                   thumbnail_url, publicado_em, curtidas, comentarios, alcance, views, salvos,
                   compartilhamentos, visitas_perfil, novos_seguidores, interacoes,
                   insights_json, atualizado_em)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(ig_media_id) DO UPDATE SET
                  legenda=excluded.legenda, media_url=excluded.media_url,
                  thumbnail_url=excluded.thumbnail_url, curtidas=excluded.curtidas,
                  comentarios=excluded.comentarios, alcance=excluded.alcance,
                  views=excluded.views, salvos=excluded.salvos,
                  compartilhamentos=excluded.compartilhamentos,
                  visitas_perfil=excluded.visitas_perfil,
                  novos_seguidores=excluded.novos_seguidores,
                  interacoes=excluded.interacoes, insights_json=excluded.insights_json,
                  atualizado_em=excluded.atualizado_em
            """, (
                conta["id"], m["id"], m.get("media_type"), m.get("media_product_type"),
                m.get("caption"), m.get("permalink"), m.get("media_url"),
                m.get("thumbnail_url"), m.get("timestamp"), m.get("like_count", 0),
                m.get("comments_count", 0), ins.get("reach", 0), ins.get("views", 0),
                ins.get("saved", 0), ins.get("shares", 0), ins.get("profile_visits", 0),
                ins.get("follows", 0), ins.get("total_interactions", 0),
                json.dumps(ins, ensure_ascii=False), agora_iso(),
            ))
            novos += 1
    return novos


def sync_conta(conta):
    token = conta["page_token"]
    ig = conta["ig_user_id"]
    perfil = graph_get(ig, token, {
        "fields": "username,name,biography,website,profile_picture_url,followers_count,follows_count,media_count"
    })
    hoje = datetime.now(TZ).date()
    since = int(datetime.combine(hoje - timedelta(days=29), datetime.min.time(), TZ).timestamp())
    until = int(datetime.combine(hoje, datetime.max.time(), TZ).timestamp())

    diario = {}
    for metrica in ("reach", "follower_count"):
        try:
            data = graph_get(f"{ig}/insights", token, {
                "metric": metrica, "period": "day", "since": since, "until": until,
            }).get("data", [])
            for m in data:
                for v in m.get("values", []):
                    dia = (v.get("end_time") or "")[:10]
                    if dia:
                        diario.setdefault(dia, {})[metrica] = v.get("value") or 0
        except GraphError as e:
            log.warning("insight %s indisponivel: %s", metrica, e)

    resumo = {}
    for janela in (7, 30):
        s = int(datetime.combine(hoje - timedelta(days=janela - 1), datetime.min.time(), TZ).timestamp())
        try:
            data = graph_get(f"{ig}/insights", token, {
                "metric": "profile_views,accounts_engaged,views,total_interactions",
                "period": "day", "metric_type": "total_value", "since": s, "until": until,
            }).get("data", [])
            for m in data:
                resumo[f"{m['name']}_{janela}d"] = (m.get("total_value") or {}).get("value", 0)
        except GraphError as e:
            log.warning("total_value %sd indisponivel: %s", janela, e)
    with db() as conn:
        for dia, vals in diario.items():
            conn.execute("""
                INSERT INTO redes_insights_diario (conta_id, data, alcance, novos_seguidores)
                VALUES (?,?,?,?)
                ON CONFLICT(conta_id, data) DO UPDATE SET
                  alcance=excluded.alcance, novos_seguidores=excluded.novos_seguidores
            """, (conta["id"], dia, vals.get("reach", 0), vals.get("follower_count", 0)))
        conn.execute("""
            INSERT INTO redes_insights_diario (conta_id, data, seguidores_total)
            VALUES (?,?,?)
            ON CONFLICT(conta_id, data) DO UPDATE SET seguidores_total=excluded.seguidores_total
        """, (conta["id"], hoje.isoformat(), perfil.get("followers_count", 0)))
        conn.execute("""
            UPDATE redes_contas SET username=?, nome=?, foto_url=?, seguidores=?, seguindo=?,
              total_midias=?, resumo_json=?, sincronizado_em=? WHERE id=?
        """, (
            perfil.get("username"), perfil.get("name"), perfil.get("profile_picture_url"),
            perfil.get("followers_count", 0), perfil.get("follows_count", 0),
            perfil.get("media_count", 0), json.dumps(resumo), agora_iso(), conta["id"],
        ))
    return resumo


def sync_comentarios(conta, max_midias=25):
    token = conta["page_token"]
    meu_usuario = (conta.get("username") or "").lower()
    with db() as conn:
        midias = conn.execute(
            "SELECT ig_media_id FROM redes_midias WHERE conta_id=? ORDER BY publicado_em DESC LIMIT ?",
            (conta["id"], max_midias),
        ).fetchall()
    total = 0
    for row in midias:
        mid = row["ig_media_id"]
        try:
            data = graph_get(f"{mid}/comments", token, {
                "fields": "id,text,username,timestamp,hidden,replies{id,text,username,timestamp}",
                "limit": 50,
            }).get("data", [])
        except GraphError as e:
            log.warning("comentarios de %s: %s", mid, e)
            continue
        with db() as conn:
            for c in data:
                if (c.get("username") or "").lower() == meu_usuario:
                    continue
                respostas = (c.get("replies") or {}).get("data", [])
                minha = next((r for r in respostas
                              if (r.get("username") or "").lower() == meu_usuario), None)
                conn.execute("""
                    INSERT INTO redes_comentarios
                      (conta_id, ig_comment_id, ig_media_id, usuario, texto, criado_em, oculto,
                       respondido, resposta, atualizado_em)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(ig_comment_id) DO UPDATE SET
                      texto=excluded.texto, oculto=excluded.oculto,
                      respondido=CASE WHEN excluded.respondido=1 THEN 1 ELSE redes_comentarios.respondido END,
                      resposta=COALESCE(excluded.resposta, redes_comentarios.resposta),
                      atualizado_em=excluded.atualizado_em
                """, (
                    conta["id"], c["id"], mid, c.get("username"), c.get("text"),
                    c.get("timestamp"), 1 if c.get("hidden") else 0,
                    1 if minha else 0, minha.get("text") if minha else None, agora_iso(),
                ))
                total += 1
    return total


def sincronizar_tudo():
    conta = conta_ativa()
    if not conta:
        return {"erro": "nenhuma conta conectada"}
    inicio = time.time()
    out = {}
    try:
        out["resumo"] = sync_conta(conta)
        out["midias"] = sync_midias(conta)
        conta = conta_ativa()
        out["comentarios"] = sync_comentarios(conta)
        out["segundos"] = round(time.time() - inicio, 1)
        registrar_log("sync", out)
    except Exception as e:
        log.error("sync falhou: %s\n%s", e, traceback.format_exc())
        registrar_log("sync", str(e), False)
        out["erro"] = str(e)
    return out


def _sync_seguro():
    try:
        sincronizar_tudo()
    except Exception as e:
        log.error("sync em background falhou: %s", e)


# ---------------------------------------------------------------------------
# Publicacao
# ---------------------------------------------------------------------------
def _aguardar_container(container_id, token, timeout=300):
    """Reels e videos precisam ser processados antes do media_publish."""
    fim = time.time() + timeout
    while time.time() < fim:
        st = graph_get(container_id, token, {"fields": "status_code,status"})
        code = st.get("status_code")
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise GraphError(f"container {code}: {st.get('status')}")
        time.sleep(5)
    raise GraphError("tempo esgotado aguardando processamento do video")


def publicar_item(item, conta):
    token = conta["page_token"]
    ig = conta["ig_user_id"]
    legenda = (item.get("legenda") or "").strip()
    hashtags = (item.get("hashtags") or "").strip()
    if hashtags and hashtags not in legenda:
        legenda = f"{legenda}\n\n{hashtags}".strip()
    formato = item.get("formato") or "imagem"
    base = request.url_root if request else ""

    def abs_url(u):
        if not u:
            return u
        if u.startswith("/"):
            root = base or os.environ.get("APP_URL", "")
            if root.startswith("http://") and "localhost" not in root:
                root = "https://" + root[len("http://"):]
            return root.rstrip("/") + u
        return u

    if formato == "imagem":
        if not item.get("imagem_url"):
            raise GraphError("imagem_url obrigatoria para post de imagem")
        c = graph_post(f"{ig}/media", token, {
            "image_url": abs_url(item["imagem_url"]), "caption": legenda,
        })
        container = c["id"]
    elif formato == "reel":
        if not item.get("video_url"):
            raise GraphError("video_url obrigatoria para reel")
        c = graph_post(f"{ig}/media", token, {
            "media_type": "REELS", "video_url": abs_url(item["video_url"]),
            "caption": legenda, "share_to_feed": "true",
        })
        container = c["id"]
        _aguardar_container(container, token)
    elif formato == "story":
        dados = {"media_type": "STORIES"}
        if item.get("video_url"):
            dados["video_url"] = abs_url(item["video_url"])
        elif item.get("imagem_url"):
            dados["image_url"] = abs_url(item["imagem_url"])
        else:
            raise GraphError("story precisa de imagem_url ou video_url")
        c = graph_post(f"{ig}/media", token, dados)
        container = c["id"]
        if "video_url" in dados:
            _aguardar_container(container, token)
    elif formato == "carrossel":
        try:
            itens = json.loads(item.get("itens_json") or "[]")
        except Exception:
            itens = []
        urls = [abs_url(u) for u in itens if u]
        if len(urls) < 2:
            raise GraphError("carrossel precisa de 2 a 10 imagens em itens_json")
        filhos = []
        for u in urls[:10]:
            f = graph_post(f"{ig}/media", token, {"image_url": u, "is_carousel_item": "true"})
            filhos.append(f["id"])
        c = graph_post(f"{ig}/media", token, {
            "media_type": "CAROUSEL", "children": ",".join(filhos), "caption": legenda,
        })
        container = c["id"]
    else:
        raise GraphError(f"formato desconhecido: {formato}")

    pub = graph_post(f"{ig}/media_publish", token, {"creation_id": container})
    media_id = pub["id"]
    permalink = None
    try:
        permalink = graph_get(media_id, token, {"fields": "permalink"}).get("permalink")
    except GraphError:
        pass
    return media_id, permalink


def publicar_da_fila(item_id):
    conta = conta_ativa()
    if not conta:
        return {"erro": "nenhuma conta conectada"}
    with db() as conn:
        item = conn.execute("SELECT * FROM redes_fila WHERE id=?", (item_id,)).fetchone()
        if not item:
            return {"erro": "item nao encontrado"}
        item = dict(item)
        conn.execute("UPDATE redes_fila SET status='publicando', atualizado_em=? WHERE id=?",
                     (agora_iso(), item_id))
    try:
        media_id, permalink = publicar_item(item, conta)
        with db() as conn:
            conn.execute("""
                UPDATE redes_fila SET status='publicado', publicado_em=?, ig_media_id=?,
                  permalink=?, erro=NULL, atualizado_em=? WHERE id=?
            """, (agora_iso(), media_id, permalink, agora_iso(), item_id))
        registrar_log("publicar", f"item {item_id} -> {media_id}")
        return {"ok": True, "ig_media_id": media_id, "permalink": permalink}
    except Exception as e:
        with db() as conn:
            conn.execute("UPDATE redes_fila SET status='erro', erro=?, atualizado_em=? WHERE id=?",
                         (str(e)[:1000], agora_iso(), item_id))
        registrar_log("publicar", f"item {item_id}: {e}", False)
        return {"erro": str(e)}


def publicar_agendados():
    agora = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        pendentes = conn.execute("""
            SELECT id FROM redes_fila
            WHERE status='agendado' AND agendado_para IS NOT NULL AND agendado_para <= ?
            ORDER BY agendado_para ASC LIMIT 5
        """, (agora,)).fetchall()
    resultados = []
    for p in pendentes:
        resultados.append({"id": p["id"], **publicar_da_fila(p["id"])})
    return resultados


# ---------------------------------------------------------------------------
# IA
# ---------------------------------------------------------------------------
CONTEXTO_ESCRITORIO = """Voce escreve para o Instagram do escritorio Silva Pinto Advocacia (@silvapinto.adv),
especializado em direito de concursos publicos: eliminacoes indevidas, TAF, exame psicotecnico,
investigacao social, heteroidentificacao, recursos contra gabarito, nomeacao de aprovados,
preterição, cadastro de reserva e demais litigios de candidatos.

PUBLICO: candidatos de concursos publicos (policia, tribunais, carreiras fiscais, prefeituras),
em geral entre 22 e 40 anos, ansiosos com resultados e prazos, que consomem conteudo no celular.

TOM: autoridade acessivel. Frases curtas. Portugues do Brasil, sem juridiques, sem emojis em excesso
(no maximo 3 por texto). Nunca prometa resultado. Nunca use "garantido", "certeza de vitoria",
"melhor escritorio". Fale em "e possivel", "a Justica tem entendido", "vale analisar seu caso".

REGRAS DA OAB (Provimento 205/2021): publicidade informativa e sobria; sem captacao agressiva,
sem precos, sem "promocao", sem mercantilizar; sem citar clientes reais identificaveis;
o CTA aceitavel e convidar para "saber mais", "analisar o seu caso", "mandar mensagem".
Sempre incluir OAB/RJ 189.781 quando houver assinatura.

FORMATOS:
- imagem: legenda de 600 a 1200 caracteres, primeira linha e o gancho (ate 60 caracteres).
- carrossel: 5 a 8 laminas; titulo curto de cada lamina + texto de apoio de 1 a 2 frases.
- reel: roteiro de 30 a 60 segundos com GANCHO (3s), DESENVOLVIMENTO e CTA; indicar texto na tela.
- story: 1 a 3 frames, texto curtissimo, pergunta ou enquete.
Hashtags: 8 a 15, mistura de nicho (#concursopublico #tafconcurso #direitodosconcursos) e locais (#rj)."""


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=180.0, max_retries=2)


def _chamar(model, prompt, max_tokens=4000, system=None):
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY nao configurada")
    msg = _client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system or CONTEXTO_ESCRITORIO,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()


def _json_robusto(raw):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw)
    except Exception:
        ini, fim = raw.find("{"), raw.rfind("}")
        if ini >= 0 and fim > ini:
            return json.loads(raw[ini:fim + 1])
        raise


def _dados_desempenho(conta_id, dias=30):
    """Resumo compacto do desempenho para alimentar os prompts."""
    limite = (datetime.now(TZ) - timedelta(days=dias)).isoformat()
    with db() as conn:
        midias = conn.execute("""
            SELECT product_type, tipo, legenda, permalink, publicado_em, curtidas, comentarios,
                   alcance, views, salvos, compartilhamentos, visitas_perfil, novos_seguidores,
                   interacoes
            FROM redes_midias WHERE conta_id=? AND publicado_em >= ?
            ORDER BY publicado_em DESC LIMIT 60
        """, (conta_id, limite)).fetchall()
        diario = conn.execute("""
            SELECT data, alcance, novos_seguidores, seguidores_total
            FROM redes_insights_diario WHERE conta_id=? ORDER BY data DESC LIMIT ?
        """, (conta_id, dias)).fetchall()
        comentarios = conn.execute("""
            SELECT usuario, texto, respondido FROM redes_comentarios
            WHERE conta_id=? AND criado_em >= ? ORDER BY criado_em DESC LIMIT 80
        """, (conta_id, limite)).fetchall()
    posts = []
    for m in midias:
        d = dict(m)
        d["legenda"] = (d.get("legenda") or "")[:160]
        try:
            dt = datetime.fromisoformat(d["publicado_em"].replace("+0000", "+00:00")).astimezone(TZ)
            d["dia_semana"] = dt.strftime("%a")
            d["hora"] = dt.strftime("%H:%M")
        except Exception:
            pass
        posts.append(d)
    return {
        "posts": posts,
        "diario": [dict(r) for r in diario],
        "comentarios": [dict(r) for r in comentarios],
    }


def gerar_relatorio(conta):
    dados = _dados_desempenho(conta["id"], 30)
    resumo = json.loads(conta.get("resumo_json") or "{}")
    prompt = f"""Analise o desempenho do Instagram @{conta.get('username')} nos ultimos 30 dias e escreva um
RELATORIO SEMANAL em portugues, em Markdown, para o socio do escritorio ler em 3 minutos.

DADOS DA CONTA: seguidores={conta.get('seguidores')}, posts totais={conta.get('total_midias')}
TOTAIS: {json.dumps(resumo, ensure_ascii=False)}
POSTS (mais recentes primeiro): {json.dumps(dados['posts'], ensure_ascii=False)}
SERIE DIARIA (alcance, novos seguidores): {json.dumps(dados['diario'], ensure_ascii=False)}
COMENTARIOS RECENTES: {json.dumps(dados['comentarios'][:40], ensure_ascii=False)}

ESTRUTURA OBRIGATORIA:
## Resumo em 3 linhas
## O que funcionou (top 3 posts, com link e por que)
## O que nao funcionou
## Formato, dia e horario vencedores
## Perguntas e dores que apareceram nos comentarios
## 5 ideias de conteudo para a proxima semana (tema + formato + gancho)
## Uma acao para fazer hoje

Use so os dados fornecidos; se algo faltar, diga "sem dados". Numeros com separador de milhar.
Nao invente metricas."""
    texto = _chamar(MODEL_CONTEUDO, prompt, max_tokens=3500)
    hoje = datetime.now(TZ).date()
    with db() as conn:
        conn.execute("""
            INSERT INTO redes_relatorios (conta_id, periodo_inicio, periodo_fim, resumo, dados_json, criado_em)
            VALUES (?,?,?,?,?,?)
        """, (conta["id"], (hoje - timedelta(days=30)).isoformat(), hoje.isoformat(),
              texto, json.dumps(resumo), agora_iso()))
    return texto


def _temas_do_mapa(limite=12):
    """Aproveita as oportunidades coletadas pelo painel como sementes de conteudo."""
    try:
        with db() as conn:
            rows = conn.execute("""
                SELECT titulo, descricao, categoria, flag, link FROM oportunidades
                WHERE arquivado=0 ORDER BY data_coleta DESC, relevancia DESC LIMIT ?
            """, (limite,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def gerar_conteudo(tema, formato, contexto_extra="", conta=None):
    dados = _dados_desempenho(conta["id"], 60) if conta else {"posts": []}
    top = sorted(dados["posts"], key=lambda p: (p.get("alcance") or 0), reverse=True)[:5]
    prompt = f"""Crie UM conteudo para o Instagram no formato "{formato}".

TEMA: {tema}
CONTEXTO ADICIONAL: {contexto_extra or '(nenhum)'}
POSTS QUE MAIS ALCANCARAM RECENTEMENTE (para calibrar estilo, nao copiar):
{json.dumps([{'legenda': p['legenda'], 'alcance': p.get('alcance'), 'tipo': p.get('product_type')} for p in top], ensure_ascii=False)}

Responda SOMENTE com JSON puro, sem markdown:
{{
  "titulo": "nome interno curto do post",
  "legenda": "legenda completa pronta para publicar (sem hashtags)",
  "hashtags": "#tag1 #tag2 ...",
  "roteiro": "para reel: roteiro com [GANCHO]/[DESENVOLVIMENTO]/[CTA] e texto na tela; para carrossel: uma lamina por linha 'Lamina N - titulo: texto'; para imagem/story: vazio",
  "sugestao_visual": "descricao objetiva da arte ou cena para o designer/Canva",
  "melhor_horario": "dia da semana e hora sugeridos com justificativa curta"
}}"""
    raw = _chamar(MODEL_CONTEUDO, prompt, max_tokens=3000)
    return _json_robusto(raw)


def gerar_calendario(dias, conta, temas_livres=""):
    dados = _dados_desempenho(conta["id"], 60)
    sementes = _temas_do_mapa()
    prompt = f"""Monte um CALENDARIO DE CONTEUDO de {dias} dias para o Instagram do escritorio.

Distribua entre formatos (aprox.: 40% reel, 30% carrossel, 20% imagem, 10% story) e entre pilares:
educacao (direitos do candidato), atualidade (concursos em andamento), prova social institucional
(sem citar clientes), bastidores e perguntas frequentes. 1 conteudo por dia, pulando domingo.

TEMAS SUGERIDOS PELO SOCIO: {temas_livres or '(nenhum)'}
OPORTUNIDADES RECENTES DO PAINEL (use as mais quentes como pauta):
{json.dumps(sementes, ensure_ascii=False)}
DESEMPENHO RECENTE (formato e alcance):
{json.dumps([{'tipo': p.get('product_type'), 'alcance': p.get('alcance'), 'legenda': p['legenda'][:80]} for p in dados['posts'][:25]], ensure_ascii=False)}

Responda SOMENTE com JSON puro:
{{"itens": [
  {{"dia_offset": 0, "hora": "11:30", "formato": "reel|carrossel|imagem|story",
    "titulo": "nome interno", "tema": "assunto em uma frase", "gancho": "primeira linha",
    "legenda": "legenda completa sem hashtags", "hashtags": "#...",
    "roteiro": "roteiro/laminas quando aplicavel, senao vazio",
    "sugestao_visual": "descricao da arte"}}
]}}"""
    raw = _chamar(MODEL_CONTEUDO, prompt, max_tokens=12000)
    data = _json_robusto(raw)
    itens = data.get("itens", [])
    hoje = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    criados = 0
    with db() as conn:
        for it in itens:
            try:
                h, mi = (it.get("hora") or "11:00").split(":")[:2]
                quando = hoje + timedelta(days=int(it.get("dia_offset", 0)) + 1)
                quando = quando.replace(hour=int(h), minute=int(mi))
            except Exception:
                quando = hoje + timedelta(days=1, hours=11)
            conn.execute("""
                INSERT INTO redes_fila (conta_id, formato, titulo, legenda, hashtags, roteiro,
                  sugestao_visual, origem, status, agendado_para, criado_em, atualizado_em)
                VALUES (?,?,?,?,?,?,?,?,'rascunho',?,?,?)
            """, (
                conta["id"], it.get("formato", "imagem"), it.get("titulo"),
                it.get("legenda"), it.get("hashtags"), it.get("roteiro"),
                it.get("sugestao_visual"), "calendario:" + (it.get("tema") or "")[:120],
                quando.astimezone(timezone.utc).isoformat(), agora_iso(), agora_iso(),
            ))
            criados += 1
    return criados


def sugerir_resposta(comentario, legenda_post=""):
    prompt = f"""Sugira uma resposta curta (ate 280 caracteres) para este comentario no Instagram do escritorio.

POST: {legenda_post[:300]}
COMENTARIO de @{comentario.get('usuario')}: {comentario.get('texto')}

Regras: cordial, sem prometer resultado, sem dar parecer juridico definitivo; se for duvida sobre caso
concreto, convide a mandar mensagem no direct para analise; se for elogio, agradeca de forma humana;
se for ofensa ou spam, responda "OCULTAR". Responda SOMENTE com o texto da resposta."""
    return _chamar(MODEL_RAPIDO, prompt, max_tokens=400)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def _checar_cron():
    if CRON_SECRET:
        secret = request.args.get("secret") or request.headers.get("X-Cron-Secret")
        if secret != CRON_SECRET:
            return jsonify({"erro": "secret invalido"}), 403
    return None


@bp.route("/api/redes/status")
def api_status():
    conta = conta_ativa()
    with db() as conn:
        fila = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) n FROM redes_fila GROUP BY status").fetchall()}
        pend = conn.execute(
            "SELECT COUNT(*) n FROM redes_comentarios WHERE respondido=0 AND oculto=0").fetchone()["n"]
        ult = conn.execute(
            "SELECT * FROM redes_log WHERE tipo='sync' ORDER BY id DESC LIMIT 1").fetchone()
        alcance7 = conn.execute("""
            SELECT COALESCE(SUM(alcance),0) s, COALESCE(SUM(novos_seguidores),0) f
            FROM redes_insights_diario WHERE conta_id=? AND data >= ?
        """, (conta["id"] if conta else -1,
              (datetime.now(TZ).date() - timedelta(days=7)).isoformat())).fetchone()
        alcance30 = conn.execute("""
            SELECT COALESCE(SUM(alcance),0) s, COALESCE(SUM(novos_seguidores),0) f
            FROM redes_insights_diario WHERE conta_id=? AND data >= ?
        """, (conta["id"] if conta else -1,
              (datetime.now(TZ).date() - timedelta(days=30)).isoformat())).fetchone()
    return jsonify({
        "configurado": bool(META_APP_ID and META_APP_SECRET),
        "ia": bool(ANTHROPIC_API_KEY),
        "redirect_uri": redirect_uri() if META_APP_ID else None,
        "conta": conta_publica(conta),
        "fila": fila,
        "comentarios_pendentes": pend,
        "alcance_7d": alcance7["s"], "seguidores_7d": alcance7["f"],
        "alcance_30d": alcance30["s"], "seguidores_30d": alcance30["f"],
        "ultimo_sync": dict(ult) if ult else None,
    })


@bp.route("/api/redes/sync", methods=["POST"])
def api_sync():
    if not conta_ativa():
        return jsonify({"erro": "conecte o Instagram primeiro"}), 400
    threading.Thread(target=_sync_seguro, daemon=True).start()
    return jsonify({"ok": True, "mensagem": "Sincronizacao iniciada. Aguarde 1-2 minutos."})


@bp.route("/api/redes/midias")
def api_midias():
    ordem = request.args.get("ordem", "publicado_em")
    if ordem not in ("publicado_em", "alcance", "views", "salvos", "compartilhamentos",
                     "interacoes", "curtidas", "comentarios", "novos_seguidores"):
        ordem = "publicado_em"
    limite = min(int(request.args.get("limite", 60)), 200)
    with db() as conn:
        rows = conn.execute(f"""
            SELECT id, ig_media_id, tipo, product_type, legenda, permalink, media_url,
                   thumbnail_url, publicado_em, curtidas, comentarios, alcance, views, salvos,
                   compartilhamentos, visitas_perfil, novos_seguidores, interacoes
            FROM redes_midias ORDER BY {ordem} DESC LIMIT ?
        """, (limite,)).fetchall()
    return jsonify({"itens": [dict(r) for r in rows]})


@bp.route("/api/redes/diario")
def api_diario():
    with db() as conn:
        rows = conn.execute("""
            SELECT data, alcance, novos_seguidores, seguidores_total
            FROM redes_insights_diario ORDER BY data ASC
        """).fetchall()
    return jsonify({"itens": [dict(r) for r in rows][-60:]})


@bp.route("/api/redes/comentarios")
def api_comentarios():
    so_pendentes = request.args.get("pendentes", "1") == "1"
    where = "WHERE c.respondido=0 AND c.oculto=0" if so_pendentes else ""
    with db() as conn:
        rows = conn.execute(f"""
            SELECT c.*, m.legenda AS post_legenda, m.permalink AS post_permalink,
                   m.thumbnail_url AS post_thumb, m.media_url AS post_media
            FROM redes_comentarios c LEFT JOIN redes_midias m ON m.ig_media_id=c.ig_media_id
            {where} ORDER BY c.criado_em DESC LIMIT 200
        """).fetchall()
    return jsonify({"itens": [dict(r) for r in rows]})


@bp.route("/api/redes/comentarios/<int:cid>/sugerir", methods=["POST"])
def api_sugerir(cid):
    with db() as conn:
        c = conn.execute("""
            SELECT c.*, m.legenda AS post_legenda FROM redes_comentarios c
            LEFT JOIN redes_midias m ON m.ig_media_id=c.ig_media_id WHERE c.id=?
        """, (cid,)).fetchone()
    if not c:
        return jsonify({"erro": "nao encontrado"}), 404
    try:
        sug = sugerir_resposta(dict(c), c["post_legenda"] or "")
    except Exception as e:
        return jsonify({"erro": str(e)}), 500
    with db() as conn:
        conn.execute("UPDATE redes_comentarios SET sugestao=? WHERE id=?", (sug, cid))
    return jsonify({"ok": True, "sugestao": sug})


@bp.route("/api/redes/comentarios/<int:cid>/responder", methods=["POST"])
def api_responder(cid):
    conta = conta_ativa()
    if not conta:
        return jsonify({"erro": "conecte o Instagram primeiro"}), 400
    texto = (request.get_json(silent=True) or {}).get("texto", "").strip()
    if not texto:
        return jsonify({"erro": "texto vazio"}), 400
    with db() as conn:
        c = conn.execute("SELECT * FROM redes_comentarios WHERE id=?", (cid,)).fetchone()
    if not c:
        return jsonify({"erro": "nao encontrado"}), 404
    try:
        graph_post(f"{c['ig_comment_id']}/replies", conta["page_token"], {"message": texto})
    except GraphError as e:
        return jsonify({"erro": str(e)}), 500
    with db() as conn:
        conn.execute("UPDATE redes_comentarios SET respondido=1, resposta=?, atualizado_em=? WHERE id=?",
                     (texto, agora_iso(), cid))
    return jsonify({"ok": True})


@bp.route("/api/redes/comentarios/<int:cid>/ocultar", methods=["POST"])
def api_ocultar(cid):
    conta = conta_ativa()
    if not conta:
        return jsonify({"erro": "conecte o Instagram primeiro"}), 400
    with db() as conn:
        c = conn.execute("SELECT * FROM redes_comentarios WHERE id=?", (cid,)).fetchone()
    if not c:
        return jsonify({"erro": "nao encontrado"}), 404
    try:
        graph_post(c["ig_comment_id"], conta["page_token"], {"hide": "true"})
    except GraphError as e:
        return jsonify({"erro": str(e)}), 500
    with db() as conn:
        conn.execute("UPDATE redes_comentarios SET oculto=1, atualizado_em=? WHERE id=?",
                     (agora_iso(), cid))
    return jsonify({"ok": True})


@bp.route("/api/redes/comentarios/<int:cid>/marcar", methods=["POST"])
def api_marcar_respondido(cid):
    with db() as conn:
        conn.execute("UPDATE redes_comentarios SET respondido=1, atualizado_em=? WHERE id=?",
                     (agora_iso(), cid))
    return jsonify({"ok": True})


# --- Fila ---
CAMPOS_FILA = ["formato", "titulo", "legenda", "hashtags", "roteiro", "sugestao_visual",
               "imagem_url", "video_url", "itens_json", "origem", "agendado_para"]
STATUS_VALIDOS = ("rascunho", "aprovado", "agendado", "publicando", "publicado", "erro", "descartado")


def _para_utc(valor):
    """Aceita 'YYYY-MM-DDTHH:MM' (horario de Brasilia) ou ISO com fuso."""
    if not valor:
        return None
    try:
        dt = datetime.fromisoformat(valor)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(timezone.utc).isoformat()


@bp.route("/api/redes/fila")
def api_fila():
    status = request.args.get("status", "")
    with db() as conn:
        if status:
            rows = conn.execute("SELECT * FROM redes_fila WHERE status=? ORDER BY COALESCE(agendado_para, criado_em) ASC",
                                (status,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM redes_fila WHERE status!='descartado'
                ORDER BY CASE status WHEN 'erro' THEN 0 WHEN 'agendado' THEN 1 WHEN 'aprovado' THEN 2
                                     WHEN 'rascunho' THEN 3 WHEN 'publicando' THEN 4 ELSE 5 END,
                         COALESCE(agendado_para, criado_em) ASC
            """).fetchall()
    return jsonify({"itens": [dict(r) for r in rows]})


@bp.route("/api/redes/fila", methods=["POST"])
def api_fila_criar():
    d = request.get_json(silent=True) or {}
    conta = conta_ativa()
    vals = {k: d.get(k) for k in CAMPOS_FILA}
    vals["agendado_para"] = _para_utc(vals.get("agendado_para"))
    vals["formato"] = vals.get("formato") or "imagem"
    with db() as conn:
        cur = conn.execute("""
            INSERT INTO redes_fila (conta_id, formato, titulo, legenda, hashtags, roteiro,
              sugestao_visual, imagem_url, video_url, itens_json, origem, status, agendado_para,
              criado_em, atualizado_em)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,'rascunho',?,?,?) RETURNING id
        """, (conta["id"] if conta else None, vals["formato"], vals["titulo"], vals["legenda"],
              vals["hashtags"], vals["roteiro"], vals["sugestao_visual"], vals["imagem_url"],
              vals["video_url"], vals["itens_json"], vals["origem"], vals["agendado_para"],
              agora_iso(), agora_iso()))
        novo_id = cur.fetchone()[0]
    return jsonify({"ok": True, "id": novo_id})


@bp.route("/api/redes/fila/<int:item_id>", methods=["PATCH"])
def api_fila_editar(item_id):
    d = request.get_json(silent=True) or {}
    sets, vals = [], []
    for k in CAMPOS_FILA:
        if k in d:
            v = _para_utc(d[k]) if k == "agendado_para" else d[k]
            sets.append(f"{k}=?")
            vals.append(v)
    if "status" in d:
        if d["status"] not in STATUS_VALIDOS:
            return jsonify({"erro": "status invalido"}), 400
        sets.append("status=?")
        vals.append(d["status"])
        if d["status"] in ("aprovado", "agendado", "rascunho"):
            sets.append("erro=NULL")
    if not sets:
        return jsonify({"erro": "nada para atualizar"}), 400
    sets.append("atualizado_em=?")
    vals.append(agora_iso())
    vals.append(item_id)
    with db() as conn:
        conn.execute(f"UPDATE redes_fila SET {', '.join(sets)} WHERE id=?", vals)
        row = conn.execute("SELECT * FROM redes_fila WHERE id=?", (item_id,)).fetchone()
    return jsonify({"ok": True, "item": dict(row) if row else None})


@bp.route("/api/redes/fila/<int:item_id>", methods=["DELETE"])
def api_fila_excluir(item_id):
    with db() as conn:
        conn.execute("UPDATE redes_fila SET status='descartado', atualizado_em=? WHERE id=?",
                     (agora_iso(), item_id))
    return jsonify({"ok": True})


@bp.route("/api/redes/fila/<int:item_id>/publicar", methods=["POST"])
def api_fila_publicar(item_id):
    return jsonify(publicar_da_fila(item_id))


# --- IA ---
@bp.route("/api/redes/ia/conteudo", methods=["POST"])
def api_ia_conteudo():
    d = request.get_json(silent=True) or {}
    tema = (d.get("tema") or "").strip()
    formato = d.get("formato") or "imagem"
    if not tema:
        return jsonify({"erro": "informe o tema"}), 400
    conta = conta_ativa()
    try:
        c = gerar_conteudo(tema, formato, d.get("contexto", ""), conta)
    except Exception as e:
        log.error("ia conteudo: %s\n%s", e, traceback.format_exc())
        return jsonify({"erro": str(e)}), 500
    with db() as conn:
        cur = conn.execute("""
            INSERT INTO redes_fila (conta_id, formato, titulo, legenda, hashtags, roteiro,
              sugestao_visual, origem, status, criado_em, atualizado_em)
            VALUES (?,?,?,?,?,?,?,?,'rascunho',?,?) RETURNING id
        """, (conta["id"] if conta else None, formato, c.get("titulo") or tema[:80],
              c.get("legenda"), c.get("hashtags"), c.get("roteiro"),
              (c.get("sugestao_visual") or "") + (
                  f"\n\nMelhor horario: {c['melhor_horario']}" if c.get("melhor_horario") else ""),
              "ia:" + tema[:120], agora_iso(), agora_iso()))
        novo_id = cur.fetchone()[0]
    return jsonify({"ok": True, "id": novo_id, "conteudo": c})


@bp.route("/api/redes/ia/calendario", methods=["POST"])
def api_ia_calendario():
    d = request.get_json(silent=True) or {}
    dias = max(3, min(int(d.get("dias", 14)), 30))
    conta = conta_ativa()
    if not conta:
        return jsonify({"erro": "conecte o Instagram primeiro"}), 400

    def run():
        try:
            n = gerar_calendario(dias, conta, d.get("temas", ""))
            registrar_log("calendario", f"{n} rascunhos criados")
        except Exception as e:
            log.error("calendario: %s\n%s", e, traceback.format_exc())
            registrar_log("calendario", str(e), False)

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok": True, "mensagem": f"Gerando calendario de {dias} dias. Aguarde 1-2 minutos e recarregue a fila."})


@bp.route("/api/redes/ia/relatorio", methods=["POST"])
def api_ia_relatorio():
    conta = conta_ativa()
    if not conta:
        return jsonify({"erro": "conecte o Instagram primeiro"}), 400

    def run():
        try:
            gerar_relatorio(conta)
            registrar_log("relatorio", "gerado")
        except Exception as e:
            log.error("relatorio: %s\n%s", e, traceback.format_exc())
            registrar_log("relatorio", str(e), False)

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok": True, "mensagem": "Gerando relatorio. Aguarde 1 minuto e recarregue."})


@bp.route("/api/redes/relatorios")
def api_relatorios():
    with db() as conn:
        rows = conn.execute(
            "SELECT id, periodo_inicio, periodo_fim, resumo, criado_em FROM redes_relatorios ORDER BY id DESC LIMIT 12"
        ).fetchall()
    return jsonify({"itens": [dict(r) for r in rows]})


@bp.route("/api/redes/log")
def api_log():
    with db() as conn:
        rows = conn.execute("SELECT * FROM redes_log ORDER BY id DESC LIMIT 50").fetchall()
    return jsonify({"itens": [dict(r) for r in rows]})


# --- Arquivos (imagens publicas para o Instagram buscar) ---
@bp.route("/api/redes/upload", methods=["POST"])
def api_upload():
    f = request.files.get("arquivo")
    if not f:
        return jsonify({"erro": "envie o campo 'arquivo'"}), 400
    dados = f.read()
    mime = f.mimetype or "application/octet-stream"
    nome = f.filename or "arquivo"
    if mime.startswith("image/") and mime != "image/jpeg":
        # Instagram so aceita JPEG para imagens
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(dados)).convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=92)
            dados = buf.getvalue()
            mime = "image/jpeg"
            nome = nome.rsplit(".", 1)[0] + ".jpg"
        except Exception as e:
            return jsonify({"erro": f"envie JPEG (conversao falhou: {e})"}), 400
    if len(dados) > 8 * 1024 * 1024:
        return jsonify({"erro": "arquivo acima de 8 MB; para videos use uma URL publica (Drive/CDN)"}), 400
    with db() as conn:
        cur = conn.execute("INSERT INTO redes_arquivos (nome, mime, dados, criado_em) VALUES (?,?,?,?) RETURNING id",
                           (nome, mime, dados, agora_iso()))
        aid = cur.fetchone()[0]
    ext = "jpg" if mime == "image/jpeg" else ("mp4" if "mp4" in mime else "bin")
    return jsonify({"ok": True, "url": f"/redes/arquivo/{aid}.{ext}", "mime": mime})


@bp.route("/redes/arquivo/<int:aid>.<ext>")
def servir_arquivo(aid, ext):
    with db() as conn:
        row = conn.execute("SELECT mime, dados FROM redes_arquivos WHERE id=?", (aid,)).fetchone()
    if not row:
        return "nao encontrado", 404
    return Response(row["dados"], mimetype=row["mime"],
                    headers={"Cache-Control": "public, max-age=86400"})


# --- Cron ---
@bp.route("/cron/redes/sync", methods=["GET", "POST"])
def cron_sync():
    bloq = _checar_cron()
    if bloq:
        return bloq
    return jsonify(sincronizar_tudo())


@bp.route("/cron/redes/publicar", methods=["GET", "POST"])
def cron_publicar():
    bloq = _checar_cron()
    if bloq:
        return bloq
    return jsonify({"publicados": publicar_agendados()})


@bp.route("/cron/redes/relatorio", methods=["GET", "POST"])
def cron_relatorio():
    bloq = _checar_cron()
    if bloq:
        return bloq
    conta = conta_ativa()
    if not conta:
        return jsonify({"erro": "nenhuma conta conectada"}), 400
    try:
        sincronizar_tudo()
        texto = gerar_relatorio(conta)
        return jsonify({"ok": True, "chars": len(texto)})
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


# ---------------------------------------------------------------------------
# Pagina
# ---------------------------------------------------------------------------
@bp.route("/redes")
def pagina():
    from redes_html import HTML_REDES
    return HTML_REDES


def init_redes(app, db_path=None, conn_factory=None):
    global DB_PATH, CONN_FACTORY
    DB_PATH = db_path
    CONN_FACTORY = conn_factory
    init_tables()
    app.register_blueprint(bp)
    log.info("Modulo Redes registrado (Graph %s, app %s)", GRAPH_VERSION,
             "configurado" if META_APP_ID else "SEM META_APP_ID")
