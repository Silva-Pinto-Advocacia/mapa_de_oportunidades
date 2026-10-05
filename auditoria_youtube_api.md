# Auditoria da API do YouTube — respostas prontas para o formulário

**Para que serve:** enquanto o projeto não passa por esta auditoria, o YouTube trava como **privado** todo vídeo enviado pela API. O sistema agora avisa no card quando isso acontecer. A auditoria libera os vídeos para saírem públicos. Pedir mais cota é opcional: a cota padrão de 10.000 unidades por dia dá uns 6 envios diários, o que basta para o escritório.

**Onde preencher:** *YouTube API Services — Audit and Quota Extension Form*
https://support.google.com/youtube/contact/yt_api_form

Preencha logada na **intimacoes@**, que é a dona do projeto no Google Cloud. O formulário é em inglês. As respostas abaixo já estão em inglês, prontas para colar. Os campos entre colchetes você completa.

---

## 1. Dados da organização e do projeto

| Campo | Resposta |
|---|---|
| Organization name | Silva Pinto Advocacia |
| Organization website | https://silvapintoadvocacia.com |
| Contact name / email | [seu nome] / contato@silvapintoadvocacia.com |
| Google Cloud project ID | reliable-bruin-495404-v9 |
| Google Cloud project number | [Google Cloud > Painel do projeto > "Número do projeto"] |
| OAuth client | silvapinto-drive (Web application) |
| API used | YouTube Data API v3 |
| Scopes | `https://www.googleapis.com/auth/youtube.upload`, `https://www.googleapis.com/auth/youtube.readonly` |
| YouTube channel(s) | Silva Pinto Advocacia — [URL do canal, ex.: https://www.youtube.com/@...] |
| Privacy policy URL | [URL da política de privacidade em silvapintoadvocacia.com] |
| Terms of service URL | [URL dos termos em silvapintoadvocacia.com] |

## 2. Descrição do uso (cole como está)

**What does your API client do?**

> Silva Pinto Advocacia is a Brazilian law firm. We built an internal content tool, used only by our own staff, to publish short educational videos (YouTube Shorts) about public-service exam law on our own YouTube channel. A staff member writes and reviews a short video in our internal system and clicks "Publish". The system then uploads that video to our channel with the YouTube Data API (videos.insert, resumable upload). The same video also goes to our Instagram, Facebook and TikTok accounts.

**Who are the users?**

> Only employees of the firm, who must log in to our internal system. The API client is not offered to the public or to third parties, and it does not access any YouTube account other than our own channel. One channel is connected: the firm's own channel, authorized by the firm's Google account through OAuth 2.0.

**Which API endpoints do you call, and why?**

> - `videos.insert` (scope youtube.upload): upload one Short at a time, only when a staff member clicks "Publish". The title, description and privacy status are set by us. `selfDeclaredMadeForKids` is set to false.
> - `channels.list?mine=true` (scope youtube.readonly): read the connected channel's id and title once, when the account is connected, so that the staff can see which channel is linked.
>
> We do not read, store or analyze data about other channels, viewers, comments or analytics.

**How do you store YouTube data?**

> We store only the OAuth refresh token, the short-lived access token, the connected channel's id and title, and the id/URL of each video we uploaded. They are kept in our private server-side database, reachable only by our authenticated staff. Nothing is shared with third parties or used for advertising. Disconnecting the account in our system, or revoking access in the Google account, ends all access.

**Expected volume**

> About 1 to 3 uploads per day (at most 6). The default quota of 10,000 units per day is enough. We are requesting the audit so that uploaded videos can be public, not more quota.

## 3. O que anexar ou mostrar

O formulário costuma pedir uma demonstração do fluxo. Grave a tela (1 a 2 minutos) mostrando:
1. o login no sistema (https://silvapinto-comercial.onrender.com/login);
2. **Marketing > Redes**, com o botão **Conectar YouTube** e a tela de consentimento do Google com os dois escopos;
3. um reel na fila sendo publicado e o vídeo aparecendo no canal.

Suba o vídeo como "não listado" no próprio canal ou no Drive, com link aberto, e cole o link no formulário.

## 4. Ordem recomendada

1. Criar o canal na contato@ e conectar no sistema. Sem canal conectado, não há demonstração.
2. Publicar o app no Google Auth Platform. Assim a conexão para de expirar a cada 7 dias.
3. Se o formulário pedir verificação dos escopos sensíveis (OAuth verification), envie pelo próprio Google Auth Platform > **Verificação**. Use a mesma descrição da seção 2 e o mesmo vídeo.
4. Preencher este formulário de auditoria.

O Google costuma responder por e-mail para a intimacoes@, em alguns dias ou semanas. Até lá, os vídeos sobem como privados. O card no sistema avisa, e dá para mudar cada um para público à mão no YouTube Studio.
