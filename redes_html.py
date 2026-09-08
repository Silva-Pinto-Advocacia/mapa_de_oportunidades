# -*- coding: utf-8 -*-
"""Pagina /redes - gestao do Instagram (mesma identidade visual do painel)."""

HTML_REDES = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Silva Pinto - Redes</title>
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,300..900&family=Manrope:wght@300..800&display=swap" rel="stylesheet">
  <style>
    :root {
      --gold: #BB904C; --gold-light: #D4AF7A; --gold-dark: #9a7438; --gold-pale: #f5ecd9;
      --navy: #1a2842; --navy-light: #2d3e5e;
      --bg-page: #faf8f3; --bg-card: #ffffff; --bg-soft: #f0ede4;
      --line: #d8d3c4; --line-soft: #e8e3d4;
      --text-primary: #1a2842; --text-secondary: #6b6e76; --text-muted: #9a9690;
      --red: #b91c1c; --red-bg: #fef2f2; --green: #5d8c5b; --green-bg: #eef5ee;
      --orange: #c2724a; --orange-bg: #fbf1ea; --blue: #3b5a8a; --blue-bg: #eef1f7;
    }
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: 'Manrope', sans-serif; background: var(--bg-page); color: var(--text-primary); min-height: 100vh; font-size: 14px; }
    a { color: var(--blue); }
    header { background: var(--navy); color: white; padding: 14px 24px; box-shadow: 0 2px 12px rgba(26,40,66,.15); position: sticky; top: 0; z-index: 100; }
    .header-inner { max-width: 1400px; margin: 0 auto; display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
    .header-left { display: flex; align-items: center; gap: 16px; }
    .logo-img { height: 70px; width: auto; display: block; }
    .header-text-block { border-left: 1px solid rgba(255,255,255,.15); padding-left: 16px; }
    .header-title { font-family: 'Fraunces', serif; font-size: 22px; font-weight: 600; letter-spacing: .3px; }
    .header-subtitle { font-size: 10px; letter-spacing: 2px; color: var(--gold-light); text-transform: uppercase; margin-top: 4px; }
    .btn { background: var(--gold); color: white; border: none; padding: 10px 18px; border-radius: 4px; font-family: 'Manrope', sans-serif; font-size: 11px; font-weight: 600; letter-spacing: 1px; text-transform: uppercase; cursor: pointer; transition: all .2s; text-decoration: none; display: inline-block; }
    .btn:hover { background: var(--gold-light); }
    .btn:disabled { opacity: .5; cursor: not-allowed; }
    .btn-ghost { background: transparent; border: 1px solid rgba(187,144,76,.5); color: var(--gold-light); }
    .btn-ghost:hover { background: rgba(187,144,76,.1); border-color: var(--gold); color: white; }
    .btn-group { display: flex; gap: 8px; flex-wrap: wrap; }
    .wrap { max-width: 1400px; margin: 0 auto; padding: 0 24px; }
    .status-bar { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-top: 28px; }
    .status-card { background: var(--bg-card); border-radius: 4px; padding: 14px 18px; border-left: 3px solid var(--gold); box-shadow: 0 1px 3px rgba(0,0,0,.04); }
    .status-card .label { font-size: 9px; color: var(--text-secondary); letter-spacing: 1.5px; text-transform: uppercase; margin-bottom: 8px; font-weight: 600; }
    .status-card .value { font-family: 'Fraunces', serif; font-size: 28px; color: var(--navy); font-weight: 600; line-height: 1; }
    .status-card .value.small { font-size: 12px; font-family: 'Manrope', sans-serif; font-weight: 500; }
    .status-card .sub { font-size: 10px; color: var(--text-muted); margin-top: 6px; }
    .perfil { display: flex; align-items: center; gap: 14px; margin-top: 24px; background: var(--bg-card); border: 1px solid var(--line-soft); border-radius: 4px; padding: 14px 18px; }
    .perfil img { width: 52px; height: 52px; border-radius: 50%; object-fit: cover; border: 2px solid var(--gold-pale); }
    .perfil .nome { font-family: 'Fraunces', serif; font-size: 20px; font-weight: 600; }
    .perfil .user { font-size: 12px; color: var(--text-secondary); }
    .perfil .right { margin-left: auto; display: flex; gap: 8px; align-items: center; }
    .setup { margin-top: 24px; background: var(--bg-card); border: 1px solid var(--gold-light); border-radius: 4px; padding: 22px 24px; }
    .setup h2 { font-family: 'Fraunces', serif; font-size: 22px; margin-bottom: 8px; }
    .setup ol { margin: 12px 0 0 20px; line-height: 1.7; font-size: 13px; }
    .setup code { background: var(--bg-soft); padding: 2px 6px; border-radius: 3px; font-size: 12px; word-break: break-all; }
    .tabs { display: flex; gap: 4px; margin-top: 28px; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
    .tab { padding: 10px 16px; font-size: 11px; letter-spacing: 1.2px; text-transform: uppercase; font-weight: 600; color: var(--text-secondary); cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -1px; }
    .tab.ativa { color: var(--navy); border-bottom-color: var(--gold); }
    .tab .n { background: var(--gold-pale); color: var(--gold-dark); border-radius: 10px; padding: 1px 7px; font-size: 10px; margin-left: 6px; }
    .painel { display: none; margin-top: 20px; }
    .painel.ativa { display: block; }
    .toolbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; padding: 12px 16px; background: var(--bg-card); border-radius: 4px; border: 1px solid var(--line-soft); margin-bottom: 16px; }
    .toolbar select, .toolbar input, .toolbar textarea, .form select, .form input, .form textarea {
      font-family: 'Manrope', sans-serif; font-size: 12px; padding: 7px 10px; border: 1px solid var(--line); border-radius: 3px; background: white; color: var(--navy);
    }
    .toolbar label { font-size: 11px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 1px; }
    table { width: 100%; border-collapse: collapse; background: var(--bg-card); border-radius: 4px; overflow: hidden; font-size: 12px; }
    th { text-align: left; font-size: 9px; letter-spacing: 1.5px; text-transform: uppercase; color: var(--text-secondary); padding: 10px 12px; border-bottom: 1px solid var(--line); background: var(--bg-soft); white-space: nowrap; }
    td { padding: 10px 12px; border-bottom: 1px solid var(--line-soft); vertical-align: middle; }
    td.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
    th.num { text-align: right; }
    tr:hover td { background: #fdfcf8; }
    .thumb { width: 44px; height: 44px; object-fit: cover; border-radius: 3px; background: var(--bg-soft); }
    .legenda-curta { max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-primary); }
    .tag { display: inline-block; font-size: 9px; letter-spacing: 1px; text-transform: uppercase; padding: 3px 8px; border-radius: 3px; font-weight: 600; }
    .tag.REELS, .tag.reel { background: #fce7f3; color: #9d174d; }
    .tag.FEED, .tag.imagem { background: var(--blue-bg); color: var(--blue); }
    .tag.STORY, .tag.story { background: var(--orange-bg); color: var(--orange); }
    .tag.carrossel, .tag.CAROUSEL_ALBUM { background: #ede9fe; color: #5b21b6; }
    .tag.rascunho { background: var(--bg-soft); color: var(--text-secondary); }
    .tag.aprovado { background: var(--blue-bg); color: var(--blue); }
    .tag.agendado { background: var(--gold-pale); color: var(--gold-dark); }
    .tag.publicando { background: var(--orange-bg); color: var(--orange); }
    .tag.publicado { background: var(--green-bg); color: var(--green); }
    .tag.erro { background: var(--red-bg); color: var(--red); }
    .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 14px; }
    .card { background: var(--bg-card); border: 1px solid var(--line-soft); border-top: 3px solid var(--gold); border-radius: 4px; padding: 16px 18px; box-shadow: 0 1px 3px rgba(0,0,0,.04); display: flex; flex-direction: column; gap: 10px; }
    .card.erro { border-top-color: var(--red); }
    .card.publicado { border-top-color: var(--green); }
    .card.agendado { border-top-color: var(--gold-dark); }
    .card-top { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .card-titulo { font-family: 'Fraunces', serif; font-size: 18px; font-weight: 600; line-height: 1.2; }
    .card .form { display: grid; gap: 8px; }
    .card .form textarea { width: 100%; min-height: 90px; resize: vertical; line-height: 1.5; }
    .card .form input { width: 100%; }
    .card .form .linha { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .card .form label { font-size: 9px; letter-spacing: 1.2px; text-transform: uppercase; color: var(--text-secondary); font-weight: 600; }
    .card-actions { display: flex; gap: 6px; flex-wrap: wrap; padding-top: 8px; border-top: 1px solid var(--line-soft); }
    .card-action { background: transparent; border: 1px solid var(--line); color: var(--navy); padding: 6px 11px; border-radius: 3px; font-family: 'Manrope', sans-serif; font-size: 10px; font-weight: 600; letter-spacing: .8px; text-transform: uppercase; cursor: pointer; }
    .card-action:hover { border-color: var(--gold); color: var(--gold-dark); }
    .card-action.primaria { background: var(--navy); color: white; border-color: var(--navy); }
    .card-action.primaria:hover { background: var(--navy-light); color: white; }
    .card-action.perigo:hover { border-color: var(--red); color: var(--red); }
    .card .meta { font-size: 11px; color: var(--text-muted); }
    .card .erro-msg { background: var(--red-bg); color: var(--red); font-size: 11px; padding: 8px 10px; border-radius: 3px; }
    .card .roteiro { white-space: pre-wrap; font-size: 12px; line-height: 1.5; background: var(--bg-soft); padding: 10px 12px; border-radius: 3px; max-height: 220px; overflow: auto; }
    .coment { background: var(--bg-card); border: 1px solid var(--line-soft); border-radius: 4px; padding: 14px 16px; display: grid; grid-template-columns: 56px 1fr; gap: 14px; margin-bottom: 10px; }
    .coment img { width: 56px; height: 56px; object-fit: cover; border-radius: 3px; background: var(--bg-soft); }
    .coment .quem { font-weight: 600; font-size: 12px; }
    .coment .quando { color: var(--text-muted); font-size: 11px; margin-left: 8px; }
    .coment .texto { margin: 6px 0 8px; font-size: 13px; line-height: 1.5; }
    .coment .post { font-size: 11px; color: var(--text-secondary); }
    .coment textarea { width: 100%; min-height: 60px; margin-top: 8px; font-family: 'Manrope', sans-serif; font-size: 12px; padding: 8px; border: 1px solid var(--line); border-radius: 3px; }
    .coment .resp { font-size: 12px; color: var(--green); margin-top: 6px; }
    .relatorio { background: var(--bg-card); border: 1px solid var(--line-soft); border-radius: 4px; padding: 26px 30px; line-height: 1.7; font-size: 13.5px; max-width: 900px; }
    .relatorio h2 { font-family: 'Fraunces', serif; font-size: 22px; margin: 22px 0 8px; color: var(--navy); }
    .relatorio h2:first-child { margin-top: 0; }
    .relatorio ul { margin: 6px 0 6px 22px; }
    .relatorio p { margin: 6px 0; }
    .relatorio .quando { font-size: 11px; color: var(--text-muted); letter-spacing: 1px; text-transform: uppercase; margin-bottom: 14px; }
    .form-box { background: var(--bg-card); border: 1px solid var(--line-soft); border-radius: 4px; padding: 22px 24px; max-width: 760px; margin-bottom: 18px; }
    .form-box h3 { font-family: 'Fraunces', serif; font-size: 20px; margin-bottom: 4px; }
    .form-box p.dica { font-size: 12px; color: var(--text-secondary); margin-bottom: 14px; }
    .form-box .form { display: grid; gap: 10px; }
    .form-box .form input, .form-box .form textarea, .form-box .form select { width: 100%; }
    .form-box .form textarea { min-height: 70px; resize: vertical; }
    .form-box .form .linha { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .empty { text-align: center; padding: 40px 20px; color: var(--text-secondary); background: var(--bg-card); border: 1px dashed var(--line); border-radius: 4px; }
    .toast { position: fixed; bottom: 24px; right: 24px; background: var(--navy); color: white; padding: 14px 22px; border-radius: 4px; box-shadow: 0 6px 24px rgba(0,0,0,.25); font-size: 13px; z-index: 200; max-width: 420px; }
    .toast.erro { background: var(--red); }
    .loading { text-align: center; padding: 40px 20px; color: var(--text-secondary); }
    .spinner { width: 32px; height: 32px; border: 3px solid var(--line); border-top-color: var(--gold); border-radius: 50%; animation: spin .8s linear infinite; margin: 0 auto 14px; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .barras { display: flex; align-items: flex-end; gap: 3px; height: 70px; padding: 10px 0 0; }
    .barras div { flex: 1; background: var(--gold-light); border-radius: 2px 2px 0 0; min-height: 2px; position: relative; }
    .barras div:hover { background: var(--gold-dark); }
    .barras div span { display: none; position: absolute; bottom: 100%; left: 50%; transform: translateX(-50%); background: var(--navy); color: white; font-size: 10px; padding: 3px 6px; border-radius: 3px; white-space: nowrap; margin-bottom: 4px; }
    .barras div:hover span { display: block; }
    footer { max-width: 1400px; margin: 60px auto 30px; padding: 20px 24px; border-top: 1px solid var(--line); text-align: center; font-size: 11px; color: var(--text-muted); letter-spacing: 1px; }
    @media (max-width: 700px) {
      .logo-img { height: 50px; } .header-title { font-size: 17px; } .header-subtitle { display: none; }
      .cards { grid-template-columns: 1fr; } .card .form .linha, .form-box .form .linha { grid-template-columns: 1fr; }
      .btn { font-size: 10px; padding: 8px 12px; letter-spacing: .5px; }
      .tabela-wrap { overflow-x: auto; }
    }
    .tabela-wrap { overflow-x: auto; }
  </style>
</head>
<body>
  <header>
    <div class="header-inner">
      <div class="header-left">
        <img class="logo-img" src="/logo.png" alt="Silva Pinto Advocacia">
        <div class="header-text-block">
          <div class="header-title">Redes &middot; Instagram</div>
          <div class="header-subtitle">Desempenho | Conteudo | Comentarios</div>
        </div>
      </div>
      <div class="btn-group">
        <a class="btn btn-ghost" href="/">Painel</a>
        <button class="btn btn-ghost" onclick="sincronizar()" id="btn-sync">Sincronizar</button>
        <a class="btn" href="/redes/conectar" id="btn-conectar">Conectar Instagram</a>
      </div>
    </div>
  </header>

  <div class="wrap">
    <div id="setup" style="display:none"></div>
    <div id="perfil" style="display:none"></div>

    <div class="status-bar">
      <div class="status-card"><div class="label">Seguidores</div><div class="value" id="st-seg">-</div><div class="sub" id="st-seg-sub"></div></div>
      <div class="status-card"><div class="label">Alcance 7 dias</div><div class="value" id="st-alc7">-</div><div class="sub" id="st-alc30"></div></div>
      <div class="status-card"><div class="label">Contas engajadas 30d</div><div class="value" id="st-eng">-</div><div class="sub" id="st-views"></div></div>
      <div class="status-card"><div class="label">Comentarios a responder</div><div class="value" id="st-com">-</div></div>
      <div class="status-card"><div class="label">Fila</div><div class="value" id="st-fila">-</div><div class="sub" id="st-fila-sub"></div></div>
      <div class="status-card"><div class="label">Ultima sincronizacao</div><div class="value small" id="st-sync">-</div></div>
    </div>

    <div class="tabs">
      <div class="tab ativa" data-tab="desempenho">Desempenho</div>
      <div class="tab" data-tab="fila">Fila de conteudo <span class="n" id="n-fila"></span></div>
      <div class="tab" data-tab="comentarios">Comentarios <span class="n" id="n-com"></span></div>
      <div class="tab" data-tab="novo">Criar com IA</div>
      <div class="tab" data-tab="relatorio">Relatorio</div>
    </div>

    <!-- DESEMPENHO -->
    <div class="painel ativa" id="painel-desempenho">
      <div class="toolbar">
        <label>Ordenar por</label>
        <select id="ordem" onchange="carregarMidias()">
          <option value="publicado_em">Mais recentes</option>
          <option value="alcance">Alcance</option>
          <option value="views">Views</option>
          <option value="salvos">Salvamentos</option>
          <option value="compartilhamentos">Compartilhamentos</option>
          <option value="interacoes">Interacoes</option>
          <option value="novos_seguidores">Seguidores gerados</option>
          <option value="comentarios">Comentarios</option>
        </select>
        <div style="margin-left:auto;font-size:11px;color:var(--text-muted)">Alcance diario (30 dias)</div>
      </div>
      <div class="barras" id="barras"></div>
      <div class="tabela-wrap" id="midias"><div class="loading"><div class="spinner"></div>Carregando...</div></div>
    </div>

    <!-- FILA -->
    <div class="painel" id="painel-fila">
      <div class="toolbar">
        <label>Status</label>
        <select id="fila-status" onchange="carregarFila()">
          <option value="">Todos ativos</option>
          <option value="rascunho">Rascunho</option>
          <option value="aprovado">Aprovado</option>
          <option value="agendado">Agendado</option>
          <option value="publicado">Publicado</option>
          <option value="erro">Com erro</option>
        </select>
        <button class="card-action" onclick="novoItemManual()">+ Novo item em branco</button>
        <button class="card-action" onclick="carregarFila()" style="margin-left:auto">Recarregar</button>
      </div>
      <div class="cards" id="fila"></div>
    </div>

    <!-- COMENTARIOS -->
    <div class="painel" id="painel-comentarios">
      <div class="toolbar">
        <label><input type="checkbox" id="com-todos" onchange="carregarComentarios()"> mostrar respondidos</label>
        <button class="card-action" onclick="carregarComentarios()" style="margin-left:auto">Recarregar</button>
      </div>
      <div id="comentarios"></div>
    </div>

    <!-- NOVO -->
    <div class="painel" id="painel-novo">
      <div class="form-box">
        <h3>Um conteudo agora</h3>
        <p class="dica">Diga o tema e o formato. A IA escreve legenda, hashtags, roteiro e a sugestao de arte, seguindo as regras da OAB. O resultado entra na fila como rascunho.</p>
        <div class="form">
          <input id="ia-tema" placeholder="Tema. Ex: reprovado no TAF por 2 segundos na corrida, o que fazer">
          <div class="linha">
            <select id="ia-formato">
              <option value="reel">Reel (roteiro 30-60s)</option>
              <option value="carrossel">Carrossel (5-8 laminas)</option>
              <option value="imagem">Imagem unica</option>
              <option value="story">Story</option>
            </select>
            <input id="ia-contexto" placeholder="Contexto extra (opcional): concurso, banca, decisao recente...">
          </div>
          <div><button class="btn" onclick="gerarConteudo()" id="btn-ia">Gerar rascunho</button></div>
        </div>
      </div>
      <div class="form-box">
        <h3>Calendario de conteudo</h3>
        <p class="dica">Gera um rascunho por dia (menos domingo), misturando formatos e usando as oportunidades quentes do Painel como pauta. Cada item ja vem com dia e horario sugeridos.</p>
        <div class="form">
          <div class="linha">
            <select id="cal-dias"><option value="7">7 dias</option><option value="14" selected>14 dias</option><option value="30">30 dias</option></select>
            <input id="cal-temas" placeholder="Temas que voce quer incluir (opcional)">
          </div>
          <div><button class="btn" onclick="gerarCalendario()" id="btn-cal">Gerar calendario</button></div>
        </div>
      </div>
    </div>

    <!-- RELATORIO -->
    <div class="painel" id="painel-relatorio">
      <div class="toolbar">
        <select id="rel-sel" onchange="mostrarRelatorio()"></select>
        <button class="btn" onclick="gerarRelatorio()" id="btn-rel" style="margin-left:auto">Gerar relatorio agora</button>
      </div>
      <div class="relatorio" id="relatorio"><div class="empty">Nenhum relatorio ainda.</div></div>
    </div>
  </div>

  <footer>Silva Pinto Advocacia &middot; OAB/RJ n&ordm; 189.781 &middot; Sistema Interno</footer>

  <script>
    let STATUS = null;
    let RELATORIOS = [];

    function toast(msg, erro) {
      const el = document.createElement('div');
      el.className = 'toast' + (erro ? ' erro' : '');
      el.textContent = msg;
      document.body.appendChild(el);
      setTimeout(() => el.remove(), erro ? 7000 : 4000);
    }
    function esc(s) { return s == null ? '' : String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
    function num(n) { return (n == null || isNaN(n)) ? '-' : Number(n).toLocaleString('pt-BR'); }
    function fmtData(iso) {
      if (!iso) return '-';
      try { return new Date(iso.replace('+0000', '+00:00')).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }); }
      catch (e) { return iso.substring(0, 16); }
    }
    function paraLocalInput(iso) {
      if (!iso) return '';
      const d = new Date(iso);
      const p = n => String(n).padStart(2, '0');
      return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + 'T' + p(d.getHours()) + ':' + p(d.getMinutes());
    }
    async function api(url, opts) {
      const r = await fetch(url, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts || {}));
      const data = await r.json().catch(() => ({}));
      if (!r.ok || data.erro) throw new Error(data.erro || ('HTTP ' + r.status));
      return data;
    }

    // Tabs
    document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(x => x.classList.remove('ativa'));
      document.querySelectorAll('.painel').forEach(x => x.classList.remove('ativa'));
      t.classList.add('ativa');
      document.getElementById('painel-' + t.dataset.tab).classList.add('ativa');
      if (t.dataset.tab === 'fila') carregarFila();
      if (t.dataset.tab === 'comentarios') carregarComentarios();
      if (t.dataset.tab === 'relatorio') carregarRelatorios();
    }));

    // Status
    async function carregarStatus() {
      try {
        STATUS = await api('/api/redes/status');
        const c = STATUS.conta;
        const setup = document.getElementById('setup');
        const perfil = document.getElementById('perfil');
        const btn = document.getElementById('btn-conectar');
        if (!STATUS.configurado) {
          btn.style.display = 'none';
          setup.style.display = 'block';
          setup.innerHTML = `<div class="setup"><h2>Falta configurar o app da Meta</h2>
            <p style="font-size:13px">Uma vez so, cerca de 15 minutos:</p>
            <ol>
              <li>Entre em <a href="https://developers.facebook.com/apps" target="_blank">developers.facebook.com/apps</a> com o Facebook que administra a pagina <b>silvapinto.adv</b> e crie um app do tipo <b>Empresa</b>.</li>
              <li>Adicione os produtos <b>Login do Facebook</b> e <b>Instagram</b> (Graph API).</li>
              <li>Em Login do Facebook &rarr; Configuracoes, cole em <i>URIs de redirecionamento do OAuth validos</i>: <code>${esc(location.origin)}/redes/callback</code></li>
              <li>Em Casos de uso, habilite: pages_show_list, pages_read_engagement, instagram_basic, instagram_manage_insights, instagram_content_publish, instagram_manage_comments, business_management.</li>
              <li>Copie <b>ID do app</b> e <b>Chave secreta</b> e cadastre no Render como <code>META_APP_ID</code> e <code>META_APP_SECRET</code>. Depois volte aqui e clique em Conectar Instagram.</li>
            </ol></div>`;
        } else if (!c) {
          btn.style.display = '';
          setup.style.display = 'block';
          setup.innerHTML = `<div class="setup"><h2>Conecte o Instagram do escritorio</h2>
            <p style="font-size:13px">Clique em <b>Conectar Instagram</b> no topo, entre com o Facebook que administra a pagina e autorize. Endereco de retorno cadastrado no app da Meta: <code>${esc(STATUS.redirect_uri || '')}</code></p></div>`;
        } else {
          setup.style.display = 'none';
          btn.textContent = 'Reconectar';
          perfil.style.display = 'block';
          perfil.innerHTML = `<div class="perfil">
            ${c.foto_url ? `<img src="${esc(c.foto_url)}" alt="">` : ''}
            <div><div class="nome">${esc(c.nome || c.username)}</div><div class="user">@${esc(c.username)} &middot; pagina ${esc(c.page_nome || c.page_id)} &middot; ${num(c.total_midias)} publicacoes</div></div>
            <div class="right"><button class="card-action perigo" onclick="desconectar()">Desconectar</button></div>
          </div>`;
        }
        document.getElementById('st-seg').textContent = c ? num(c.seguidores) : '-';
        document.getElementById('st-seg-sub').textContent = c ? ('+' + num(STATUS.seguidores_30d) + ' em 30 dias') : '';
        document.getElementById('st-alc7').textContent = num(STATUS.alcance_7d);
        document.getElementById('st-alc30').textContent = num(STATUS.alcance_30d) + ' em 30 dias';
        const r = (c && c.resumo) || {};
        document.getElementById('st-eng').textContent = num(r.accounts_engaged_30d);
        document.getElementById('st-views').textContent = r.views_30d != null ? (num(r.views_30d) + ' views em 30 dias') : '';
        document.getElementById('st-com').textContent = num(STATUS.comentarios_pendentes);
        document.getElementById('n-com').textContent = STATUS.comentarios_pendentes || '';
        const f = STATUS.fila || {};
        const ativos = (f.rascunho || 0) + (f.aprovado || 0) + (f.agendado || 0);
        document.getElementById('st-fila').textContent = num(f.agendado || 0);
        document.getElementById('st-fila-sub').textContent = 'agendados · ' + (f.rascunho || 0) + ' rascunhos · ' + (f.publicado || 0) + ' publicados';
        document.getElementById('n-fila').textContent = ativos || '';
        const u = STATUS.ultimo_sync;
        document.getElementById('st-sync').textContent = u ? (fmtData(u.quando) + (u.sucesso ? '' : ' [erro]')) : 'nunca';
      } catch (e) { console.error(e); }
    }

    async function sincronizar() {
      const b = document.getElementById('btn-sync'); b.disabled = true;
      try { const r = await api('/api/redes/sync', { method: 'POST' }); toast(r.mensagem); }
      catch (e) { toast(e.message, true); }
      setTimeout(() => { b.disabled = false; carregarTudo(); }, 60000);
    }
    async function desconectar() {
      if (!confirm('Desconectar o Instagram? Os dados ja coletados continuam salvos.')) return;
      await api('/api/redes/desconectar', { method: 'POST' }); carregarStatus();
    }

    // Desempenho
    async function carregarMidias() {
      const el = document.getElementById('midias');
      try {
        const d = await api('/api/redes/midias?ordem=' + document.getElementById('ordem').value);
        if (!d.itens.length) { el.innerHTML = '<div class="empty">Nenhum post sincronizado ainda. Conecte e clique em Sincronizar.</div>'; return; }
        el.innerHTML = `<table><thead><tr>
          <th></th><th>Post</th><th>Tipo</th><th>Data</th>
          <th class="num">Alcance</th><th class="num">Views</th><th class="num">Curtidas</th><th class="num">Coment.</th>
          <th class="num">Salvos</th><th class="num">Compart.</th><th class="num">Interacoes</th><th class="num">Seguidores</th></tr></thead><tbody>` +
          d.itens.map(m => `<tr>
            <td><a href="${esc(m.permalink)}" target="_blank">${(m.thumbnail_url || m.media_url) ? `<img class="thumb" src="${esc(m.thumbnail_url || m.media_url)}" loading="lazy">` : '<div class="thumb"></div>'}</a></td>
            <td class="legenda-curta" title="${esc(m.legenda)}">${esc((m.legenda || '(sem legenda)').split('\n')[0])}</td>
            <td><span class="tag ${esc(m.product_type || m.tipo)}">${esc(m.product_type || m.tipo)}</span></td>
            <td style="white-space:nowrap">${fmtData(m.publicado_em)}</td>
            <td class="num">${num(m.alcance)}</td><td class="num">${num(m.views)}</td>
            <td class="num">${num(m.curtidas)}</td><td class="num">${num(m.comentarios)}</td>
            <td class="num">${num(m.salvos)}</td><td class="num">${num(m.compartilhamentos)}</td>
            <td class="num">${num(m.interacoes)}</td><td class="num">${num(m.novos_seguidores)}</td>
          </tr>`).join('') + '</tbody></table>';
      } catch (e) { el.innerHTML = '<div class="empty">' + esc(e.message) + '</div>'; }
    }
    async function carregarDiario() {
      try {
        const d = await api('/api/redes/diario');
        const itens = d.itens.slice(-30);
        const max = Math.max(1, ...itens.map(i => i.alcance || 0));
        document.getElementById('barras').innerHTML = itens.map(i =>
          `<div style="height:${Math.max(3, Math.round((i.alcance || 0) / max * 60))}px"><span>${esc(i.data.slice(5))} · ${num(i.alcance)} alcance · +${num(i.novos_seguidores)} seg.</span></div>`).join('');
      } catch (e) {}
    }

    // Fila
    async function carregarFila() {
      const el = document.getElementById('fila');
      try {
        const d = await api('/api/redes/fila?status=' + document.getElementById('fila-status').value);
        if (!d.itens.length) { el.innerHTML = '<div class="empty">Fila vazia. Use "Criar com IA" ou adicione um item em branco.</div>'; return; }
        el.innerHTML = d.itens.map(cardFila).join('');
      } catch (e) { el.innerHTML = '<div class="empty">' + esc(e.message) + '</div>'; }
    }
    function cardFila(it) {
      const editavel = !['publicado', 'publicando'].includes(it.status);
      const precisaVideo = it.formato === 'reel';
      const precisaItens = it.formato === 'carrossel';
      return `<div class="card ${esc(it.status)}" id="item-${it.id}">
        <div class="card-top"><span class="tag ${esc(it.formato)}">${esc(it.formato)}</span><span class="tag ${esc(it.status)}">${esc(it.status)}</span>
          <span class="meta">${it.agendado_para ? 'agenda: ' + fmtData(it.agendado_para) : ''}${it.publicado_em ? 'publicado: ' + fmtData(it.publicado_em) : ''}</span></div>
        <div class="card-titulo">${esc(it.titulo || '(sem titulo)')}</div>
        ${it.erro ? `<div class="erro-msg">${esc(it.erro)}</div>` : ''}
        ${it.permalink ? `<div class="meta"><a href="${esc(it.permalink)}" target="_blank">Ver no Instagram</a></div>` : ''}
        ${it.origem ? `<div class="meta">origem: ${esc(it.origem)}</div>` : ''}
        ${editavel ? `<div class="form">
          <label>Titulo</label><input data-k="titulo" value="${esc(it.titulo)}">
          <label>Legenda</label><textarea data-k="legenda">${esc(it.legenda)}</textarea>
          <label>Hashtags</label><input data-k="hashtags" value="${esc(it.hashtags)}">
          ${it.roteiro ? `<label>Roteiro / laminas</label><textarea data-k="roteiro" style="min-height:140px">${esc(it.roteiro)}</textarea>` : ''}
          ${it.sugestao_visual ? `<label>Sugestao visual</label><div class="roteiro">${esc(it.sugestao_visual)}</div>` : ''}
          <div class="linha">
            <div><label>${precisaVideo ? 'URL do video (MP4 publico)' : 'URL da imagem (JPEG publico)'}</label>
              <input data-k="${precisaVideo ? 'video_url' : 'imagem_url'}" value="${esc(precisaVideo ? it.video_url : it.imagem_url)}" placeholder="https://..."></div>
            <div><label>ou enviar arquivo</label><input type="file" accept="${precisaVideo ? 'video/mp4' : 'image/*'}" onchange="upload(${it.id}, this, '${precisaVideo ? 'video_url' : 'imagem_url'}')"></div>
          </div>
          ${precisaItens ? `<label>Imagens do carrossel (JSON: ["url1","url2"]) ou envie varias</label><input data-k="itens_json" value="${esc(it.itens_json)}"><input type="file" multiple accept="image/*" onchange="uploadVarios(${it.id}, this)">` : ''}
          <div class="linha"><div><label>Agendar para (horario de Brasilia)</label><input type="datetime-local" data-k="agendado_para" value="${paraLocalInput(it.agendado_para)}"></div></div>
        </div>` : `<div class="roteiro">${esc(it.legenda)}${it.hashtags ? '\n\n' + esc(it.hashtags) : ''}</div>`}
        <div class="card-actions">
          ${editavel ? `<button class="card-action" onclick="salvarItem(${it.id})">Salvar</button>` : ''}
          ${it.status === 'rascunho' ? `<button class="card-action" onclick="mudarStatus(${it.id}, 'aprovado')">Aprovar</button>` : ''}
          ${['rascunho', 'aprovado', 'erro'].includes(it.status) ? `<button class="card-action" onclick="agendar(${it.id})">Agendar</button>` : ''}
          ${it.status === 'agendado' ? `<button class="card-action" onclick="mudarStatus(${it.id}, 'aprovado')">Cancelar agenda</button>` : ''}
          ${editavel ? `<button class="card-action primaria" onclick="publicarAgora(${it.id})">Publicar agora</button>` : ''}
          ${editavel ? `<button class="card-action perigo" onclick="descartar(${it.id})">Descartar</button>` : ''}
        </div>
      </div>`;
    }
    function coletar(id) {
      const out = {};
      document.querySelectorAll(`#item-${id} [data-k]`).forEach(el => { out[el.dataset.k] = el.value; });
      return out;
    }
    async function salvarItem(id, extra) {
      try { await api('/api/redes/fila/' + id, { method: 'PATCH', body: JSON.stringify(Object.assign(coletar(id), extra || {})) }); toast('Salvo.'); carregarFila(); carregarStatus(); }
      catch (e) { toast(e.message, true); }
    }
    async function mudarStatus(id, status) { await salvarItem(id, { status }); }
    async function agendar(id) {
      const v = coletar(id).agendado_para;
      if (!v) { toast('Preencha a data e hora antes de agendar.', true); return; }
      await salvarItem(id, { status: 'agendado' });
    }
    async function publicarAgora(id) {
      if (!confirm('Publicar este item no Instagram agora?')) return;
      try {
        await api('/api/redes/fila/' + id, { method: 'PATCH', body: JSON.stringify(coletar(id)) });
        toast('Publicando... pode levar ate 2 minutos para reels.');
        const r = await api('/api/redes/fila/' + id + '/publicar', { method: 'POST' });
        toast('Publicado! ' + (r.permalink || ''));
      } catch (e) { toast(e.message, true); }
      carregarFila(); carregarStatus();
    }
    async function descartar(id) {
      if (!confirm('Descartar este item?')) return;
      await api('/api/redes/fila/' + id, { method: 'DELETE' }); carregarFila(); carregarStatus();
    }
    async function novoItemManual() {
      const r = await api('/api/redes/fila', { method: 'POST', body: JSON.stringify({ formato: 'imagem', titulo: 'Novo post', legenda: '' }) });
      document.getElementById('fila-status').value = '';
      await carregarFila();
      const el = document.getElementById('item-' + r.id); if (el) el.scrollIntoView({ behavior: 'smooth' });
    }
    async function upload(id, input, campo) {
      const f = input.files[0]; if (!f) return;
      const fd = new FormData(); fd.append('arquivo', f);
      toast('Enviando arquivo...');
      try {
        const r = await fetch('/api/redes/upload', { method: 'POST', body: fd }).then(x => x.json());
        if (r.erro) throw new Error(r.erro);
        const el = document.querySelector(`#item-${id} [data-k="${campo}"]`); if (el) el.value = r.url;
        await salvarItem(id);
      } catch (e) { toast(e.message, true); }
    }
    async function uploadVarios(id, input) {
      const urls = [];
      for (const f of input.files) {
        const fd = new FormData(); fd.append('arquivo', f);
        const r = await fetch('/api/redes/upload', { method: 'POST', body: fd }).then(x => x.json());
        if (r.url) urls.push(r.url);
      }
      const el = document.querySelector(`#item-${id} [data-k="itens_json"]`);
      let atuais = []; try { atuais = JSON.parse(el.value || '[]'); } catch (e) {}
      el.value = JSON.stringify(atuais.concat(urls));
      await salvarItem(id);
    }

    // Comentarios
    async function carregarComentarios() {
      const el = document.getElementById('comentarios');
      const todos = document.getElementById('com-todos').checked;
      try {
        const d = await api('/api/redes/comentarios?pendentes=' + (todos ? '0' : '1'));
        if (!d.itens.length) { el.innerHTML = '<div class="empty">Nenhum comentario pendente.</div>'; return; }
        el.innerHTML = d.itens.map(c => `<div class="coment" id="com-${c.id}">
          <a href="${esc(c.post_permalink)}" target="_blank">${(c.post_thumb || c.post_media) ? `<img src="${esc(c.post_thumb || c.post_media)}">` : '<div style="width:56px;height:56px;background:var(--bg-soft)"></div>'}</a>
          <div>
            <span class="quem">@${esc(c.usuario)}</span><span class="quando">${fmtData(c.criado_em)}</span>
            <div class="texto">${esc(c.texto)}</div>
            <div class="post">no post: ${esc((c.post_legenda || '').split('\n')[0].slice(0, 90))}</div>
            ${c.respondido ? `<div class="resp">Respondido${c.resposta ? ': ' + esc(c.resposta) : ''}</div>` : `
              <textarea id="resp-${c.id}" placeholder="Sua resposta...">${esc(c.sugestao || '')}</textarea>
              <div class="card-actions" style="border:0;padding-top:6px">
                <button class="card-action" onclick="sugerir(${c.id})">Sugerir com IA</button>
                <button class="card-action primaria" onclick="responder(${c.id})">Responder</button>
                <button class="card-action" onclick="marcar(${c.id})">Marcar como tratado</button>
                <button class="card-action perigo" onclick="ocultar(${c.id})">Ocultar</button>
              </div>`}
          </div></div>`).join('');
      } catch (e) { el.innerHTML = '<div class="empty">' + esc(e.message) + '</div>'; }
    }
    async function sugerir(id) {
      try { const r = await api(`/api/redes/comentarios/${id}/sugerir`, { method: 'POST' }); document.getElementById('resp-' + id).value = r.sugestao; }
      catch (e) { toast(e.message, true); }
    }
    async function responder(id) {
      const texto = document.getElementById('resp-' + id).value.trim();
      if (!texto) { toast('Escreva a resposta.', true); return; }
      try { await api(`/api/redes/comentarios/${id}/responder`, { method: 'POST', body: JSON.stringify({ texto }) }); toast('Resposta publicada.'); carregarComentarios(); carregarStatus(); }
      catch (e) { toast(e.message, true); }
    }
    async function marcar(id) { await api(`/api/redes/comentarios/${id}/marcar`, { method: 'POST' }); carregarComentarios(); carregarStatus(); }
    async function ocultar(id) {
      if (!confirm('Ocultar este comentario no Instagram?')) return;
      try { await api(`/api/redes/comentarios/${id}/ocultar`, { method: 'POST' }); carregarComentarios(); carregarStatus(); }
      catch (e) { toast(e.message, true); }
    }

    // IA
    async function gerarConteudo() {
      const b = document.getElementById('btn-ia'); b.disabled = true; toast('Escrevendo... 20 a 40 segundos.');
      try {
        const r = await api('/api/redes/ia/conteudo', { method: 'POST', body: JSON.stringify({
          tema: document.getElementById('ia-tema').value, formato: document.getElementById('ia-formato').value,
          contexto: document.getElementById('ia-contexto').value }) });
        toast('Rascunho criado na fila.');
        document.querySelector('.tab[data-tab="fila"]').click();
        setTimeout(() => { const el = document.getElementById('item-' + r.id); if (el) el.scrollIntoView({ behavior: 'smooth' }); }, 600);
      } catch (e) { toast(e.message, true); }
      b.disabled = false;
    }
    async function gerarCalendario() {
      const b = document.getElementById('btn-cal'); b.disabled = true;
      try {
        const r = await api('/api/redes/ia/calendario', { method: 'POST', body: JSON.stringify({ dias: document.getElementById('cal-dias').value, temas: document.getElementById('cal-temas').value }) });
        toast(r.mensagem, false);
      } catch (e) { toast(e.message, true); }
      setTimeout(() => b.disabled = false, 15000);
    }

    // Relatorio
    function md(texto) {
      const linhas = (texto || '').split('\n'); let html = ''; let lista = false;
      const inline = s => esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/\[(.+?)\]\((https?:[^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
      for (const l of linhas) {
        if (/^\s*[-*] /.test(l)) { if (!lista) { html += '<ul>'; lista = true; } html += '<li>' + inline(l.replace(/^\s*[-*] /, '')) + '</li>'; continue; }
        if (lista) { html += '</ul>'; lista = false; }
        if (/^#{1,3} /.test(l)) html += '<h2>' + inline(l.replace(/^#+ /, '')) + '</h2>';
        else if (l.trim()) html += '<p>' + inline(l) + '</p>';
      }
      if (lista) html += '</ul>';
      return html;
    }
    async function carregarRelatorios() {
      try {
        const d = await api('/api/redes/relatorios'); RELATORIOS = d.itens;
        const sel = document.getElementById('rel-sel');
        sel.innerHTML = RELATORIOS.map((r, i) => `<option value="${i}">${fmtData(r.criado_em)} · ${r.periodo_inicio} a ${r.periodo_fim}</option>`).join('');
        mostrarRelatorio();
      } catch (e) {}
    }
    function mostrarRelatorio() {
      const i = document.getElementById('rel-sel').value || 0;
      const r = RELATORIOS[i];
      document.getElementById('relatorio').innerHTML = r ? `<div class="quando">Gerado em ${fmtData(r.criado_em)}</div>` + md(r.resumo) : '<div class="empty">Nenhum relatorio ainda. Clique em "Gerar relatorio agora".</div>';
    }
    async function gerarRelatorio() {
      const b = document.getElementById('btn-rel'); b.disabled = true;
      try { const r = await api('/api/redes/ia/relatorio', { method: 'POST' }); toast(r.mensagem); }
      catch (e) { toast(e.message, true); }
      setTimeout(() => { b.disabled = false; carregarRelatorios(); }, 60000);
    }

    function carregarTudo() { carregarStatus(); carregarMidias(); carregarDiario(); }
    const q = new URLSearchParams(location.search);
    if (q.get('conectado')) toast('Instagram conectado. Sincronizando os primeiros dados...');
    if (q.get('erro')) toast('Erro na conexao: ' + q.get('erro'), true);
    carregarTudo();
    setInterval(carregarStatus, 3 * 60 * 1000);
  </script>
</body>
</html>
"""
