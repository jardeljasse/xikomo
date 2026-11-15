"""
Uso e cobertura da terra de Mocambique, lido do servico publico da Esri
(Sentinel-2, 10 m, um mapa por ano, 2017 em diante), o mesmo do mapa no ArcGIS:
https://www.arcgis.com/home/item.html?id=cfcb7609de5f478eb7666240902d4d3d

Nao precisa de chave. Substitui mocambique_lulc.py (Planetary Computer, so ate 2023).

Correr:
    python mocambique_cobertura.py          # ano mais recente do servico
    python mocambique_cobertura.py 2020     # outro ano

Cria mocambique_lulc_<ano>.tif no mesmo formato que o resto do projecto espera.
Depois: python prototipo.py <ano>   e   python app/preparar_dados.py <ano>

Nota: cada celula de ~250 m recebe a classe de UM pixel de 10 m (amostragem, nao a
classe mais frequente). Para percentagens por distrito isto e fiel; para ver detalhe,
use a camada em directo do mapa da pagina.
"""
import io
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import requests
from rasterio.transform import from_origin

SERVICO = "https://ic.imagery1.arcgis.com/arcgis/rest/services/Sentinel2_10m_LandCover/ImageServer"
PASTA = Path(__file__).parent
CELULA = 0.0025          # graus, ~277 m
LADO = 2000              # pixeis por pedido (o servico aceita ate 4000)

info = requests.get(SERVICO, params={"f": "json"}, timeout=60).json()
ultimo = datetime.fromtimestamp(info["timeInfo"]["timeExtent"][1] / 1000, timezone.utc).year
ANO = int(sys.argv[1]) if len(sys.argv) > 1 else ultimo
if not 2017 <= ANO <= ultimo:
    sys.exit(f"O servico tem os anos 2017 a {ultimo}.")

# Caixa de Mocambique, alinhada a grelha
oeste, sul, leste, norte = gpd.read_file(PASTA / "moz_ADM2.geojson").to_crs(4326).total_bounds
oeste, sul = np.floor(oeste / CELULA) * CELULA, np.floor(sul / CELULA) * CELULA
leste, norte = np.ceil(leste / CELULA) * CELULA, np.ceil(norte / CELULA) * CELULA
largura, altura = int(round((leste - oeste) / CELULA)), int(round((norte - sul) / CELULA))
mapa = np.zeros((altura, largura), dtype="uint8")
tempo = f"{int(datetime(ANO, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)}," \
        f"{int(datetime(ANO, 12, 31, tzinfo=timezone.utc).timestamp() * 1000)}"
print(f"Ano {ANO}: grelha de {largura} x {altura} celulas em blocos de {LADO}")

pedidos = [(c, l) for l in range(0, altura, LADO) for c in range(0, largura, LADO)]
for n, (c0, l0) in enumerate(pedidos, 1):
    c1, l1 = min(c0 + LADO, largura), min(l0 + LADO, altura)
    caixa = (oeste + c0 * CELULA, norte - l1 * CELULA, oeste + c1 * CELULA, norte - l0 * CELULA)
    for tentativa in range(1, 5):
        try:
            r = requests.get(f"{SERVICO}/exportImage", timeout=240, params={
                "f": "image", "bbox": ",".join(f"{x:.6f}" for x in caixa), "bboxSR": 4326, "imageSR": 4326,
                "size": f"{c1 - c0},{l1 - l0}", "format": "tiff", "pixelType": "U8",
                "interpolation": "RSP_NearestNeighbor", "time": tempo, "noData": 0})
            r.raise_for_status()
            with rasterio.open(io.BytesIO(r.content)) as src:
                mapa[l0:l1, c0:c1] = src.read(1)
            break
        except (requests.RequestException, rasterio.errors.RasterioError) as erro:
            if tentativa == 4:
                sys.exit(f"Falhou o bloco {n}/{len(pedidos)}: {type(erro).__name__}")
            time.sleep(3 * tentativa)
    print(f"  bloco {n}/{len(pedidos)}")

saida = PASTA / f"mocambique_lulc_{ANO}.tif"
with rasterio.open(saida, "w", driver="GTiff", height=altura, width=largura, count=1, dtype="uint8",
                   crs="EPSG:4326", transform=from_origin(oeste, norte, CELULA, CELULA),
                   compress="deflate") as dst:
    dst.write(mapa, 1)
print(f"Guardado em {saida.name}")

# Resumo: percentagem de cada classe (so pixeis com classe conhecida; sem corte pela fronteira)
NOMES = {1: "Agua", 2: "Arvores", 4: "Vegetacao inundada", 5: "Culturas", 7: "Area construida",
         8: "Solo nu", 11: "Pastagem / mato"}
valores, contagem = np.unique(mapa[np.isin(mapa, list(NOMES))], return_counts=True)
total = contagem.sum()
print("\nClasse                 % da caixa (inclui parte dos paises vizinhos)")
for v, c in zip(valores, contagem):
    print(f"{NOMES[v]:<22} {100 * c / total:5.1f}")
