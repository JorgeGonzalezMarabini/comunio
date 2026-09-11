"""
Backtest offline (a petición del usuario, evaluación del trigger de venta,
2026-09-11) de los umbrales de rentabilidad/corte de pérdidas de
`engine/selling_strategy.py` (`SELLING_MIN_PROFIT_PCT`/`SELLING_MAX_LOSS_PCT`)
contra el histórico REAL ya acumulado en la BD local -- resuelve el "sin
calibrar todavía con resultados reales" que marcan esos mismos comentarios
en `config.py`.

Solo LECTURA: nunca escribe en la BD ni llama a la API de Futmondo. Pensado
para ejecutarse a mano de vez en cuando (`python scripts/backtest_selling_
thresholds.py`), no como job programado -- con poco histórico acumulado el
resultado es ruidoso (ver "Limitaciones" más abajo), así que no tiene
sentido automatizarlo todavía.

Metodología:
  1. Para cada jugador con al menos una puja 'won' (misma fuente y mismo
     criterio de "más reciente" que `db.models.get_won_bid_prices()`), se
     reconstruye su serie de precio POSTERIOR a la compra a partir de
     `futmondo_snapshots` (histórico local que ya recoge cada pasada de
     jobs/sync_data.py -- ni una llamada de red).
  2. Para una combinación de umbrales (profit_th, loss_th), se recorre esa
     serie en orden cronológico y se marca el primer punto en que
     `profit_pct = (price - buy_price) / buy_price` cruza `+profit_th` o
     `-loss_th` -- exactamente la misma condición que evalúa
     `engine.selling_strategy.decide_sales()` en producción (vías 1 y 2).
     Sin cruce dentro del histórico disponible, el jugador queda "sin
     resolver" (censurado): se usa su último precio conocido como marca a
     mercado, no se inventa un desenlace.
  3. Se resume por combinación: nº de salidas por ganancia/pérdida, nº sin
     resolver, retorno medio equal-weighted (por decisión) y
     capital-weighted (ponderado por precio de compra -- una posición de
     20M pesa más que una de 1M, igual que en la cartera real).
  4. Se contrasta contra el resultado YA MATERIALIZADO en `sales`
     (status='sold') -- validación cruzada de que la simulación reproduce
     razonablemente el comportamiento real del bot.
  5. Diagnóstico aparte: variación de precio en las primeras 24h tras la
     compra, para distinguir "el umbral de venta corta mal" de "el precio
     de ENTRADA (puja) ya nace caro y se corrige solo, independientemente
     de cuándo se decida vender".

Limitaciones (léanse antes de tocar ningún umbral con este resultado):
  - Muestra pequeña y ventana corta (unas pocas decenas de jugadores,
    semanas de histórico en un repo joven) -- las diferencias entre
    combinaciones de umbrales pueden estar dentro del margen de ruido.
    Cuantos más días de histórico acumule `futmondo_snapshots`, más fiable
    este backtest.
  - No es un backtest de cartera con reinversión (no simula qué se habría
    comprado con el dinero liberado en cada salida) -- solo evalúa, por
    cada compra ya realizada de verdad, cuándo la habría cortado cada
    combinación de umbrales. Sirve para calibrar EL UMBRAL, no para
    proyectar el rendimiento total de la estrategia.
  - Usa el precio de compra real (`bids.amount`, la puja ganada), no
    `buyPrice` de Futmondo -- misma fuente que `decide_sales()` en
    producción (ver docstring de `db.models.get_won_bid_prices()`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import config
from db.models import get_connection

# Rejilla de umbrales alternativos a comparar contra el vigente en config.
PROFIT_THRESHOLDS_GRID = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
LOSS_THRESHOLDS_GRID = [0.05, 0.10, 0.15, 0.20]


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_purchases() -> dict[str, dict]:
    """
    {player_id: {"price", "date"}} -- precio pagado y fecha de la puja
    'won' MÁS RECIENTE por jugador (mismo criterio que
    `db.models.get_won_bid_prices()`, para el caso de recompra tras una
    venta anterior).
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            WITH latest_won AS (
                SELECT player_id, amount, created_at, ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY id DESC) AS rn
                FROM bids WHERE status = 'won'
            )
            SELECT player_id, amount, created_at FROM latest_won WHERE rn = 1
            """
        ).fetchall()
    return {row["player_id"]: {"price": row["amount"], "date": row["created_at"]} for row in rows}


def load_price_histories(purchases: dict[str, dict]) -> dict[str, list[tuple[str, int]]]:
    """
    {player_id: [(recorded_at, price), ...]} en orden cronológico, SOLO
    puntos posteriores a la compra de ese jugador -- de
    `futmondo_snapshots`, sin llamada de red.
    """
    histories = {}
    with get_connection() as conn:
        for player_id, info in purchases.items():
            rows = conn.execute(
                """
                SELECT price, recorded_at FROM futmondo_snapshots
                WHERE player_id = ? AND recorded_at >= ? AND price > 0
                ORDER BY recorded_at ASC
                """,
                (player_id, info["date"]),
            ).fetchall()
            histories[player_id] = [(row["recorded_at"], row["price"]) for row in rows]
    return histories


def simulate(
    purchases: dict[str, dict], histories: dict[str, list[tuple[str, int]]], profit_th: float, loss_th: float
) -> list[dict]:
    """
    Para cada compra, recorre su histórico y marca el primer cruce de
    `+profit_th`/`-loss_th` (ver docstring del módulo). Sin cruce, queda
    censurado con la última marca conocida.
    """
    results = []
    for player_id, info in purchases.items():
        buy_price = info["price"]
        buy_date = _parse(info["date"])
        exit_reason = exit_pct = exit_days = None
        last_pct = last_days = None
        for recorded_at, price in histories.get(player_id, []):
            pct = (price - buy_price) / buy_price
            days = (_parse(recorded_at) - buy_date).total_seconds() / 86400
            last_pct, last_days = pct, days
            if pct >= profit_th:
                exit_reason, exit_pct, exit_days = "ganancia", pct, days
                break
            if pct <= -loss_th:
                exit_reason, exit_pct, exit_days = "pérdida", pct, days
                break
        results.append(
            {
                "player_id": player_id,
                "buy_price": buy_price,
                "exit_reason": exit_reason,
                "exit_pct": exit_pct,
                "exit_days": exit_days,
                "censored_pct": last_pct if exit_reason is None else None,
                "censored_days": last_days if exit_reason is None else None,
                "n_points": len(histories.get(player_id, [])),
            }
        )
    return results


def summarize(results: list[dict], label: str, verbose: bool = False) -> None:
    resolved = [r for r in results if r["exit_reason"] is not None]
    profit_exits = [r for r in resolved if r["exit_reason"] == "ganancia"]
    loss_exits = [r for r in resolved if r["exit_reason"] == "pérdida"]
    censored = [r for r in results if r["exit_reason"] is None]

    # Marca a mercado: resuelto -> pct de salida; sin resolver -> último precio conocido.
    marked = [(r["exit_pct"] if r["exit_reason"] else r["censored_pct"], r["buy_price"]) for r in results]
    eq_avg = sum(p for p, _ in marked) / len(marked) if marked else 0.0
    total_weight = sum(w for _, w in marked)
    cap_avg = sum(p * w for p, w in marked) / total_weight if total_weight else 0.0

    avg_exit_pct = sum(r["exit_pct"] for r in resolved) / len(resolved) if resolved else None
    avg_days = sum(r["exit_days"] for r in resolved) / len(resolved) if resolved else None
    avg_censored_pct = sum(r["censored_pct"] for r in censored) / len(censored) if censored else None

    line = (
        f"{label:14s} n={len(results):3d} ganancia={len(profit_exits):3d} pérdida={len(loss_exits):3d} "
        f"sin_resolver={len(censored):3d} | eq_avg={eq_avg:+7.2%} cap_avg={cap_avg:+7.2%}"
    )
    if resolved:
        line += f" | avg_salida={avg_exit_pct:+.2%} avg_días={avg_days:.1f}"
    if censored:
        line += f" | avg_sin_resolver={avg_censored_pct:+.2%}"
    print(line)

    if verbose:
        ordering = sorted(
            results,
            key=lambda r: (r["exit_reason"] or "zzz", r["exit_pct"] if r["exit_pct"] is not None else r["censored_pct"]),
        )
        for r in ordering:
            tag = r["exit_reason"] or "sin resolver"
            pct = r["exit_pct"] if r["exit_pct"] is not None else r["censored_pct"]
            days = r["exit_days"] if r["exit_days"] is not None else r["censored_days"]
            print(f"   {r['player_id']:>26}  {tag:13s} {pct:+7.2%}  {days:5.1f}d  (n={r['n_points']})")


def early_drift_diagnostics(purchases: dict[str, dict], histories: dict[str, list[tuple[str, int]]]) -> None:
    """
    Variación de precio en el primer snapshot tras la compra y en el más
    cercano a +24h -- distingue "el umbral de venta corta mal" de "el
    precio de ENTRADA (puja) ya nace caro y se corrige solo" (ver
    docstring del módulo).
    """
    pct_first, pct_24h = [], []
    for player_id, info in purchases.items():
        buy_price = info["price"]
        buy_date = _parse(info["date"])
        hist = histories.get(player_id, [])
        if not hist:
            continue
        first_recorded_at, first_price = hist[0]
        pct_first.append((first_price - buy_price) / buy_price)

        target = buy_date + timedelta(hours=24)
        closest_at, closest_price = min(hist, key=lambda h: abs((_parse(h[0]) - target).total_seconds()))
        if abs((_parse(closest_at) - target).total_seconds()) < 6 * 3600:  # solo si hay dato a +-6h de las 24h
            pct_24h.append((closest_price - buy_price) / buy_price)

    def stats(values: list[float], label: str) -> None:
        if not values:
            print(f"{label}: sin datos suficientes todavía")
            return
        values_sorted = sorted(values)
        n = len(values)
        avg = sum(values) / n
        median = values_sorted[n // 2]
        negative_pct = sum(1 for v in values if v < 0) / n
        print(f"{label}: n={n} media={avg:+.2%} mediana={median:+.2%} %negativos={negative_pct:.0%} min={min(values):+.2%} max={max(values):+.2%}")

    print("\n=== Deriva temprana tras la compra (posible sobrepago en puja) ===")
    stats(pct_first, "Primer snapshot tras la compra")
    stats(pct_24h, "Snapshot más cercano a +24h")


def compare_with_real_sales() -> None:
    """Contraste contra lo YA MATERIALIZADO en `sales` -- valida que la simulación se parece a la realidad."""
    with get_connection() as conn:
        sold = conn.execute(
            "SELECT profit, profit_pct, reason FROM sales WHERE status = 'sold'"
        ).fetchall()

    print("\n=== Contraste con ventas YA completadas (tabla sales, status='sold') ===")
    if not sold:
        print("Sin ventas completadas todavía.")
        return

    total_profit = sum(row["profit"] or 0 for row in sold)
    avg_pct = sum(row["profit_pct"] or 0 for row in sold) / len(sold)
    print(f"Ventas completadas: {len(sold)} | plusvalía total real: {total_profit:,} | profit_pct medio: {avg_pct:+.2%}")

    reasons = {}
    for row in sold:
        reason = row["reason"] or ""
        if "corte de p" in reason:
            key = "corte de pérdidas"
        elif "lesión" in reason.lower():
            key = "lesión"
        elif "mercado" in reason:
            key = "oportunidad de mercado"
        elif "reversión desde máximo" in reason:
            key = "trailing-stop"
        else:
            key = "rentabilidad normal"
        reasons[key] = reasons.get(key, 0) + 1
    print("Por motivo:", reasons)


def main() -> None:
    purchases = load_purchases()
    if not purchases:
        print("Sin pujas 'won' todavía -- nada que backtestear.")
        return
    histories = load_price_histories(purchases)

    n_points = [len(h) for h in histories.values()]
    oldest_purchase = min(_parse(info["date"]) for info in purchases.values())
    now = datetime.now(timezone.utc)
    print(f"Jugadores comprados por el bot (pujas 'won'): {len(purchases)}")
    print(f"Puntos de histórico por jugador: min={min(n_points)} max={max(n_points)} media={sum(n_points) / len(n_points):.1f}")
    print(f"Compra más antigua: {oldest_purchase.date()} -- ventana real de backtest: {(now - oldest_purchase).days} días\n")

    current_profit_th = config.SELLING_MIN_PROFIT_PCT
    current_loss_th = config.SELLING_MAX_LOSS_PCT
    print(f"=== Umbral VIGENTE en config ({current_profit_th:.0%} ganancia / {current_loss_th:.0%} pérdida) ===")
    summarize(simulate(purchases, histories, current_profit_th, current_loss_th), "vigente", verbose=True)

    print("\n=== Rejilla de umbrales alternativos ===")
    for profit_th in PROFIT_THRESHOLDS_GRID:
        for loss_th in LOSS_THRESHOLDS_GRID:
            label = f"{profit_th:.0%}/{loss_th:.0%}"
            summarize(simulate(purchases, histories, profit_th, loss_th), label)

    early_drift_diagnostics(purchases, histories)
    compare_with_real_sales()


if __name__ == "__main__":
    main()
