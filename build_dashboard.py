import csv
import json
from datetime import datetime
from pathlib import Path

DATA_FILE = Path('data/history.csv')
SITE_DIR = Path('site')
SITE_DIR.mkdir(exist_ok=True)

if not DATA_FILE.exists():
    raise SystemExit('data/history.csv introuvable')

with DATA_FILE.open('r', encoding='utf-8', newline='') as f:
    rows = list(csv.DictReader(f))

if not rows:
    raise SystemExit('Historique vide')

for row in rows:
    for key in ('buy_sol', 'sell_sol', 'sol_eur', 'buy_eur', 'sell_eur'):
        row[key] = float(row[key])

# Regroupement par relevé horodaté
snapshots = {}
for row in rows:
    snapshots.setdefault(row['timestamp'], []).append(row)

series = []
for timestamp, group in sorted(snapshots.items()):
    if len(group) != 3:
        continue
    total_buy = sum(r['buy_sol'] for r in group)
    total_sell = sum(r['sell_sol'] for r in group)
    sol_eur = group[0]['sol_eur']
    spread_pct = ((total_sell - total_buy) / total_buy * 100) if total_buy else None
    series.append({
        'timestamp': timestamp,
        'total_buy_sol': total_buy,
        'total_sell_sol': total_sell,
        'total_buy_eur': total_buy * sol_eur,
        'total_sell_eur': total_sell * sol_eur,
        'spread_pct': spread_pct,
        'collections': {
            r['symbol']: {
                'name': r['name'],
                'buy_sol': r['buy_sol'],
                'sell_sol': r['sell_sol'],
                'buy_eur': r['buy_eur'],
                'sell_eur': r['sell_eur'],
            }
            for r in group
        },
    })

if not series:
    raise SystemExit('Aucun relevé complet de 3 collections')

latest = series[-1]
previous = series[-2] if len(series) > 1 else None

payload = {
    'generated_at': datetime.now().isoformat(timespec='seconds'),
    'latest': latest,
    'previous': previous,
    'series': series,
}
(SITE_DIR / 'data.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

html = r'''<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Tomorrowland Medallion Tracker</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
  <style>
    :root{font-family:Inter,system-ui,sans-serif;color:#e8e8f0;background:#090914}
    body{margin:0;background:radial-gradient(circle at top,#1c1740,#090914 50%);min-height:100vh}
    main{max-width:1100px;margin:auto;padding:28px 18px 50px}
    h1{font-size:clamp(25px,4vw,42px);margin:0 0 6px}.muted{color:#aaa9bd}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin:24px 0}
    .card{background:rgba(26,25,48,.88);border:1px solid #39365d;border-radius:16px;padding:18px;box-shadow:0 12px 40px #0006}
    .label{font-size:13px;color:#aaa9bd}.value{font-size:27px;font-weight:750;margin-top:7px}.sub{font-size:14px;color:#c8c7d7;margin-top:5px}
    table{width:100%;border-collapse:collapse}th,td{text-align:right;padding:12px 8px;border-bottom:1px solid #35334e}th:first-child,td:first-child{text-align:left}
    canvas{max-height:360px}.good{color:#66dfa5}.warn{color:#ffcc6b}.bad{color:#ff7b86}
    a{color:#b8a7ff}
  </style>
</head>
<body><main>
  <h1>Medallion of Memoria</h1><div class="muted" id="date"></div>
  <section class="grid">
    <div class="card"><div class="label">Achat immédiat</div><div class="value" id="buy"></div><div class="sub" id="buyEur"></div></div>
    <div class="card"><div class="label">Revente immédiate</div><div class="value" id="sell"></div><div class="sub" id="sellEur"></div></div>
    <div class="card"><div class="label">Spread total</div><div class="value" id="spread"></div><div class="sub" id="spreadSol"></div></div>
    <div class="card"><div class="label">Variation achat</div><div class="value" id="variation"></div><div class="sub">depuis le relevé précédent</div></div>
  </section>
  <section class="card"><h2>Détail par NFT</h2><table><thead><tr><th>Collection</th><th>Achat</th><th>Revente</th><th>Spread</th></tr></thead><tbody id="rows"></tbody></table></section>
  <section class="card" style="margin-top:14px"><h2>Évolution du Medallion</h2><canvas id="chart"></canvas></section>
  <p class="muted">Données Magic Eden et cours SOL/EUR Kraken. Mise à jour automatique quotidienne.</p>
</main>
<script>
const f=(n,d=4)=>Number(n).toLocaleString('fr-FR',{minimumFractionDigits:d,maximumFractionDigits:d});
fetch('data.json?'+Date.now()).then(r=>r.json()).then(d=>{
 const x=d.latest,p=d.previous;
 document.getElementById('date').textContent='Dernier relevé : '+new Date(x.timestamp).toLocaleString('fr-FR');
 document.getElementById('buy').textContent=f(x.total_buy_sol)+' SOL';
 document.getElementById('buyEur').textContent=f(x.total_buy_eur,2)+' €';
 document.getElementById('sell').textContent=f(x.total_sell_sol)+' SOL';
 document.getElementById('sellEur').textContent=f(x.total_sell_eur,2)+' €';
 document.getElementById('spread').textContent=f(x.spread_pct,2)+' %';
 document.getElementById('spreadSol').textContent=f(x.total_sell_sol-x.total_buy_sol)+' SOL';
 const v=p?((x.total_buy_sol-p.total_buy_sol)/p.total_buy_sol*100):null;
 document.getElementById('variation').textContent=v===null?'—':(v>=0?'+':'')+f(v,2)+' %';
 const body=document.getElementById('rows');
 Object.values(x.collections).forEach(c=>{
   const s=(c.sell_sol-c.buy_sol)/c.buy_sol*100;
   body.insertAdjacentHTML('beforeend',`<tr><td>${c.name}</td><td>${f(c.buy_sol)} SOL</td><td>${f(c.sell_sol)} SOL</td><td class="${s>-5?'good':s>-10?'warn':'bad'}">${f(s,2)} %</td></tr>`)
 });
 new Chart(document.getElementById('chart'),{type:'line',data:{labels:d.series.map(s=>new Date(s.timestamp).toLocaleDateString('fr-FR')),datasets:[{label:'Achat SOL',data:d.series.map(s=>s.total_buy_sol)},{label:'Revente SOL',data:d.series.map(s=>s.total_sell_sol)}]},options:{responsive:true,interaction:{mode:'index',intersect:false},scales:{x:{ticks:{color:'#aaa9bd'},grid:{color:'#29273d'}},y:{ticks:{color:'#aaa9bd'},grid:{color:'#29273d'}}},plugins:{legend:{labels:{color:'#e8e8f0'}}}}});
});
</script></body></html>'''
(SITE_DIR / 'index.html').write_text(html, encoding='utf-8')
print('Dashboard généré dans site/')
