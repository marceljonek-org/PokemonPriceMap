# Cenová mapa Pokémon TCG

Denné sledovanie cien a dostupnosti **zapečatených** Pokémon TCG produktov v 28 českých
a slovenských eshopoch. Pokrýva **79 setov od klasiky z roku 1999 po Mega Evolution** — série
Base Set/Neo, XY, Sun & Moon, Sword & Shield, Scarlet & Violet a Mega Evolution —
naprieč 22 formátmi — od jedného boostera cez bundle, box a ETB až po Ultra Premium
Collection a zberateľské tiny.
Výsledok je statická stránka s obrázkami balení, históriou cien, denným rebríčkom
„čo dnes stojí za nákup“, cieľovými cenami, portfóliom a označením podhodnotených ponúk.

Beží zadarmo: GitHub Actions robí denný sken, Cloudflare Pages hostuje stránku.

---

## Rýchly štart

### 1. Založ repo a nahraj kód

Na GitHube si vytvor **verejné** repo (verejné = neobmedzené minúty v Actions).
Potom v priečinku s týmto projektom:

```bash
git init
git add .
git commit -m "Cenová mapa Pokémon TCG"
git branch -M main
git remote add origin https://github.com/<tvoje-meno>/<repo>.git
git push -u origin main
```

### 2. Povoľ zápis pre Actions

`Settings → Actions → General → Workflow permissions` → **Read and write permissions** → Save.
Bez toho nemôže denný beh commitnúť nové dáta.

### 3. Prvý sken

`Actions → Denný sken cien → Run workflow`. Trvá 1–3 minúty. Po dobehnutí
pribudnú v repe `data/history.csv`, `docs/latest.json` a obrázky v `docs/img/`.

### 4. Cloudflare Pages

1. [dash.cloudflare.com](https://dash.cloudflare.com) → **Workers & Pages** → **Create** → **Pages** → **Connect to Git**
2. Vyber toto repo.
3. Nastavenia buildu:
   - Framework preset: **None**
   - Build command: *nechaj prázdne*
   - Build output directory: **`docs`**
4. **Save and Deploy.**

Stránka pobeží na `https://<meno-projektu>.pages.dev` a Cloudflare ju nasadí znova
pri každom pushi — teda po každom dennom skene. Vlastnú doménu pridáš v
*Custom domains*, heslo na stránku cez *Cloudflare Access*.

> Alternatíva: ak nechceš Cloudflare, zapni GitHub Pages
> (`Settings → Pages → Source: GitHub Actions`) a v `.github/workflows/pages.yml`
> odkomentuj spúšťač `push`.

---

## Ako to beží

Workflow `daily.yml` sa spúšťa cronom `0 17 * * *` (17:00 UTC = **19:00 letný čas**)
a dá sa kedykoľvek spustiť ručne. Sedem krokov:

1. **Testy parserov** nad snapshotmi v `tests/fixtures/` — keď je rozbitá parsovacia
   logika, beh spadne ešte pred dotykom so živými webmi.
2. **Sken 28 eshopov** vrátane stránkovania, max 4 eshopy naraz. Kurz CZK/EUR z ECB.
3. **Klasifikácia** názvov na edíciu + formát; čo nie je sledovaný formát v edícii
   úrovne A/B/C, sa zahodí.
4. **Porovnanie s posledným behom** — zmeny cien, preklopenia dostupnosti, nové položky,
   značky `pod trhom` / `overiť`. Stiahnu sa chýbajúce obrázky.
5. **Vyhodnotenie** — rebríček „Kúpiť dnes“, kontrola cieľových cien, ocenenie
   portfólia a zápis dnešného bodu do histórie jeho hodnoty.
6. **Commit** dát a obrázkov → Cloudflare Pages nasadí stránku do minúty.
7. **Upozornenia** na Telegram, ak sú nastavené (splnený cieľ, veľký pokles, naskladnenie).

### Poistky proti tichému zlyhaniu

Pri 26 eshopoch je výpadok jedného normálna prevádzka, nie dôvod zahodiť celý beh.
Fatálne je až to, keď vypadne väčšia časť eshopov alebo keď objem dát spadne na zlomok.

| Situácia | Čo sa stane |
|---|---|
| Menej než 80 % **povinných** eshopov vrátilo dáta | beh skončí kódom 2, dáta sa **nezapíšu** |
| Celkovo menej než polovica zaradených ponúk oproti minulému behu | to isté |
| Žiadne zaradené položky | to isté |
| Jednotlivý eshop nevrátil nič | varovanie v logu + „nedostupný“ v pätičke stránky, beh pokračuje |
| Eshop vrátil menej než polovicu položiek než včera | varovanie, beh pokračuje |
| Cena mimo 30–300 % predchádzajúcej | zapíše sa, ale označí ako `skok ceny` |
| Stránka nemá dnešný dátum | hore sa zobrazí červený pruh |

Každá požiadavka sa opakuje až trikrát s narastajúcou pauzou — jednorazový 403
alebo timeout tak beh nezhodí.

Ak si istý, že prepad je v poriadku (eshop naozaj vypredal), spusti workflow ručne
so zaškrtnutým **force**.

---

## Sledované eshopy

28 eshopov na 11 platformách. Adaptér je parser pre danú platformu — ďalší eshop
na tej istej platforme je otázka troch riadkov v `config/shops.yaml`.

| Adaptér | Eshopy | Ako sa čítajú dáta |
|---|---|---|
| `shoptet` | Cardstore.cz, Fyft.cz, Nekonecno.sk, Pokemon4U.cz, TCG4You.cz, Card Empire SK, CC Planet, KúzelnéHry.sk | mikrodáta `data-micro-*` (schema.org) |
| `pgs` | PGS.sk, Smarty.cz, Smarty.sk | `data-gaItem` JSON + `.productList-item-price` |
| `woocommerce` | PokecTCG.cz, Pokélio.cz, GeekHall.cz | `li.product`, alebo `article.product_card` v šablónach z Oxygen Builderu |
| `upgates` | Zardo Cards, Gengar.cz | `article.card-item` |
| `pompo` | Pompo.cz, Pompo.sk | JSON v `data-tracking-view` |
| `veselydrak` | Veselý drak CZ, Veselý drak SK | `div.catalogue-item` |
| `shopify` | Cardyx.sk, 64ka.sk | verejné `/products.json`, pole `available` |
| `xzone` | Xzone.cz, Xzone.sk | `div.product-item`, stránkovanie naslepo cez `?page=N` |
| `alza` | Alza.cz, Alza.sk | `div.box.browsingitem` |
| `digihry` | Digihry.sk | mikrodáta `itemprop` |
| `opencart` | Dazzle.sk | `.product-grid .product`; stužka PREDOBJEDNÁVKA prebíja text o sklade |

Zvažované a zatiaľ nezaradené: **Charizard.sk** (PrestaShop) a **Cheapgame.cz** —
ich HTML sa nepodarilo spoľahlivo stiahnuť na overenie selektorov, takže by šlo
o neotestovaný kód. Dajú sa doplniť neskôr.

Gengar.cz je v zozname od začiatku (adaptér `upgates`); slovenská mutácia
`gengar.cz/sk` je ten istý sklad, len v eurách, preto ju nesledujeme zvlášť.

---

## Konfigurácia

Všetko podstatné je v `config/`, kód sa nemusí meniť.

### Portfólio

Zoznam toho, čo vlastníš. Stránka z neho počíta náklady, aktuálnu hodnotu, rozdiel
a hlavne to, **či si kúpil pod cenou** — porovnanie nákupnej ceny s najlacnejšou
ponukou skladom dnes.

Zapisovať sa dá dvoma spôsobmi.

**A) Priamo v aplikácii (odporúčané).** Záložka *Portfólio* → formulár; produkt sa
vyberá zo zoznamu, aby sa dal oceniť. Ukladá sa do Cloudflare KV cez ten istý Worker,
ktorý robí proxy. Nastavenie:

1. Cloudflare → **Storage & databases → KV → Create** namespace, napr. `cenova-mapa-portfolio`
2. Worker → *Settings* → **Bindings** → *Add* → **KV namespace**, premenná `PORTFOLIO`,
   vyber ten namespace
3. Worker → *Settings* → *Variables and Secrets* → pridaj secret **`PORTFOLIO_TOKEN`**
   (iné heslo než `PROXY_TOKEN` — toto sa zadáva v prehliadači)
4. GitHub → *Secrets and variables → Actions* → pridaj **`SCRAPE_PORTFOLIO_TOKEN`**
   s rovnakou hodnotou
5. Spusti sken. V záložke *Portfólio* zadaj heslo — uloží sa len v tvojom prehliadači
   a posiela sa výhradne na tvoj Worker.

**B) Súborom `config/portfolio.yaml`** — na hromadné zadanie alebo keď Worker nechceš:

```yaml
valuation: median        # median = medián ponúk skladom, min = najlacnejšia
holdings:
  - key: chaos-rising|etb
    qty: 2
    price: 62.50         # za jeden kus
    currency: EUR        # CZK sa prepočíta kurzom dňa
    bought: 2026-07-14
    shop: Alza.sk
```

Obidva zdroje sa sčítajú. Oceňuje sa cenou, za ktorú sa produkt **ponúka** — nie cenou,
za ktorú ho vieš predať; reálne speňaženie býva nižšie.

### Pridanie edície — `config/editions.yaml`

```yaml
  - id: nova-edicia
    name: Nová Edícia
    code: ME07
    tier: A            # A a B sa zobrazujú, C je len cenová kotva
    note: Prečo ju sledujeme
    patterns:
      - "nova\\s+edicia"
      - "\\bme\\s*0?7\\b"
```

Vzory sa hľadajú v názve **bez diakritiky a malými písmenami** — `nova`, nie `nová`.

**Pozor na poradie:** rozhoduje prvá zhoda, takže polovičné sety (SV8.5, ME02.5, SWSH4.5)
musia byť v súbore **nad** základnými (SV08, ME02, SWSH04). Inak by `SV8.5` spadlo pod `SV08`.

**Pozor na generické názvy.** Staré sety majú mená, ktoré sa objavujú aj v moderných
produktoch — SV10 Destined Rivals je celý o Team Rockete. Preto klasický Team Rocket
vyžaduje dobový znak (`1st edition`, rok) a `Evolutions` či `Generations` musia mať
za sebou názov formátu. Bez toho by monitor hádzal moderné produkty do roku 2000.
`tier` je nepovinný — edícia bez neho sa sleduje tiež, len nemá investičné zaradenie.

Keď sa v ponuke objaví formát v neznámej edícii (napr. „ME07 … Booster Bundle“),
zapíše sa do `data/unknown.csv`. To je signál, že vyšiel nový set — pozri sa tam
raz za čas.

### Pridanie eshopu — `config/shops.yaml`

Ak eshop beží na už podporovanej platforme (zoznam nižšie), stačí pridať záznam
s `adapter` a `urls`.
Nová platforma znamená nový parser v `src/adapters.py` + fixture a test.

### Vlastné obrázky — `config/images.yaml`

```yaml
images:
  "30th-celebration|etb": "https://.../etb.png"
```

Kľúč je `<edition_id>|<format_id>`. Prebije automaticky vybraný obrázok z eshopu.
Obrázok sa stiahne raz, zmenší na 400 px a uloží ako WebP do `docs/img/` — stránka
teda nikdy nehotlinkuje na cudzí server.

---

## Tlačidlo „Spustiť sken“

Na stránke vpravo hore je tlačidlo, ktoré spustí presne ten istý beh ako cron o 19:00.
Stránka nesmie poznať prístup na GitHub, tak dispatch robí Worker; chráni ho to isté
heslo ako portfólio (`PORTFOLIO_TOKEN`), takže tlačidlo funguje až po odomknutí
v záložke *Portfólio*.

Vo Workeri pribudnú tri premenné:

| Názov | Typ | Hodnota |
|---|---|---|
| `GITHUB_TOKEN` | Secret | token z GitHubu (nižšie) |
| `GITHUB_REPO` | Text | `meno/repo`, napr. `marceljonek-org/PokemonPriceMap` |
| `GITHUB_WORKFLOW` | Text | nepovinné, default `daily.yml` |

Token: GitHub → *Settings* (účet, nie repo) → *Developer settings* →
*Personal access tokens* → **Fine-grained tokens** → *Generate new token*.
Repository access: len toto repo. Permissions → Repository permissions →
**Actions: Read and write**. Ak repo patrí organizácii a fine-grained token neprejde
(GitHub vráti 403), použi klasický token so zaškrtnutými `repo` a `workflow`.

Po spustení stránka každých 20 sekúnd kontroluje `latest.json`; keď sa objaví nový
sken, obnoví sa sama. Po desiatich minútach čakanie vzdá a odkáže ťa na Actions.

---

## Investičné metriky

| Značka | Čo znamená |
|---|---|
| `pod trhom X %` | 5–30 % pod mediánom ponúk skladom |
| `overiť` | viac než 30 % pod mediánom — skoro vždy chyba eshopu |
| `najnižšie doteraz` | najlacnejšia ponuka skladom je na úrovni historického minima (tolerancia 1 %) |
| `X % pod uvádzacou` | aspoň 3 % pod orientačnou uvádzacou cenou formátu |
| `po ukončení tlače` | od vydania ubehlo viac než 550 dní (~18 mesiacov) |
| `za balíček` | cena delená počtom boosterov — jediné číslo, ktorým sa porovná bundle proti boxu |

Dátumy vydania a uvádzacie ceny sú v `config/editions.yaml` vrátane zdrojov.
Uvádzacia cena **nie je oficiálne MSRP** — to sa pre CZ/SK nezverejňuje; sú to ceny,
za ktoré sa čerstvo vydaný set v týchto obchodoch bežne predáva.

---

## Kúpiť dnes — denný rebríček

Záložka *Kúpiť dnes* zoradí dnešné ponuky podľa toho, ako výhodne vyzerajú. Poradie
počíta sken, nie prehliadač, takže je pre rovnaké dáta vždy rovnaké a dá sa spätne
overiť z `latest.json`.

| Zložka | Body | Prečo |
|---|---|---|
| pod dnešným mediánom ponúk skladom | 0–40 | najpriamejšia miera „lacnejšie než trh“; zľava sa počíta len do 30 %, ďalej to už býva chyba |
| pod uvádzacou cenou formátu | 0–25 | set pod cenou z vydania sa nekupuje často |
| na historickom minime (tolerancia 3 %) | 20 | nižšie to zatiaľ nikdy nebolo |
| úroveň edície A/B/C | 2–10 | slabý set lacno je stále slabý set |
| po ukončení tlače | 5 | ponuka sa už nedopĺňa |

Ponuky označené `overiť` alebo `skok ceny` sa do rebríčka nedostanú a z jedného
eshopu sa berú najviac tri položky, nech nezaplní celý zoznam.

Žiadna predpoveď budúcnosti sa tu nepočíta — sú to len dnes merateľné čísla.
Ceny zberateľských kariet sú špekulatívne; toto nie je investičné poradenstvo.

---

## Cieľové ceny

V detaile produktu (klik na kartu) je pole **Cieľová cena**. Zapíše sa do toho istého
Cloudflare KV ako portfólio, len pod kľúč `watchlist` — netreba zakladať druhý namespace.
Funguje po odomknutí heslom portfólia.

Pri každom skene sa cieľ porovná s najlacnejšou ponukou **skladom**:

- splnené ciele sa vypíšu navrchu záložky *Kúpiť dnes* a pošlú sa na Telegram
- nesplnené sú v tabuľke *Čakajú na cenu* aj s tým, koľko percent chýba

Hromadne sa dajú zadať aj v `config/portfolio.yaml` pod kľúčom `targets:`; oba
zdroje sa sčítajú.

---

## Graf hodnoty portfólia

Každý sken zapíše jeden riadok do `data/portfolio-history.csv` (dátum, počet položiek,
náklady, hodnota, rozdiel). Záložka *Portfólio* z toho kreslí graf: plná čiara je
hodnota, čiarkovaná to, čo si za to zaplatil. Opakovaný sken v ten istý deň riadok
prepíše, nie pridá — inak by v grafe boli tri body na jeden deň.

Prvý bod pribudne pri najbližšom skene po pridaní prvého nákupu, druhý na druhý deň;
dovtedy stránka napíše, že graf ešte nie je z čoho nakresliť.

---

## Upozornenia na Telegram

Nepovinné. Bez nastavenia sken beží presne ako doteraz, len na konci vypíše, koľko
upozornení by poslal.

Posielajú sa tri veci:

| Značka | Kedy |
|---|---|
| 🎯 | splnená cieľová cena |
| 📉 | pokles o 10 % a viac oproti minulému skenu (nad 50 % sa ignoruje — to nebýva zľava, ale iný produkt pod tou istou adresou) |
| 📦 | edícia úrovne A alebo B je opäť skladom |

To isté upozornenie sa neopakuje **7 dní**; čo už išlo, je v `data/alerts-sent.csv`.

### Nastavenie

1. V Telegrame napíš **@BotFather** → `/newbot` → zadaj meno a používateľské meno bota.
   Odpovie ti tokenom v tvare `1234567890:AA...`.
2. Napíš svojmu novému botovi ľubovoľnú správu (bez toho ti nemá kam písať).
3. Otvor v prehliadači `https://api.telegram.org/bot<TOKEN>/getUpdates` a nájdi
   `"chat":{"id":123456789` — to číslo je tvoje `chat_id`.
4. GitHub → *Settings → Secrets and variables → Actions* → *New repository secret*:
   - `TELEGRAM_BOT_TOKEN` = token z kroku 1
   - `TELEGRAM_CHAT_ID` = číslo z kroku 3

Ak Telegram neodpovie, sken to len vypíše do logu a pokračuje — upozornenie nikdy
nesmie zhodiť zber cien.

---

## Proxy pre blokované eshopy

Šesť eshopov (Zardo, Smarty CZ/SK, Alza CZ/SK, PokecTCG) vracia z IP adries
GitHub Actions `403 Forbidden`. Nie je to namierené proti nám — je to plošné
pravidlo proti serverovým IP. Riešenie: malý Cloudflare Worker, ktorý stránku
stiahne zo svojej siete a vráti ju skenu.

**Worker nie je podmienka behu.** Bez neho sken funguje presne ako doteraz,
len tých šesť eshopov zostane v pätičke ako „nedostupný“.

### Nasadenie

1. `tools/cloudflare-worker.js` je hotový kód Workera.
2. V Cloudflare: **Compute (Workers & Pages) → Create → Workers → Create Worker**,
   pomenuj ho napr. `cenova-mapa-proxy`, **Deploy**, potom **Edit code**,
   obsah nahraď súborom `tools/cloudflare-worker.js` a znova **Deploy**.
3. **Settings → Variables and Secrets** pridaj:

   | Názov | Typ | Hodnota |
   |---|---|---|
   | `PROXY_TOKEN` | Secret | dlhé náhodné heslo, ktoré si vymyslíš |
   | `ALLOWED_HOSTS` | Text | `pokectcg.cz,alza.cz,alza.sk,smarty.cz,smarty.sk,zardo.cards` |

4. Na GitHube: **Settings → Secrets and variables → Actions → New repository secret**

   | Názov | Hodnota |
   |---|---|
   | `SCRAPE_PROXY_URL` | `https://cenova-mapa-proxy.<tvoj-podúčet>.workers.dev` |
   | `SCRAPE_PROXY_TOKEN` | rovnaké heslo ako `PROXY_TOKEN` |

5. Spusti workflow ručne. V logu sa objaví riadok
   `Označené na proxy (6): cez proxy`.

### Poistky vo Workeri

Bez nich by to bola otvorená proxy pre kohokoľvek na internete:

- bez správneho tokenu vráti `401`
- pustí len domény z `ALLOWED_HOSTS`
- prijíma len `GET` a len `http`/`https`
- nič neukladá ani necachuje

Eshop, ktorý cez proxy začne fungovať, si necháva `optional: true`, kým sa
neukáže, že to drží. Ak Worker nepomôže, stačí v `config/shops.yaml` zmazať
riadky `proxy: true` — alebo nechať, nič to nekazí.

---

## Lokálne spustenie

```bash
pip install -r requirements.txt
make test          # 156 testov nad snapshotmi, bez siete
make demo          # postaví docs/latest.json zo snapshotov
python -m http.server -d docs 8000   # náhľad na http://localhost:8000
make scan          # ostrý sken (potrebuje sieť)
```

Užitočné prepínače:

```bash
python src/scrape.py --only pgs-sk,cardyx-sk   # len vybrané eshopy
python src/scrape.py --dry-run                 # nezapisovať históriu
python src/scrape.py --snapshots snapshots/    # uložiť stiahnuté stránky
```

### Obnova fixtures po zmene eshopu

Keď eshop prekope šablónu, test spadne. Vtedy:

1. `Actions → Denný sken cien → Run workflow` so zaškrtnutým **snapshots**
2. stiahni artefakt `snapshots`, nájdi stránku daného eshopu
3. `gzip -9 -c stranka.html > tests/fixtures/<nazov>.html.gz`
4. oprav selektor v `src/adapters.py`, spusti `make test`

---

## Štruktúra

```
.github/workflows/daily.yml   denný sken a commit
.github/workflows/pages.yml   záloha pre GitHub Pages
config/                       eshopy, edície, ručné obrázky
src/scrape.py                 orchestrácia, poistky, agregácia
src/adapters.py               11 parserov podľa platformy eshopu
src/classify.py               názov -> edícia + formát + počet balíčkov
src/images.py                 sťahovanie a konverzia obrázkov
docs/index.html               celá stránka, jeden súbor bez závislostí
docs/latest.json              dáta, ktoré stránka číta
data/history.csv              každý sken, každá ponuka
data/unknown.csv              nerozpoznané názvy na kontrolu
tests/                        170 testov nad gzip snapshotmi
data/portfolio-history.csv    denná hodnota portfólia (graf)
data/alerts-sent.csv          čo už išlo na Telegram (proti opakovaniu)
tools/demo_from_fixtures.py   náhľad bez siete
```

`data/history.csv` narastie asi o 4 MB za rok — pre git bez problémov.

---

## Známe riziká

- **Osem eshopov je označených `optional: true`** — Alza CZ/SK, Smarty CZ/SK,
  Zardo Cards, PokecTCG.cz a Veselý drak CZ/SK. Z IP adries GitHub Actions vracajú
  403 alebo padajú do timeoutu. Nerátajú sa do zdravotnej kontroly behu a prejavia sa
  ako „nedostupný“ v pätičke stránky. Ak niektorý z nich začne fungovať, stačí mu
  riadok `optional: true` zmazať.
- **Zimný čas.** Cron je v UTC, takže od konca októbra beží sken o 18:00. Ak chceš
  držať 19:00, prepni v `daily.yml` na `0 18 * * *`.
- **GitHub vypína cron** v repozitároch bez aktivity 60 dní. Denný commit dát to pokrýva.
- **Ceny zberateľských kariet sú špekulatívne.** Toto je nástroj na porovnávanie ponúk,
  nie investičné poradenstvo.
