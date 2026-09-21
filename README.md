# alquiler-seguro-fetcher

Bot de Telegram que monitoriza los inmuebles en alquiler de [alquilerseguro.es](https://www.alquilerseguro.es) y avisa cuando aparece uno nuevo que cumpla tus filtros.

## Características

- `/start` — registra tu usuario en el bot.
- `/config` — configura tus filtros de forma interactiva: provincia, precio mínimo/máximo, habitaciones y baños (usa `-` para omitir un campo).
- `/get` — busca ahora con tus filtros y muestra todos los resultados.
- Alertas automáticas cada `POLL_INTERVAL` segundos (por defecto 120): cuando aparece un inmueble **nuevo** que cumple tus filtros, te envía un mensaje con sus datos y enlace.

## Cómo funciona

- Consulta `https://www.alquilerseguro.es/api/inmueble/get-all?lang=es-ES&provincia=X`.
- Filtra en local (precio min/max, habitaciones, baños).
- La configuración es **por usuario** y se guarda en SQLite.
- Al guardar `/config` marca como "vistos" los inmuebles ya publicados, para no spamear; solo avisa de los que aparezcan a partir de entonces.

## Requisitos

- Docker y Docker Compose.

## Puesta en marcha

```bash
cp .env.example .env
# edita .env y pon tu BOT_TOKEN
docker compose up -d --build
docker compose logs -f
```

## Variables de entorno

| Variable | Descripción | Default |
| --- | --- | --- |
| `BOT_TOKEN` | Token del bot de Telegram (obligatorio) | — |
| `POLL_INTERVAL` | Segundos entre cada comprobación de alertas | `120` |
| `DATA_DIR` | Directorio donde se guarda la BD SQLite | `/app/data` |
| `ADMIN_CHAT_ID` | Chat id que recibe aviso si el fetch falla repetidamente | — |
| `FAILURE_THRESHOLD` | Fallos consecutivos antes de avisar al admin | `3` |

## Despliegue en producción

```bash
# copiar al server
scp -r alquiler-seguro-fetcher user@server:/srv/

# en el server
cd /srv/alquiler-seguro-fetcher
cp .env.example .env    # pon BOT_TOKEN
docker compose up -d --build
```

- `restart: unless-stopped` ya está configurado: Docker relanza el bot si cae o al reiniciar la máquina.
- La BD persiste en el volumen `./data` entre deploys.
- Para actualizar: `git pull` y `docker compose up -d --build`.

## Estructura

```
alquiler-seguro-fetcher/
├── bot.py               # handlers, /config, /get, job de alertas
├── fetcher.py           # llamada a la API y filtrado client-side
├── db.py                # SQLite (users, user_config, notified)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── .gitignore
```

## Notas

- La respuesta de la API trae caracteres de control en las descripciones, por eso se parsea con `json.loads(texto, strict=False)`.
- El token del bot va en `.env` y nunca se sube al repositorio (está en `.gitignore`).
- Si el fetch falla `FAILURE_THRESHOLD` veces seguidas, se avisa a `ADMIN_CHAT_ID` y se reinicia el contador (sin spam).
