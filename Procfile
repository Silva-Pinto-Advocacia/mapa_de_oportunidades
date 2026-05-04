<!DOCTYPE html>
<html lang="pt-br">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Silva Pinto Advocacia · Painel de Oportunidades</title>
  <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;500;600;700&family=Montserrat:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --gold: #BB904C;
      --gold-light: #D4AF7A;
      --navy: #1a2842;
      --navy-light: #2d3e5e;
      --gray-bg: #f4f4f0;
      --gray-line: #d8d8d2;
      --text-primary: #1a2842;
      --text-secondary: #6b6e76;
    }
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: 'Montserrat', sans-serif;
      background: linear-gradient(135deg, var(--navy) 0%, var(--navy-light) 100%);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }
    .login-card {
      background: white;
      border-radius: 8px;
      padding: 50px 45px;
      max-width: 440px;
      width: 100%;
      box-shadow: 0 25px 60px rgba(0, 0, 0, 0.4);
      border-top: 4px solid var(--gold);
    }
    .logo-area {
      text-align: center;
      margin-bottom: 30px;
    }
    .logo-area img {
      max-width: 130px;
      height: auto;
      margin-bottom: 20px;
    }
    h1 {
      font-family: 'Cormorant Garamond', serif;
      font-size: 26px;
      color: var(--navy);
      font-weight: 600;
      margin-bottom: 6px;
      text-align: center;
    }
    .subtitle {
      text-align: center;
      color: var(--text-secondary);
      font-size: 13px;
      letter-spacing: 1px;
      text-transform: uppercase;
      margin-bottom: 35px;
    }
    label {
      display: block;
      font-size: 13px;
      font-weight: 500;
      color: var(--navy);
      margin-bottom: 8px;
      letter-spacing: 0.3px;
    }
    input[type="password"] {
      width: 100%;
      padding: 14px 16px;
      border: 1.5px solid var(--gray-line);
      border-radius: 6px;
      font-family: 'Montserrat', sans-serif;
      font-size: 15px;
      transition: border-color 0.2s;
    }
    input[type="password"]:focus {
      outline: none;
      border-color: var(--gold);
    }
    button {
      width: 100%;
      padding: 14px;
      background: var(--gold);
      color: white;
      border: none;
      border-radius: 6px;
      font-family: 'Montserrat', sans-serif;
      font-size: 14px;
      font-weight: 600;
      letter-spacing: 1px;
      text-transform: uppercase;
      cursor: pointer;
      margin-top: 24px;
      transition: background 0.2s;
    }
    button:hover {
      background: var(--gold-light);
    }
    .error {
      background: #fef2f2;
      border-left: 3px solid #dc2626;
      color: #991b1b;
      padding: 12px 14px;
      border-radius: 4px;
      font-size: 13px;
      margin-bottom: 20px;
    }
    .footer {
      margin-top: 30px;
      text-align: center;
      font-size: 11px;
      color: var(--text-secondary);
      letter-spacing: 0.5px;
    }
  </style>
</head>
<body>
  <div class="login-card">
    <div class="logo-area">
      <img src="/static/logo.png" alt="Silva Pinto">
      <h1>Painel de Oportunidades</h1>
      <div class="subtitle">Sistema Interno · Acesso Restrito</div>
    </div>

    {% if error %}
      <div class="error">{{ error }}</div>
    {% endif %}

    <form method="POST">
      <label for="token">Senha de acesso</label>
      <input type="password" id="token" name="token" autofocus required>
      <button type="submit">Entrar</button>
    </form>

    <div class="footer">
      Silva Pinto Advocacia · OAB/RJ nº 189.781
    </div>
  </div>
</body>
</html>
