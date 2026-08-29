/**
 * Cenová mapa — proxy pre eshopy, ktoré blokujú IP adresy GitHub Actions.
 *
 * Nasadí sa ako Cloudflare Worker. Sken mu pošle URL, Worker ju stiahne
 * zo svojej siete a vráti obsah. Nič nemení, nič neukladá.
 *
 * Tri poistky, aby z toho nebola otvorená proxy pre kohokoľvek na internete:
 *   1. PROXY_TOKEN  — bez správneho tokenu Worker odpovie 401
 *   2. ALLOWED_HOSTS — pustí len domény, ktoré sú v zozname
 *   3. len GET, len http/https
 *
 * Premenné sa nastavujú v Cloudflare: Worker → Settings → Variables.
 */

const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
           "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36";

export default {
  async fetch(request, env) {
    if (request.method !== "GET") {
      return new Response("only GET", { status: 405 });
    }

    const params = new URL(request.url).searchParams;
    const token = params.get("t") || request.headers.get("x-proxy-token") || "";
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
    const ok = allowed.some((h) => host === h || host.endsWith("." + h));
    if (!ok) return new Response("host not allowed", { status: 403 });

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

    // Telo posielame ako je; status necháme, nech scraper vidí pravdu (403 zostane 403).
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
