# Claude project guide

Start with [AGENTS.md](AGENTS.md). It is the shared, compact map of the current app, important code entry points, data boundaries, and local checks. Follow its links into source only for the part of the app you are changing.

The Flask backend and browser UI are in `app.py` and `web/`; the native Compose client is in `android/`. Treat source as authoritative when older planning documents disagree. Keep `AGENTS.md` accurate as the app evolves.
