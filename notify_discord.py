import csv
import os
from pathlib import Path
import requests

WEBHOOK = os.environ.get('DISCORD_WEBHOOK_URL', '').strip()
MEDALLION_THRESHOLD = float(os.environ.get('MEDALLION_ALERT_SOL', '65'))
DROP_THRESHOLD = float(os.environ.get('NFT_DROP_ALERT_PCT', '5'))
SPREAD_THRESHOLD = float(os.environ.get('SPREAD_ALERT_PCT', '3'))
SITE_URL = os.environ.get('SITE_URL', '').strip()

if not WEBHOOK:
    print('DISCORD_WEBHOOK_URL absent : notification ignorée.')
    raise SystemExit(0)

history = Path('data/history.csv')
if not history.exists():
    raise SystemExit('data/history.csv introuvable')

with history.open('r', encoding='utf-8', newline='') as f:
    rows = list(csv.DictReader(f))

stamps = []
for r in rows:
    if r['timestamp'] not in stamps:
        stamps.append(r['timestamp'])
latest_rows = [r for r in rows if r['timestamp'] == stamps[-1]]
previous_rows = [r for r in rows if len(stamps) > 1 and r['timestamp'] == stamps[-2]]
prev = {r['symbol']: r for r in previous_rows}

buy = sum(float(r['buy_sol']) for r in latest_rows)
sell = sum(float(r['sell_sol']) for r in latest_rows)
sol_eur = float(latest_rows[0]['sol_eur'])
spread_pct = (sell-buy)/buy*100 if buy else 0
alerts = []

if buy <= MEDALLION_THRESHOLD:
    alerts.append(f'🟢 Medallion sous **{MEDALLION_THRESHOLD:.2f} SOL**')
if abs(spread_pct) <= SPREAD_THRESHOLD:
    alerts.append(f'🟢 Spread total inférieur à **{SPREAD_THRESHOLD:.2f} %**')

for r in latest_rows:
    old = prev.get(r['symbol'])
    if old and float(old['buy_sol']) > 0:
        change = (float(r['buy_sol'])-float(old['buy_sol']))/float(old['buy_sol'])*100
        if change <= -DROP_THRESHOLD:
            alerts.append(f"📉 {r['name']} baisse de **{abs(change):.2f} %**")

fields = []
for r in latest_rows:
    b=float(r['buy_sol']); s=float(r['sell_sol']); sp=(s-b)/b*100 if b else 0
    fields.append({'name': r['name'], 'value': f"Achat **{b:.4f} SOL** · Revente **{s:.4f} SOL** · Spread **{sp:.2f} %**", 'inline': False})

if alerts:
    fields.insert(0, {'name':'🚨 Alertes', 'value':'\n'.join(alerts), 'inline':False})

payload = {
  'username':'Tomorrowland Tracker',
  'allowed_mentions': {'parse': []},
  'embeds':[{
    'title':'Relevé quotidien — Medallion of Memoria',
    'description': f"Achat total **{buy:.4f} SOL** ({buy*sol_eur:,.2f} €)\nRevente **{sell:.4f} SOL** ({sell*sol_eur:,.2f} €)\nSpread **{spread_pct:.2f} %**".replace(',', ' '),
    'fields': fields,
    'url': SITE_URL or None,
    'footer': {'text': latest_rows[0]['timestamp']},
  }]
}

resp = requests.post(WEBHOOK, json=payload, timeout=30)
if resp.status_code not in (200, 204):
    raise SystemExit(f'Discord HTTP {resp.status_code}: {resp.text[:300]}')
print('Notification Discord envoyée.')
