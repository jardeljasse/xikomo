"""
Verifica e envia os alertas por SMS a todos os assinantes activos.
Correr uma vez por dia (por exemplo com cron, as 6h):
    uv run python enviar_alertas.py
Por agora o envio e simulado (ver app/sms.py).
"""
from app import sms
from app.server import POR_ID

if __name__ == "__main__":
    print(sms.verificar_todos(POR_ID))
