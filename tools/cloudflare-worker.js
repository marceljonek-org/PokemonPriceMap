/**
 * Cenová mapa — Worker s dvoma úlohami.
 *
 *  1. PROXY  — sťahuje stránky eshopov, ktoré blokujú IP adresy GitHub Actions.
 *              ?t=<PROXY_TOKEN>&url=<adresa>
 *
 *  2. PORTFÓLIO — úložisko toho, čo mám kúpené, aby sa dalo zapisovať priamo
 *              zo stránky a nemuselo sa editovať na GitHube.
 *              GET  ?portfolio=1&t=<PORTFOLIO_TOKEN>
 *              POST ?portfolio=1&t=<PORTFOLIO_TOKEN>   telo = JSON pole položiek
 *
 *  3. SKEN    — tlačidlo na stránke, ktoré spustí ten istý beh ako cron o 19:00.
 *              POST ?scan=1&t=<PORTFOLIO_TOKEN>
 *              Prístup na GitHub drží Worker, aby token nebol vo verejnej stránke.
 *
 * Tokeny sú dva zámerne. PORTFOLIO_TOKEN si zadáš v prehliadači a uloží sa ti
 * lokálne; keby to bol ten istý token ako pri proxy, mal by ho v ruke každý,
 * kto otvorí stránku, a mohol by cez Worker sťahovať čokoľvek.
 *
 * Premenné (Settings → Variables and Secrets):
 *   PROXY_TOKEN      secret  — heslo pre sťahovanie stránok
 *   PORTFOLIO_TOKEN  secret  — heslo pre portfólio
 *   ALLOWED_HOSTS    text    — domény, ktoré smie proxy sťahovať
 *   GITHUB_TOKEN     secret  — fine-grained PAT s právom Actions: Read and write
 *   GITHUB_REPO      text    — "meno/repo", napr. "marceljonek-org/PokemonPriceMap"
 *   GITHUB_WORKFLOW  text    — nepovinné, názov súboru workflowu (default daily.yml)
 * Väzba (Settings → Bindings → KV namespace):
 *   PORTFOLIO        → KV namespace, napr. "cenova-mapa-portfolio"
 *                      (kód znesie aj zápis "Portfolio" alebo "portfolio")
 */

const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
           "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36";

const KV_KEY = "holdings";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "content-type",
};

const json = (data, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8",
               "cache-control": "no-store", ...CORS },
  });

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS });
    }

    const params = new URL(request.url).searchParams;
    const token = params.get("t") || request.headers.get("x-proxy-token") || "";

    // ---------------------------------------------------------- portfólio
    if (params.get("portfolio")) {
      if (!env.PORTFOLIO_TOKEN || token !== env.PORTFOLIO_TOKEN) {
        return json({ error: "unauthorized" }, 401);
      }
      // Dashboard nedovolí väzbu premenovať, tak berieme aj iné zápisy mena.
      const kv = env.PORTFOLIO || env.Portfolio || env.portfolio;
      if (!kv) {
        return json({ error: "chýba väzba na KV namespace PORTFOLIO" }, 500);
      }

      if (request.method === "GET") {
        const stored = await kv.get(KV_KEY);
        return json({ holdings: stored ? JSON.parse(stored) : [] });
      }

      if (request.method === "POST") {
        let body;
        try {
          body = await request.json();
        } catch {
          return json({ error: "telo nie je JSON" }, 400);
        }
        const holdings = Array.isArray(body) ? body : body.holdings;
        if (!Array.isArray(holdings)) {
          return json({ error: "očakávam pole položiek" }, 400);
        }
        if (holdings.length > 500) {
          return json({ error: "priveľa položiek" }, 413);
        }
        // Ukladáme len polia, ktoré poznáme — nech sa do úložiska nedostane
        // čokoľvek, čo pošle prehliadač.
        const clean = holdings.map((h) => ({
          id: String(h.id || "").slice(0, 40),
          key: String(h.key || "").slice(0, 120),
          title: String(h.title || "").slice(0, 160),
          qty: Number(h.qty) || 0,
          price: Number(h.price) || 0,
          currency: String(h.currency || "EUR").slice(0, 3).toUpperCase(),
          bought: String(h.bought || "").slice(0, 10),
          shop: String(h.shop || "").slice(0, 80),
          note: String(h.note || "").slice(0, 200),
        }));
        await kv.put(KV_KEY, JSON.stringify(clean));
        return json({ ok: true, count: clean.length });
      }

      return json({ error: "only GET/POST" }, 405);
    }

    // ---------------------------------------------------------- ručný sken
    if (params.get("scan")) {
      if (!env.PORTFOLIO_TOKEN || token !== env.PORTFOLIO_TOKEN) {
        return json({ error: "unauthorized" }, 401);
      }
      if (request.method !== "POST") return json({ error: "only POST" }, 405);
      if (!env.GITHUB_TOKEN || !env.GITHUB_REPO) {
        return json({ error: "chýba GITHUB_TOKEN alebo GITHUB_REPO" }, 500);
      }
      const workflow = env.GITHUB_WORKFLOW || "daily.yml";
      const url = `https://api.github.com/repos/${env.GITHUB_REPO}` +
                  `/actions/workflows/${workflow}/dispatches`;
      let res;
      try {
        res = await fetch(url, {
          method: "POST",
          headers: {
            "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "cenova-mapa-worker",
            "content-type": "application/json",
          },
          body: JSON.stringify({ ref: "main" }),
        });
      } catch (err) {
        return json({ error: "GitHub nedostupný: " + err }, 502);
      }
      if (res.status === 204) return json({ ok: true });
      const detail = await res.text();
      return json({ error: `GitHub odpovedal ${res.status}`, detail: detail.slice(0, 300) },
                   res.status === 401 || res.status === 403 ? 403 : 502);
    }

    // ---------------------------------------------------------- proxy
    if (request.method !== "GET") {
      return new Response("only GET", { status: 405 });
    }
    if (!env.PROXY_TOKEN || token !== env.PROXY_TOKEN) {
      return new Response("unauthorized", { status: 401 });
    }

    const target = params.get("url");
    if (!target) return new Response("missing url", { status: 400 });

    let parsed;
    try {
      parsed = new URL(target);
    } catch {
      return new Response("bad url", { status: 400 });
    }
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
      return new Response("bad scheme", { status: 400 });
    }

    const allowed = (env.ALLOWED_HOSTS || "")
      .split(",").map((h) => h.trim().toLowerCase()).filter(Boolean);
    const host = parsed.hostname.toLowerCase();
    if (!allowed.some((h) => host === h || host.endsWith("." + h))) {
      return new Response("host not allowed", { status: 403 });
    }

    let upstream;
    try {
      upstream = await fetch(parsed.toString(), {
        redirect: "follow",
        headers: {
          "User-Agent": UA,
          "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9," +
                    "image/avif,image/webp,*/*;q=0.8",
          "Accept-Language": "sk-SK,sk;q=0.9,cs;q=0.8,en;q=0.7",
          "Upgrade-Insecure-Requests": "1",
          "Sec-Fetch-Dest": "document",
          "Sec-Fetch-Mode": "navigate",
          "Sec-Fetch-Site": "none",
          "Referer": parsed.origin + "/",
        },
      });
    } catch (err) {
      return new Response("upstream error: " + err, { status: 502 });
    }

    // Status necháme tak, ako prišiel — nech scraper vidí pravdu (403 zostane 403).
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") ||
                        "text/html; charset=utf-8",
        "x-proxied-from": host,
        "cache-control": "no-store",
      },
    });
  },
};
