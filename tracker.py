import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


MAGIC_EDEN_BASE = "https://api-mainnet.magiceden.dev/v2"
KRAKEN_URL = "https://api.kraken.com/0/public/Ticker"

COLLECTIONS = [
    {
        "name": "A Letter from the Universe",
        "symbol": "tomorrowland_winter",
    },
    {
        "name": "The Reflection of Love",
        "symbol": "the_reflection_of_love",
    },
    {
        "name": "The Symbol of Love and Unity",
        "symbol": "tomorrowland_love_unity",
    },
]

PARIS = ZoneInfo("Europe/Paris")
TIMEOUT = 30

DATA_DIR = Path("data")
REPORT_DIR = Path("reports")
HISTORY_FILE = DATA_DIR / "history.csv"

MAGIC_EDEN_API_KEY = os.environ.get("MAGIC_EDEN_API_KEY", "").strip()


class OfficialDataUnavailable(Exception):
    pass


def headers_magic_eden():
    headers = {
        "accept": "application/json",
        "user-agent": "Tomorrowland-Medallion-Tracker/1.0",
    }

    if MAGIC_EDEN_API_KEY:
        headers["Authorization"] = f"Bearer {MAGIC_EDEN_API_KEY}"

    return headers


def get_json(url, params=None, headers=None):
    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise OfficialDataUnavailable(
            f"Erreur réseau pour {url}: {exc}"
        ) from exc

    if response.status_code != 200:
        raise OfficialDataUnavailable(
            f"HTTP {response.status_code} pour {response.url}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise OfficialDataUnavailable(
            f"Réponse non JSON pour {response.url}"
        ) from exc


def extract_items(payload):
    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        for key in ("results", "items", "data", "pools"):
            value = payload.get(key)
            if isinstance(value, list):
                return value

    return []


def get_cheapest_listing(symbol):
    url = f"{MAGIC_EDEN_BASE}/collections/{symbol}/listings"

    payload = get_json(
        url,
        params={
            "offset": 0,
            "limit": 20,
            "sort": "listPrice",
            "sort_direction": "asc",
            "listingAggMode": "false",
        },
        headers=headers_magic_eden(),
    )

    listings = extract_items(payload)

    valid = []
    for listing in listings:
        price = listing.get("price", listing.get("listPrice"))

        if isinstance(price, (int, float)) and price > 0:
            valid.append((float(price), listing))

    if not valid:
        raise OfficialDataUnavailable(
            f"Aucun listing actif vérifiable pour {symbol}"
        )

    valid.sort(key=lambda value: value[0])
    return valid[0]


def to_number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def lamports_to_sol(value):
    """
    Les champs financiers MMM sont normalement retournés en lamports.
    Une valeur déjà exprimée en SOL est conservée par sécurité.
    """
    amount = to_number(value)

    if amount <= 0:
        return 0.0

    # Les prix de ces collections dépassent plusieurs SOL :
    # une valeur supérieure à 1 000 000 est nécessairement en lamports.
    if amount > 1_000_000:
        return amount / 1_000_000_000

    return amount


def find_adjusted_price(pool):
    """
    Calcule le BUYSIDE_ADJUSTED_PRICE réellement reçu par le vendeur :

    spotPrice - royalties - LP fee éventuel.

    Les royalties réellement appliquées correspondent à :
    part de royalties acceptée par le pool
    × royalties totales de la collection.
    """

    spot_price_sol = lamports_to_sol(pool.get("spotPrice"))

    if spot_price_sol <= 0:
        return None

    royalty_share_bp = to_number(
        pool.get("buysideCreatorRoyaltyBp")
    )

    collection_royalty_bp = to_number(
        pool.get("collectionSellerFeeBasisPoints")
    )

    lp_fee_bp = to_number(pool.get("lpFeeBp"))

    payment_amount_sol = lamports_to_sol(
        pool.get("buysidePaymentAmount")
    )

    sellside_asset_amount = to_number(
        pool.get("sellsideAssetAmount")
    )

    # Magic Eden : un pool est considéré comme two-sided lorsque
    # le dépôt SOL dépasse le spot price et qu'il contient plus d'un NFT.
    is_two_sided = (
        payment_amount_sol > spot_price_sol
        and sellside_asset_amount > 1
    )

    royalty_rate = (
        royalty_share_bp / 10_000
    ) * (
        collection_royalty_bp / 10_000
    )

    lp_fee_rate = (
        lp_fee_bp / 10_000
        if is_two_sided
        else 0.0
    )

    adjusted_price_sol = spot_price_sol * (
        1 - royalty_rate - lp_fee_rate
    )

    if adjusted_price_sol <= 0:
        return None

    return adjusted_price_sol


def pool_is_executable(pool):
    now_timestamp = int(datetime.now(PARIS).timestamp())

    # Vérification de l'expiration
    expiry = int(to_number(pool.get("expiry")))

    if expiry > 0 and expiry <= now_timestamp:
        return False

    # Magic Eden calcule directement le nombre d'ordres
    # que le pool peut encore exécuter.
    buy_orders_amount = to_number(
        pool.get("buyOrdersAmount")
    )

    if buy_orders_amount <= 0:
        return False

    spot_price_sol = lamports_to_sol(
        pool.get("spotPrice")
    )

    payment_amount_sol = lamports_to_sol(
        pool.get("buysidePaymentAmount")
    )

    if spot_price_sol <= 0:
        return False

    # Un solde acheteur positif doit être disponible.
    if payment_amount_sol <= 0:
        return False

    return True


def get_best_offer(symbol):
    url = f"{MAGIC_EDEN_BASE}/mmm/pools"

    payload = get_json(
        url,
        params={
            "collectionSymbol": symbol,
            "offset": 0,
            "limit": 100,
            "field": 5,
            "direction": 1,
        },
        headers=headers_magic_eden(),
    )

    REPORT_DIR.mkdir(exist_ok=True)

    diagnostic_path = REPORT_DIR / f"mmm_{symbol}.json"
    diagnostic_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    pools = extract_items(payload)

    print(
        f"[MMM] {symbol} : "
        f"{len(pools)} pool(s) retourné(s)"
    )

    if not pools:
        print(
            f"[MMM] {symbol} : "
            "aucun pool retourné par Magic Eden"
        )
        return 0.0, None

    offers = []

    for pool in pools:
        if not pool_is_executable(pool):
            continue

        adjusted_price = find_adjusted_price(pool)

        if adjusted_price is None:
            continue

        offers.append((adjusted_price, pool))

    if not offers:
        print(
            f"[MMM] {symbol} : pools présents, "
            "mais aucune offre actuellement exécutable"
        )
        return 0.0, None

    # Double sécurité : le résultat est retrié localement.
    offers.sort(
        key=lambda value: value[0],
        reverse=True,
    )

    best_price, best_pool = offers[0]

    print(
        f"[MMM] {symbol} : meilleure offre nette "
        f"= {best_price:.6f} SOL ; "
        f"pool={best_pool.get('poolKey', 'inconnu')}"
    )

    return best_price, best_pool


def get_sol_eur():
    payload = get_json(
        KRAKEN_URL,
        params={"pair": "SOLEUR"},
        headers={
            "accept": "application/json",
            "user-agent": "Tomorrowland-Medallion-Tracker/1.0",
        },
    )

    errors = payload.get("error", [])

    if errors:
        raise OfficialDataUnavailable(
            f"Erreur Kraken : {errors}"
        )

    result = payload.get("result", {})

    if not isinstance(result, dict) or not result:
        raise OfficialDataUnavailable(
            "Cours SOL/EUR Kraken indisponible"
        )

    ticker = next(iter(result.values()), None)

    if not isinstance(ticker, dict):
        raise OfficialDataUnavailable(
            "Réponse Kraken inexploitable"
        )

    # c[0] = prix de la dernière transaction
    last_trade = ticker.get("c")

    if (
        not isinstance(last_trade, list)
        or not last_trade
    ):
        raise OfficialDataUnavailable(
            "Dernier cours SOL/EUR Kraken absent"
        )

    try:
        price = float(last_trade[0])
    except (TypeError, ValueError) as exc:
        raise OfficialDataUnavailable(
            "Cours SOL/EUR Kraken invalide"
        ) from exc

    if price <= 0:
        raise OfficialDataUnavailable(
            "Cours SOL/EUR Kraken nul ou négatif"
        )

    return price, int(datetime.now(PARIS).timestamp())


def money_eur(value):
    return (
        f"{value:,.2f}"
        .replace(",", " ")
        .replace(".", ",")
        + " €"
    )


def number_sol(value):
    return f"{value:.4f} SOL".replace(".", ",")


def percentage(value):
    return f"{value:.2f} %".replace(".", ",")


def load_previous():
    if not HISTORY_FILE.exists():
        return {}

    with HISTORY_FILE.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as file:
        rows = list(csv.DictReader(file))

    if not rows:
        return {}

    latest_timestamp = rows[-1]["timestamp"]

    return {
        row["symbol"]: row
        for row in rows
        if row["timestamp"] == latest_timestamp
    }


def save_history(timestamp, rows):
    DATA_DIR.mkdir(exist_ok=True)

    exists = HISTORY_FILE.exists()

    fields = [
        "timestamp",
        "symbol",
        "name",
        "buy_sol",
        "sell_sol",
        "sol_eur",
        "buy_eur",
        "sell_eur",
    ]

    with HISTORY_FILE.open(
        "a",
        encoding="utf-8",
        newline="",
    ) as file:
        writer = csv.DictWriter(file, fieldnames=fields)

        if not exists:
            writer.writeheader()

        for row in rows:
            writer.writerow({
                key: row[key]
                for key in fields
            })


def calculate_variation(current, previous):
    if previous is None:
        return None

    old = float(previous)

    if old == 0:
        return None

    return ((current - old) / old) * 100


def main():
    now = datetime.now(PARIS)
    timestamp = now.isoformat(timespec="seconds")

    previous = load_previous()

    try:
        sol_eur, sol_updated_at = get_sol_eur()

        results = []

        for collection in COLLECTIONS:
            symbol = collection["symbol"]

            buy_sol, listing = get_cheapest_listing(symbol)
            sell_sol, pool = get_best_offer(symbol)

            buy_eur = buy_sol * sol_eur
            sell_eur = sell_sol * sol_eur
            spread_sol = sell_sol - buy_sol
            spread_eur = sell_eur - buy_eur
            spread_pct = (
                (spread_sol / buy_sol) * 100
                if buy_sol > 0
                else None
            )

            old = previous.get(symbol)

            results.append({
                "timestamp": timestamp,
                "symbol": symbol,
                "name": collection["name"],
                "buy_sol": buy_sol,
                "sell_sol": sell_sol,
                "sol_eur": sol_eur,
                "buy_eur": buy_eur,
                "sell_eur": sell_eur,
                "spread_sol": spread_sol,
                "spread_eur": spread_eur,
                "spread_pct": spread_pct,
                "buy_change_24h": calculate_variation(
                    buy_sol,
                    old["buy_sol"] if old else None,
                ),
                "sell_change_24h": calculate_variation(
                    sell_sol,
                    old["sell_sol"] if old else None,
                ),
                "listing": listing,
                "pool": pool,
            })

    except OfficialDataUnavailable as exc:
        REPORT_DIR.mkdir(exist_ok=True)

        report = (
            f"# Relevé Tomorrowland\n\n"
            f"Date : {now.strftime('%d/%m/%Y à %H:%M:%S')} "
            f"Europe/Paris\n\n"
            f"## Relevé non fiable\n\n"
            f"**Relevé impossible — donnée officielle inaccessible.**\n\n"
            f"Détail technique : `{exc}`\n"
        )

        report_path = REPORT_DIR / "latest.md"
        report_path.write_text(report, encoding="utf-8")

        print(report)
        sys.exit(1)

    total_buy_sol = sum(row["buy_sol"] for row in results)
    total_sell_sol = sum(row["sell_sol"] for row in results)

    total_buy_eur = total_buy_sol * sol_eur
    total_sell_eur = total_sell_sol * sol_eur

    total_spread_sol = total_sell_sol - total_buy_sol
    total_spread_eur = total_sell_eur - total_buy_eur

    total_spread_pct = (
        total_spread_sol / total_buy_sol * 100
        if total_buy_sol > 0
        else None
    )

    lines = [
        "# Relevé Tomorrowland — Medallion of Memoria",
        "",
        (
            f"Date : **{now.strftime('%d/%m/%Y à %H:%M:%S')} "
            f"Europe/Paris**"
        ),
        "",
        (
            f"Cours SOL/EUR : **{money_eur(sol_eur)}** — "
            f"source : Kraken — dernière transaction SOLEUR"
        ),
        "",
    ]

    for row in results:
        buy_share = (
            row["buy_sol"] / total_buy_sol * 100
            if total_buy_sol > 0
            else 0
        )

        sell_share = (
            row["sell_sol"] / total_sell_sol * 100
            if total_sell_sol > 0
            else 0
        )

        lines.extend([
            f"## {row['name']}",
            "",
            (
                f"- Achat immédiat : **{number_sol(row['buy_sol'])}** "
                f"/ **{money_eur(row['buy_eur'])}**"
            ),
            (
                f"- Revente immédiate : "
                f"**{number_sol(row['sell_sol'])}** "
                f"/ **{money_eur(row['sell_eur'])}**"
                if row["sell_sol"] > 0
                else
                "- Revente immédiate : **0,00 SOL / 0,00 €** "
                "— aucune offre exécutable"
            ),
            (
                f"- Spread : **{number_sol(row['spread_sol'])}** "
                f"/ **{money_eur(row['spread_eur'])}** "
                f"/ **{percentage(row['spread_pct'])}**"
            ),
            (
                f"- Quote-part achat : **{percentage(buy_share)}**"
            ),
            (
                f"- Quote-part revente : **{percentage(sell_share)}**"
            ),
        ])

        if row["buy_change_24h"] is not None:
            lines.append(
                f"- Variation achat : "
                f"**{percentage(row['buy_change_24h'])}**"
            )

        if row["sell_change_24h"] is not None:
            lines.append(
                f"- Variation revente : "
                f"**{percentage(row['sell_change_24h'])}**"
            )

        lines.append("")

    lines.extend([
        "## Medallion complet",
        "",
        (
            f"- Coût total d’achat : "
            f"**{number_sol(total_buy_sol)}** "
            f"/ **{money_eur(total_buy_eur)}**"
        ),
        (
            f"- Valeur totale de revente : "
            f"**{number_sol(total_sell_sol)}** "
            f"/ **{money_eur(total_sell_eur)}**"
        ),
        (
            f"- Spread total : "
            f"**{number_sol(total_spread_sol)}** "
            f"/ **{money_eur(total_spread_eur)}** "
            f"/ **{percentage(total_spread_pct)}**"
        ),
        "",
    ])

    if total_sell_sol == 0:
        conclusion = (
            "Liquidité acheteuse inexistante : attendre avant d’acheter, "
            "sauf objectif d’usage personnel du Medallion."
        )
    elif total_spread_pct <= -25:
        conclusion = (
            "Spread important et liquidité limitée : attendre ou placer "
            "des offres plutôt que d’acheter immédiatement."
        )
    elif total_spread_pct <= -10:
        conclusion = (
            "Liquidité présente mais spread significatif : achat immédiat "
            "à envisager seulement sur un prix particulièrement attractif."
        )
    else:
        conclusion = (
            "Liquidité relativement correcte au moment du relevé, "
            "mais vérifier la profondeur des offres avant tout achat."
        )

    lines.extend([
        "## Conclusion",
        "",
        conclusion,
        "",
    ])

    report = "\n".join(lines)

    REPORT_DIR.mkdir(exist_ok=True)

    (REPORT_DIR / "latest.md").write_text(
        report,
        encoding="utf-8",
    )

    dated_path = REPORT_DIR / f"{now.strftime('%Y-%m-%d')}.md"
    dated_path.write_text(report, encoding="utf-8")

    raw_path = REPORT_DIR / f"{now.strftime('%Y-%m-%d')}.json"
    raw_path.write_text(
        json.dumps(
            results,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    save_history(timestamp, results)

    print(report)


if __name__ == "__main__":
    main()
