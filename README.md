# Cenová mapa Pokémon TCG

Denné sledovanie cien a dostupnosti **sealed** Pokémon TCG produktov v 26 českých
a slovenských eshopoch. Sleduje štyri formáty — Booster, Booster Bundle, Booster Box
a Elite Trainer Box — a len edície s investičným potenciálom (úroveň A a B).
Výsledok je statická stránka s obrázkami balení, históriou cien a označením
podhodnotených ponúk.

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
a dá sa kedykoľvek spustiť ručne. Päť krokov:

1. **Testy parserov** nad snapshotmi v `tests/fixtures/` — keď je rozbitá parsovacia
   logika, beh spadne ešte pred dotykom so živými webmi.
2. **Sken 26 eshopov** vrátane stránkovania, max 4 eshopy naraz. Kurz CZK/EUR z ECB.
3. **Klasifikácia** názvov na edíciu + formát; čo nie je sledovaný formát v edícii
   úrovne A/B/C, sa zahodí.
4. **Porovnanie s posledným behom** — zmeny cien, preklopenia dostupnosti, nové položky,
   značky `pod trhom` / `overiť`. Stiahnu sa chýbajúce obrázky.
5. **Commit** dát a obrázkov → Cloudflare Pages nasadí stránku do minúty.

### Poistky proti tichému zlyhaniu

| Situácia | Čo sa stane |
|---|---|
| Eshop vráti menej než 50 % položiek oproti minulému behu | beh skončí kódom 2, dáta sa **nezapíšu** |
| Povinný eshop nevráti nič | to isté |
| Eshop označený `optional: true` zlyhá | beh pokračuje, eshop sa vypíše v pätičke stránky |
| Cena mimo 30–300 % predchádzajúcej | zapíše sa, ale označí ako `skok ceny` |
| Stránka nemá dnešný dátum | hore sa zobrazí červený pruh |

Ak si istý, že prepad je v poriadku (eshop naozaj vypredal), spusti workflow ručne
so zaškrtnutým **force**.

---

## Sledované eshopy

26 eshopov na 10 platformách. Adaptér je parser pre danú platformu — ďalší eshop
na tej istej platforme je otázka troch riadkov v `config/shops.yaml`.

| Adaptér | Eshopy | Ako sa čítajú dáta |
|---|---|---|
| `shoptet` | Cardstore.cz, Fyft.cz, Nekonecno.sk, Pokemon4U.cz, TCG4You.cz, Card Empire SK, CC Planet | mikrodáta `data-micro-*` (schema.org) |
| `pgs` | PGS.sk, Smarty.cz, Smarty.sk | `data-gaItem` JSON + `.productList-item-price` |
| `woocommerce` | PokecTCG.cz, Pokélio.cz, GeekHall.cz | `li.product`, stav skladu z tried `instock`/`outofstock` |
| `upgates` | Zardo Cards, Gengar.cz | `article.card-item` |
| `pompo` | Pompo.cz, Pompo.sk | JSON v `data-tracking-view` |
| `veselydrak` | Veselý drak CZ, Veselý drak SK | `div.catalogue-item` |
| `shopify` | Cardyx.sk, 64ka.sk | verejné `/products.json`, pole `available` |
| `xzone` | Xzone.cz, Xzone.sk | `div.product-item`, stránkovanie naslepo cez `?page=N` |
| `alza` | Alza.cz, Alza.sk | `div.box.browsingitem` |
| `digihry` | Digihry.sk | mikrodáta `itemprop` |

Zvažované a zatiaľ nezaradené: **Charizard.sk** (PrestaShop) a **Cheapgame.cz** —
ich HTML sa nepodarilo spoľahlivo stiahnuť na overenie selektorov, takže by šlo
o neotestovaný kód. Dajú sa doplniť neskôr.

Gengar.cz je v zozname od začiatku (adaptér `upgates`); slovenská mutácia
`gengar.cz/sk` je ten istý sklad, len v eurách, preto ju nesledujeme zvlášť.

---

## Konfigurácia

Všetko podstatné je v `config/`, kód sa nemusí meniť.

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

## Lokálne spustenie

```bash
pip install -r requirements.txt
make test          # 95 testov nad snapshotmi, bez siete
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
src/adapters.py               10 parserov podľa platformy eshopu
src/classify.py               názov -> edícia + formát + počet balíčkov
src/images.py                 sťahovanie a konverzia obrázkov
docs/index.html               celá stránka, jeden súbor bez závislostí
docs/latest.json              dáta, ktoré stránka číta
data/history.csv              každý sken, každá ponuka
data/unknown.csv              nerozpoznané názvy na kontrolu
tests/                        95 testov nad gzip snapshotmi
tools/demo_from_fixtures.py   náhľad bez siete
```

`data/history.csv` narastie asi o 4 MB za rok — pre git bez problémov.

---

## Známe riziká

- **Alza (CZ/SK) a Smarty (CZ/SK)** sú označené `optional: true`. Môžu z IP adries
  GitHub Actions vracať 403; prejaví sa to ako „nedostupný“ v pätičke stránky,
  nie ako spadnutý beh.
- **Zimný čas.** Cron je v UTC, takže od konca októbra beží sken o 18:00. Ak chceš
  držať 19:00, prepni v `daily.yml` na `0 18 * * *`.
- **GitHub vypína cron** v repozitároch bez aktivity 60 dní. Denný commit dát to pokrýva.
- **Ceny zberateľských kariet sú špekulatívne.** Toto je nástroj na porovnávanie ponúk,
  nie investičné poradenstvo.
