"""Phone / browser remote: each phone gets its own room in the same weather.

* zones.py  - ZoneHub (shared weather + phone rooms) and Zone (one phone)
* server.py - RemoteServer: LAN HTTP + WebSocket (stdlib asyncio)
* qr.py     - QR Code encoder for the pairing link
* web/      - the phone web app (static files, no build step)

Protocol: PROTOCOL.md. Try it without the desktop: python -m app.remote.devserver
"""
