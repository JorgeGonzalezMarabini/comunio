# Comunio Liga Bot

Bot de gestión automática de un equipo en Comunio (liga privada): fichajes/pujas,
evaluación de jugadores (Comunio + stats externas), alineaciones automáticas y
notificación de cada acción. Coste 0: sin APIs de pago ni VPS de pago.

## Estado actual

- [ ] **Captura de endpoints reales de Comunio (HAR) — bloqueante inicial**
- [ ] Cliente de Comunio (`clients/comunio_client.py`) — esqueleto listo, métodos sin implementar
- [ ] Cliente de stats externas (`clients/laliga_stats_client.py`) — Understat parcialmente implementado, FBref/lesiones pendientes
- [x] Esquema de base de datos (`db/models.py`) — provisional, se ajustará con datos reales
- [x] Motor de evaluación (`engine/evaluator.py`) — pesos configurables en `config.py`, normalización pendiente de calibrar
- [x] Estrategia de pujas (`engine/bidding_strategy.py`) — límites de seguridad configurables en `config.py`
- [x] Optimizador de alineaciones (`engine/lineup_optimizer.py`) — formaciones básicas, falta dificultad del rival
- [x] Jobs y scheduler en GitHub Actions — YAMLs listos en modo manual (`workflow_dispatch`), cron comentado hasta tener credenciales
- [x] Notificaciones por Telegram (`notifier.py`)

## Siguiente paso obligatorio

Nada en `clients/comunio_client.py` puede completarse sin capturar antes, con
Chrome DevTools → Network → HAR, el flujo real de login de Comunio (URL,
payload, estructura del token, header de autenticación) y los endpoints de
clasificación/plantilla/mercado/pujas.

## Setup local

Requiere Python 3.11+ (`.venv` de este repo usa 3.14).

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # y rellenar credenciales
python -m db.models    # crea db/comunio.db con el esquema
```

## Estructura

Ver el detalle de cada módulo en su propio docstring. Resumen:

```
clients/    -> integraciones externas (Comunio, Understat/FBref)
db/         -> esquema SQLite + conexión
engine/     -> evaluator, bidding_strategy, lineup_optimizer
jobs/       -> entrypoints ejecutados por cron (sync_data, run_market, set_lineup)
notifier.py -> resumen por Telegram tras cada job
logs/       -> auditoría de decisiones
```
