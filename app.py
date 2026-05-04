<!DOCTYPE html>
<html lang="pt-br">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Silva Pinto · Oportunidades</title>
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
      --gray-card: #ffffff;
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
    /* Header */
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
    .header-right {
      display: flex;
      gap: 10px;
      align-items: center;
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

    /* Status bar */
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

    /* Tabs */
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
      letter-spacing: 0.3px;
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

    /* Filters */
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
      font-family: 'Montserrat', sans-serif;
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

    /* Cards grid */
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
      position: relative;
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
      letter-spacing: 0.3px;
      transition: all 0.15s;
    }
    .card-action:hover {
      border-color: var(--gold);
      color: var(--gold);
    }
    .card-action.primary {
      background: var(--navy);
      color: white;
      border-color: var(--navy);
    }
    .card-action.primary:hover {
      background: var(--navy-light);
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

    /* Toast */
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
      animation: slideIn 0.3s ease-out;
      max-width: 400px;
    }
    @keyframes slideIn {
      from { transform: translateY(20px); opacity: 0; }
      to { transform: translateY(0); opacity: 1; }
    }

    /* Loading */
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

    /* Mobile */
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
        <img src="/static/logo.png" alt="Silva Pinto">
        <div>
          <div class="header-title">Painel de Oportunidades</div>
          <div class="header-subtitle">Concursos · Recursos · Jurisprudência</div>
        </div>
      </div>
      <div class="header-right">
        <button class="btn btn-ghost" onclick="rodarAgora()" id="btn-rodar">↻ Coletar agora</button>
        <a class="btn btn-ghost" href="/logout">Sair</a>
      </div>
    </div>
  </header>

  <div class="status-bar" id="status-bar">
    <div class="status-card">
      <div class="label">Não lidos</div>
      <div class="value" id="stat-nao-lidos">—</div>
    </div>
    <div class="status-card">
      <div class="label">Total no banco</div>
      <div class="value" id="stat-total">—</div>
    </div>
    <div class="status-card">
      <div class="label">Última coleta</div>
      <div class="value small" id="stat-ultima">—</div>
    </div>
    <div class="status-card">
      <div class="label">Próxima coleta</div>
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
      Recursos / Anulação <span class="count" id="count-recursos_anulacao">0</span>
    </button>
    <button class="tab" data-cat="jurisprudencia">
      Jurisprudência <span class="count" id="count-jurisprudencia">0</span>
    </button>
  </div>

  <div class="filters">
    <label>
      <input type="checkbox" id="filtro-lidos"> mostrar lidos também
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
    let dadosAtuais = [];

    // Tabs
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

    function toast(msg, ms = 4000) {
      const el = document.createElement('div');
      el.className = 'toast';
      el.textContent = msg;
      document.body.appendChild(el);
      setTimeout(() => el.remove(), ms);
    }

    function fmtData(iso) {
      if (!iso) return '—';
      try {
        const d = new Date(iso);
        return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
      } catch (e) { return iso.substring(0, 16); }
    }

    function categoriaLabel(cat) {
      return {
        'concursos_abertos': 'Concurso aberto',
        'recursos_anulacao': 'Recurso/Anulação',
        'jurisprudencia': 'Jurisprudência',
      }[cat] || cat;
    }

    async function carregarStatus() {
      try {
        const r = await fetch('/api/status');
        const data = await r.json();
        document.getElementById('stat-nao-lidos').textContent = data.nao_lidos;
        document.getElementById('stat-total').textContent = data.total;
        if (data.ultima_execucao) {
          const ue = data.ultima_execucao;
          const txt = `${fmtData(ue.data_execucao)} · ${ue.itens_novos} novos`;
          document.getElementById('stat-ultima').textContent = txt;
        } else {
          document.getElementById('stat-ultima').textContent = 'Aguardando primeira coleta';
        }
      } catch (e) {
        console.error('Status error:', e);
      }
    }

    async function carregarContagens() {
      // Busca não-lidos por categoria pra mostrar nos badges das tabs
      const cats = ['concursos_abertos', 'recursos_anulacao', 'jurisprudencia'];
      let total = 0;
      for (const cat of cats) {
        try {
          const r = await fetch(`/api/oportunidades?categoria=${cat}&limite=500`);
          const data = await r.json();
          document.getElementById(`count-${cat}`).textContent = data.total;
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
        const r = await fetch(`/api/oportunidades?${params}`);
        const data = await r.json();
        dadosAtuais = data.itens;
        renderizarCards(data.itens);
      } catch (e) {
        container.innerHTML = '<div class="empty"><h3>Erro ao carregar</h3><p>Tente recarregar a página</p></div>';
        console.error(e);
      }
    }

    function renderizarCards(itens) {
      const container = document.getElementById('cards-container');
      if (!itens.length) {
        container.innerHTML = `
          <div class="empty">
            <h3>Nenhuma oportunidade ${mostrarLidos ? 'no momento' : 'não lida'}</h3>
            <p>O sistema coleta automaticamente às 09:00 e 17:00. Você também pode disparar uma coleta manual com o botão "Coletar agora" no topo.</p>
          </div>`;
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
        badges.push(`<span class="badge">${categoriaLabel(item.categoria)}</span>`);
        if (item.estado) badges.push(`<span class="badge estado">${item.estado}</span>`);
        if (item.orgao) badges.push(`<span class="badge orgao">${item.orgao}</span>`);
        if (item.prazo) badges.push(`<span class="badge prazo">⏱ ${item.prazo}</span>`);

        card.innerHTML = `
          <div class="card-header">
            <div class="card-title">${escapeHtml(item.titulo)}</div>
            <div class="relevancia ${relClass}">${item.relevancia}/10</div>
          </div>
          <div class="card-meta">${badges.join('')}</div>
          <div class="card-desc">${escapeHtml(item.descricao || '')}</div>
          <div class="card-actions">
            <button class="card-action" onclick="marcarLido(${item.id})">✓ Marcar como lido</button>
            <button class="card-action" onclick="arquivar(${item.id})">⊘ Arquivar</button>
            ${item.link ? `<a class="card-link" href="${escapeAttr(item.link)}" target="_blank" rel="noopener">Ver fonte ↗</a>` : ''}
          </div>
        `;
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
    function escapeAttr(str) {
      return escapeHtml(str);
    }

    async function marcarLido(id) {
      try {
        await fetch(`/api/oportunidades/${id}/marcar_lido`, { method: 'POST' });
        document.querySelector(`.card[data-id="${id}"]`)?.remove();
        carregarStatus();
        carregarContagens();
      } catch (e) { toast('Erro ao marcar'); }
    }

    async function arquivar(id) {
      if (!confirm('Arquivar esta oportunidade? (não aparecerá mais)')) return;
      try {
        await fetch(`/api/oportunidades/${id}/arquivar`, { method: 'POST' });
        document.querySelector(`.card[data-id="${id}"]`)?.remove();
        carregarStatus();
        carregarContagens();
      } catch (e) { toast('Erro ao arquivar'); }
    }

    async function rodarAgora() {
      const btn = document.getElementById('btn-rodar');
      if (!confirm('Disparar coleta manual agora? Demora ~1-2 minutos. (Custo estimado: $0.05-0.20 em API)')) return;
      btn.disabled = true;
      btn.textContent = '⏱ Coletando...';

      try {
        const r = await fetch('/cron/manual', { method: 'POST' });
        const data = await r.json();
        if (data.erro) {
          toast('Erro: ' + data.erro, 6000);
          btn.disabled = false;
          btn.textContent = '↻ Coletar agora';
          return;
        }
        toast(data.mensagem || 'Coleta iniciada — recarregue em ~2 minutos', 8000);

        // Re-poll status every 10s for up to 3 minutes
        let polls = 0;
        const interval = setInterval(async () => {
          polls++;
          await carregarStatus();
          await carregarContagens();
          await carregarOportunidades();
          if (polls >= 18) {
            clearInterval(interval);
            btn.disabled = false;
            btn.textContent = '↻ Coletar agora';
          }
        }, 10000);
      } catch (e) {
        toast('Erro ao disparar coleta');
        btn.disabled = false;
        btn.textContent = '↻ Coletar agora';
      }
    }

    // Inicializa
    carregarStatus();
    carregarContagens();
    carregarOportunidades();

    // Auto-refresh a cada 5 min
    setInterval(() => {
      carregarStatus();
      carregarContagens();
    }, 5 * 60 * 1000);
  </script>
</body>
</html>
