// functions/_middleware.js
//
// Protege TODO o site do Hub Produtec (todas as páginas e arquivos) atrás de
// login real do Google, sem usar o Cloudflare Access/Zero Trust (que exige
// cartão de crédito) — isso roda como parte do próprio Cloudflare Pages
// (gratuito, sem cartão).
//
// Como funciona:
//  1. Quem tenta acessar qualquer página sem estar logado vê uma tela de
//     login com o botão oficial "Sign in with Google".
//  2. Depois do login, o Google manda uma credencial assinada digitalmente
//     (um ID token). Este código verifica essa credencial DIRETO com o
//     Google (chamando o endpoint oficial deles) — não dá pra falsificar
//     digitando um e-mail qualquer.
//  3. Se o e-mail verificado terminar em um dos domínios permitidos, um
//     cookie assinado (HMAC) é criado, válido por alguns dias, liberando o
//     acesso ao resto do site.
//
// Variáveis de ambiente necessárias (configuradas no painel do Cloudflare
// Pages, em Settings -> Environment variables — nunca commitadas no git):
//   GOOGLE_CLIENT_ID  -> o Client ID criado no Google Cloud Console
//   SESSION_SECRET    -> uma string aleatória longa, só pra assinar os
//                        cookies (trocar esse valor desloga todo mundo)
//
// Domínios de e-mail permitidos: edite a lista ALLOWED_DOMAINS abaixo.

const ALLOWED_DOMAINS = ["chatbotmaker.io", "suri.ai"];

const COOKIE_NAME = "hub_auth";
const SESSION_DURATION_SECONDS = 60 * 60 * 24 * 7; // 7 dias

// ---------------------------------------------------------------------------
// Helpers de codificação (base64url, sem dependências externas)
// ---------------------------------------------------------------------------

function bytesToBase64Url(bytes) {
  let binary = "";
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function base64UrlToBytes(b64url) {
  const b64 = b64url.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((b64url.length + 3) % 4);
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function textToBase64Url(text) {
  return bytesToBase64Url(new TextEncoder().encode(text));
}

function base64UrlToText(b64url) {
  return new TextDecoder().decode(base64UrlToBytes(b64url));
}

// ---------------------------------------------------------------------------
// Assinatura HMAC-SHA256 do cookie de sessão (Web Crypto, disponível no
// runtime do Cloudflare Pages Functions)
// ---------------------------------------------------------------------------

async function getHmacKey(secret) {
  return crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"]
  );
}

async function signPayload(payloadB64Url, secret) {
  const key = await getHmacKey(secret);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payloadB64Url));
  return bytesToBase64Url(new Uint8Array(sig));
}

async function makeSessionCookieValue(email, secret) {
  const payload = JSON.stringify({
    email,
    exp: Math.floor(Date.now() / 1000) + SESSION_DURATION_SECONDS,
  });
  const payloadB64Url = textToBase64Url(payload);
  const sig = await signPayload(payloadB64Url, secret);
  return `${payloadB64Url}.${sig}`;
}

async function verifySessionCookieValue(cookieValue, secret) {
  if (!cookieValue || !cookieValue.includes(".")) return null;
  const [payloadB64Url, sig] = cookieValue.split(".");
  if (!payloadB64Url || !sig) return null;
  const expectedSig = await signPayload(payloadB64Url, secret);
  if (expectedSig !== sig) return null; // assinatura não bate -> forjado/adulterado
  let payload;
  try {
    payload = JSON.parse(base64UrlToText(payloadB64Url));
  } catch {
    return null;
  }
  if (!payload.email || !payload.exp) return null;
  if (Math.floor(Date.now() / 1000) > payload.exp) return null; // expirado
  if (!isAllowedEmail(payload.email)) return null; // domínio mudou de ideia
  return payload;
}

// ---------------------------------------------------------------------------
// Cookies (parse simples do header Cookie)
// ---------------------------------------------------------------------------

function getCookie(request, name) {
  const header = request.headers.get("Cookie") || "";
  for (const part of header.split(";")) {
    const [k, ...rest] = part.trim().split("=");
    if (k === name) return rest.join("=");
  }
  return null;
}

function buildSetCookieHeader(name, value, maxAgeSeconds) {
  return `${name}=${value}; Path=/; Max-Age=${maxAgeSeconds}; HttpOnly; Secure; SameSite=Lax`;
}

function buildClearCookieHeader(name) {
  return `${name}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax`;
}

// ---------------------------------------------------------------------------
// Regra de domínio
// ---------------------------------------------------------------------------

function isAllowedEmail(email) {
  if (!email || typeof email !== "string") return false;
  const lower = email.toLowerCase();
  return ALLOWED_DOMAINS.some((domain) => lower.endsWith("@" + domain.toLowerCase()));
}

// ---------------------------------------------------------------------------
// Verificação do ID token do Google direto com o Google (sem precisar
// implementar verificação de assinatura JWT à mão)
// ---------------------------------------------------------------------------

async function verifyGoogleIdToken(idToken, expectedClientId) {
  const resp = await fetch(
    "https://oauth2.googleapis.com/tokeninfo?id_token=" + encodeURIComponent(idToken)
  );
  if (!resp.ok) return { ok: false, reason: "token_invalid" };
  const data = await resp.json();
  if (data.aud !== expectedClientId) return { ok: false, reason: "aud_mismatch" };
  if (data.email_verified !== "true" && data.email_verified !== true) {
    return { ok: false, reason: "email_not_verified" };
  }
  if (!isAllowedEmail(data.email)) return { ok: false, reason: "domain_not_allowed", email: data.email };
  return { ok: true, email: data.email };
}

// ---------------------------------------------------------------------------
// Páginas HTML (login / acesso negado)
// ---------------------------------------------------------------------------

function renderLoginPage({ origin, clientId, redirectPath, error }) {
  const loginUri = origin + "/auth/callback" + (redirectPath ? "?redirect=" + encodeURIComponent(redirectPath) : "");
  const errorBlock = error
    ? `<p class="error">${escapeHtml(error)}</p>`
    : "";
  return `<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hub Produtec — Login</title>
<style>
  body {
    margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #141936; color: #f7f8ff;
  }
  .box {
    background: #1e2550; border: 1px solid rgba(255,255,255,0.12); border-radius: 16px;
    padding: 40px 36px; max-width: 380px; width: 100%; text-align: center;
  }
  h1 { font-size: 1.4rem; margin: 0 0 8px; }
  p.sub { color: #aeb4d6; font-size: 0.95rem; margin: 0 0 24px; }
  p.error {
    color: #ff8fb1; font-size: 0.88rem; background: rgba(255, 143, 177, 0.1);
    border: 1px solid rgba(255, 143, 177, 0.3); border-radius: 8px; padding: 10px 14px;
    margin: 0 0 20px;
  }
  .g_id_signin { display: flex; justify-content: center; }
</style>
</head>
<body>
  <div class="box">
    <h1>Hub Produtec</h1>
    <p class="sub">Entre com sua conta Google da empresa pra continuar.</p>
    ${errorBlock}
    <div id="g_id_onload"
         data-client_id="${escapeHtml(clientId)}"
         data-login_uri="${escapeHtml(loginUri)}"
         data-auto_prompt="false">
    </div>
    <div class="g_id_signin"
         data-type="standard"
         data-shape="pill"
         data-theme="filled_blue"
         data-text="signin_with"
         data-size="large">
    </div>
  </div>
  <script src="https://accounts.google.com/gsi/client" async defer></script>
</body>
</html>`;
}

function renderDeniedPage(email) {
  return `<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hub Produtec — Acesso negado</title>
<style>
  body {
    margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #141936; color: #f7f8ff; text-align: center;
  }
  .box { max-width: 420px; padding: 20px; }
  h1 { font-size: 1.4rem; }
  p { color: #aeb4d6; }
  a { color: #4a54ff; }
</style>
</head>
<body>
  <div class="box">
    <h1>Acesso negado</h1>
    <p>${email ? "A conta <b>" + escapeHtml(email) + "</b> não tem acesso a este hub." : "Não foi possível confirmar sua conta Google."}</p>
    <p>Se você acha que deveria ter acesso, fale com quem administra o Hub Produtec.</p>
    <p><a href="/">Tentar de novo</a></p>
  </div>
</body>
</html>`;
}

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ---------------------------------------------------------------------------
// Handler principal
// ---------------------------------------------------------------------------

export async function onRequest(context) {
  const { request, env, next } = context;
  const url = new URL(request.url);

  if (!env.GOOGLE_CLIENT_ID || !env.SESSION_SECRET) {
    return new Response(
      "Configuração incompleta: defina GOOGLE_CLIENT_ID e SESSION_SECRET nas variáveis de ambiente do projeto Cloudflare Pages.",
      { status: 500 }
    );
  }

  // Callback de login: recebe a credencial do Google via POST de formulário
  if (url.pathname === "/auth/callback" && request.method === "POST") {
    const form = await request.formData();
    const idToken = form.get("credential");
    if (!idToken) {
      return new Response(renderLoginPage({
        origin: url.origin, clientId: env.GOOGLE_CLIENT_ID, redirectPath: null,
        error: "Não recebi a credencial do Google. Tente novamente.",
      }), { status: 400, headers: { "Content-Type": "text/html; charset=utf-8" } });
    }

    const result = await verifyGoogleIdToken(idToken, env.GOOGLE_CLIENT_ID);
    if (!result.ok) {
      return new Response(renderDeniedPage(result.email), {
        status: 403,
        headers: { "Content-Type": "text/html; charset=utf-8" },
      });
    }

    const cookieValue = await makeSessionCookieValue(result.email, env.SESSION_SECRET);
    const redirectTo = url.searchParams.get("redirect") || "/";
    return new Response(null, {
      status: 302,
      headers: {
        Location: redirectTo,
        "Set-Cookie": buildSetCookieHeader(COOKIE_NAME, cookieValue, SESSION_DURATION_SECONDS),
      },
    });
  }

  // Logout manual (opcional, útil pra testar com outra conta)
  if (url.pathname === "/logout") {
    return new Response(null, {
      status: 302,
      headers: {
        Location: "/",
        "Set-Cookie": buildClearCookieHeader(COOKIE_NAME),
      },
    });
  }

  // Qualquer outra rota: exige sessão válida
  const cookieValue = getCookie(request, COOKIE_NAME);
  const session = await verifySessionCookieValue(cookieValue, env.SESSION_SECRET);
  if (session) {
    return next(); // autenticado -> segue pro conteúdo estático normal do Pages
  }

  // Não autenticado -> mostra tela de login (preservando a página que a
  // pessoa tentou abrir, pra voltar pra ela depois do login)
  const redirectPath = url.pathname + url.search;
  return new Response(
    renderLoginPage({ origin: url.origin, clientId: env.GOOGLE_CLIENT_ID, redirectPath }),
    { status: 200, headers: { "Content-Type": "text/html; charset=utf-8" } }
  );
}
