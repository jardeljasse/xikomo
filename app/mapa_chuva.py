"""
Imagem da chuva de um mes (NASA GLDAS) para o mapa da pagina.

O servico da Esri exige chave, por isso o pedido e feito aqui no servidor
(ARCGIS_API_KEY no .env) e a chave nunca chega ao browser. Os valores em mm
sao pintados com a nossa propria escala: o renderizador da Esri esta afinado
para o mundo inteiro e deixa Mocambique todo da mesma cor.
"""
import io
import json
import os
from datetime import datetime, timezone

import numpy as np
import rasterio
import requests
from PIL import Image

from . import db  # carrega o .env

SERVICO = "https://earthobs2.arcgis.com/arcgis/rest/services/GLDAS_Precipitation/ImageServer"
# limite inferior de cada classe (mm no mes) e a sua cor
ESCALA = [(10, "#deebf7"), (25, "#c6dbef"), (50, "#9ecae1"), (100, "#6baed6"),
          (150, "#4292c6"), (200, "#2171b5"), (300, "#08519c"), (400, "#08306b")]
_catalogo = None


def _pedir(caminho, **parametros):
    chave = os.environ.get("ARCGIS_API_KEY")
    if not chave:
        raise RuntimeError("Falta ARCGIS_API_KEY no .env")
    r = requests.get(f"{SERVICO}/{caminho}", params={"token": chave, **parametros}, timeout=60)
    r.raise_for_status()
    return r


def catalogo():
    """{(ano, mes): id do raster}. Lido uma vez por arranque do servidor."""
    global _catalogo
    if _catalogo is None:
        meses, inicio = {}, 0
        while True:
            pagina = _pedir("query", f="json", where="Variable = 'Rainfall (mm)' AND Tag = 'Actual'",
                            outFields="OBJECTID,StdTime", returnGeometry="false", orderByFields="StdTime",
                            resultOffset=inicio, resultRecordCount=100).json()
            if "error" in pagina:
                raise RuntimeError(pagina["error"].get("message", "erro do serviço"))
            linhas = pagina.get("features", [])
            for l in linhas:
                d = datetime.fromtimestamp(l["attributes"]["StdTime"] / 1000, timezone.utc)
                meses[(d.year, d.month)] = l["attributes"]["OBJECTID"]
            if not linhas:
                break
            inicio += len(linhas)
        _catalogo = meses
    return _catalogo


def imagem(ano, mes, bbox, largura, altura):
    """PNG transparente com a chuva do mes na area pedida (bbox em Web Mercator)."""
    oid = catalogo().get((ano, mes))
    if oid is None:
        return None
    r = _pedir("exportImage", f="image", bbox=bbox, bboxSR=3857, imageSR=3857, size=f"{largura},{altura}",
               format="tiff", pixelType="F32", noData=-9999, interpolation="RSP_BilinearInterpolation",
               renderingRule=json.dumps({"rasterFunction": "None"}),
               mosaicRule=json.dumps({"mosaicMethod": "esriMosaicLockRaster", "lockRasterIds": [oid]}))
    if "image" not in r.headers.get("content-type", ""):
        raise RuntimeError("o serviço não devolveu uma imagem")
    with rasterio.open(io.BytesIO(r.content)) as src:
        chuva = src.read(1)
    rgba = np.zeros(chuva.shape + (4,), dtype="uint8")
    for limite, cor in ESCALA:
        rgba[chuva >= limite] = [int(cor[i:i + 2], 16) for i in (1, 3, 5)] + [255]
    rgba[(chuva < 0) | (chuva > 5000)] = 0      # mar e celulas sem dados ficam transparentes
    saida = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(saida, "PNG")
    return saida.getvalue()
