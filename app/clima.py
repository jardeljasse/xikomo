"""
Dados de clima e solo por coordenada, guardados na base de dados (app/db.py).

- climatologia(): medias mensais 2001-2025 da NASA POWER (sem conta).
- previsao():     previsao diaria a 16 dias da Open-Meteo (sem conta).
- solo():         pH e textura da SoilGrids (ISRIC, 250 m); se o servico nao
                  responder, usa uma estimativa regional e avisa.

Cada distrito so e pedido uma vez aos servicos externos; depois le-se da base de
dados. Sem base de dados, usa-se a cache em ficheiros (app/cache/).
"""
import json
import time
from datetime import date
from pathlib import Path

import requests

from . import db

CACHE = Path(__file__).parent / "cache"
CACHE.mkdir(exist_ok=True)
MESES = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
DIAS_MES = [31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

_solo_falhou_em = 0.0  # para nao esperar pela SoilGrids em cada pedido quando esta em baixo


def _ler(nome, distrito_id, sufixo=""):
    """Base de dados primeiro; se nao houver, o ficheiro (e copia-o para a base de dados)."""
    dados = db.ler(nome, distrito_id)
    if dados is None:
        ficheiro = CACHE / f"{nome}_{distrito_id}{sufixo}.json"
        if ficheiro.exists():
            dados = json.loads(ficheiro.read_text(encoding="utf-8"))
            db.guardar(nome, distrito_id, dados)
    return dados


def _guardar(nome, distrito_id, dados, sufixo=""):
    if not db.guardar(nome, distrito_id, dados):
        ficheiro = CACHE / f"{nome}_{distrito_id}{sufixo}.json"
        ficheiro.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
    return dados


def climatologia(distrito):
    """Lista de 12 meses: chuva (mm/mes), temperatura média (C), humidade (%)."""
    dados = _ler("clima", distrito["id"])
    if dados:
        return dados
    r = requests.get(
        "https://power.larc.nasa.gov/api/temporal/climatology/point",
        params={
            "parameters": "PRECTOTCORR,T2M,RH2M",
            "community": "AG",
            "longitude": distrito["lon"],
            "latitude": distrito["lat"],
            "format": "JSON",
        },
        timeout=60,
    )
    r.raise_for_status()
    p = r.json()["properties"]["parameter"]
    meses = [
        {
            "mes": i + 1,
            "chuva_mm": round(p["PRECTOTCORR"][m] * DIAS_MES[i], 1),
            "temp_c": round(p["T2M"][m], 1),
            "humidade": round(p["RH2M"][m]),
        }
        for i, m in enumerate(MESES)
    ]
    return _guardar("clima", distrito["id"], meses)


def previsao(distrito):
    """Previsao diaria (ate 16 dias). Guardada em cache por dia."""
    hoje = "_" + date.today().isoformat()
    dados = _ler("previsao", distrito["id"], hoje)
    if dados is not None:
        return dados
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": distrito["lat"],
                "longitude": distrito["lon"],
                "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min,"
                "relative_humidity_2m_mean",
                "forecast_days": 16,
                "timezone": "Africa/Maputo",
            },
            timeout=30,
        )
        r.raise_for_status()
        d = r.json()["daily"]
    except (requests.RequestException, KeyError, ValueError):
        return []  # sem previsao: a analise usa so a media historica
    dias = [
        {
            "data": d["time"][i],
            "chuva_mm": d["precipitation_sum"][i],
            "tmax": d["temperature_2m_max"][i],
            "tmin": d["temperature_2m_min"][i],
            "humidade": d["relative_humidity_2m_mean"][i],
        }
        for i in range(len(d["time"]))
        if d["precipitation_sum"][i] is not None and d["temperature_2m_max"][i] is not None
    ]
    return _guardar("previsao", distrito["id"], dias, hoje) if dias else dias


# Estimativa regional (PROJECT_BRIEF.md), usada so quando a SoilGrids nao responde.
SOLO_REGIONAL = {
    "sul": {"ph": 6.0, "areia": 75, "argila": 10},
    "centro": {"ph": 6.2, "areia": 50, "argila": 25},
    "norte": {"ph": 5.8, "areia": 45, "argila": 35},
}
REGIAO = {
    "Maputo": "sul", "Gaza": "sul", "Inhambane": "sul",
    "Sofala": "centro", "Manica": "centro", "Tete": "centro", "Zambézia": "centro",
    "Nampula": "norte", "Niassa": "norte", "Cabo Delgado": "norte",
}


def _textura(areia, argila):
    if areia >= 65:
        return "arenoso"
    if argila >= 35:
        return "argiloso"
    return "franco"


def solo(distrito):
    """pH e textura do solo superficial (0-15 cm)."""
    global _solo_falhou_em
    dados = _ler("solo", distrito["id"])
    if dados:
        return dados
    if time.time() - _solo_falhou_em > 600:
        try:
            r = requests.get(
                "https://rest.isric.org/soilgrids/v2.0/properties/query",
                params=[
                    ("lon", distrito["lon"]), ("lat", distrito["lat"]),
                    ("property", "phh2o"), ("property", "clay"), ("property", "sand"),
                    ("depth", "0-5cm"), ("depth", "5-15cm"), ("value", "mean"),
                ],
                timeout=8,
            )
            r.raise_for_status()
            valores = {}
            for camada in r.json()["properties"]["layers"]:
                medias = [d["values"]["mean"] for d in camada["depths"]]
                medias = [m for m in medias if m is not None]
                valores[camada["name"]] = sum(medias) / len(medias) / 10  # unidades x10
            dados = {
                "ph": round(valores["phh2o"], 1),
                "areia": round(valores["sand"]),
                "argila": round(valores["clay"]),
                "fonte": "SoilGrids (ISRIC), modelo a 250 m",
                "estimativa_regional": False,
            }
            dados["textura"] = _textura(dados["areia"], dados["argila"])
            return _guardar("solo", distrito["id"], dados)
        except (requests.RequestException, KeyError, ValueError, ZeroDivisionError):
            _solo_falhou_em = time.time()
    regiao = REGIAO.get(distrito["provincia"], "centro")
    dados = dict(SOLO_REGIONAL[regiao])
    dados["textura"] = _textura(dados["areia"], dados["argila"])
    dados["fonte"] = f"estimativa regional ({regiao} do país); SoilGrids indisponível"
    dados["estimativa_regional"] = True
    return dados  # nao se guarda em cache: tenta-se de novo mais tarde
