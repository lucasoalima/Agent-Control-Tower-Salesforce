"""ASGI entry point: ``uvicorn control_tower.main:app``."""

from control_tower.api.app import create_app

app = create_app()
