"""
Uso e cobertura da terra (Sentinel-2 10 m, Esri / Impact Observatory)
recortado para Mocambique, lido pela API STAC do Microsoft Planetary Computer.

Instalar:
    pip install pystac-client planetary-computer odc-stac geopandas rioxarray matplotlib requests

Correr:
    python mocambique_lulc.py            # ano 2023 por defeito
    python mocambique_lulc.py 2020       # outro ano
"""
import sys

import geopandas as gpd
import matplotlib.pyplot as plt
import odc.stac
import planetary_computer
import pystac_client
import requests
import rioxarray  # noqa: F401  (activa o acessor .rio)
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch

ANO = int(sys.argv[1]) if len(sys.argv) > 1 else 2023
RESOLUCAO_GRAUS = 0.0025  # ~250 m (0.01 = ~1 km). Pais inteiro a 10 m nao cabe na memoria.

# 1) Fronteira de Mocambique (geoBoundaries, dados abertos)
meta = requests.get(
    "https://www.geoboundaries.org/api/current/gbOpen/MOZ/ADM0/", timeout=60
).json()
moz = gpd.read_file(meta["simplifiedGeometryGeoJSON"]).to_crs(4326)
bbox = list(moz.total_bounds)  # [oeste, sul, leste, norte]

# 2) Ligar a API e procurar as imagens que tocam em Mocambique
catalogo = pystac_client.Client.open(
    "https://planetarycomputer.microsoft.com/api/stac/v1",
    modifier=planetary_computer.sign_inplace,  # assina os links automaticamente
)
itens = catalogo.search(
    collections=["io-lulc-annual-v02"],
    bbox=bbox,
    datetime=f"{ANO}-06-01",  # meio do ano: um intervalo a comecar em 01-01 apanha tambem o ano anterior
).item_collection()
print(f"{len(itens)} mosaicos encontrados para {ANO}")

# 3) Carregar so a area de Mocambique, ja reamostrada para ~1 km
dados = odc.stac.load(
    itens,
    bands=["data"],
    bbox=bbox,
    crs="EPSG:4326",
    resolution=RESOLUCAO_GRAUS,
    resampling="mode",  # classe mais frequente em cada celula
    chunks={},
)
mapa = dados["data"].max("time").compute()

# 3b) Guardar os dados (nao a figura) em GeoTIFF: e a base de dados do prototipo.
# Guarda-se antes do corte, para os distritos de fronteira ficarem completos.
mapa.fillna(0).astype("uint8").rio.write_crs(4326).rio.to_raster(
    f"mocambique_lulc_{ANO}.tif", compress="deflate"
)
print(f"Dados guardados em mocambique_lulc_{ANO}.tif")

# 4) Cortar pela fronteira (fora do pais fica 0 = sem dados)
mapa = mapa.rio.write_crs(4326).rio.clip(moz.geometry, moz.crs, drop=True)

# 5) Desenhar
CLASSES = {
    1: ("Agua", "#419bdf"),
    2: ("Arvores", "#397d49"),
    4: ("Vegetacao inundada", "#7a87c6"),
    5: ("Culturas", "#e49635"),
    7: ("Area construida", "#c4281b"),
    8: ("Solo nu", "#a59b8f"),
    11: ("Pastagem / mato", "#e3e2c3"),
}
codigos = sorted(CLASSES)
cmap = ListedColormap([CLASSES[c][1] for c in codigos])
norm = BoundaryNorm([c - 0.5 for c in codigos] + [codigos[-1] + 0.5], cmap.N)

fig, ax = plt.subplots(figsize=(7, 10))
mapa.where(mapa.isin(codigos)).plot(ax=ax, cmap=cmap, norm=norm, add_colorbar=False)
moz.boundary.plot(ax=ax, color="black", linewidth=0.6)
ax.legend(
    handles=[Patch(color=CLASSES[c][1], label=CLASSES[c][0]) for c in codigos],
    loc="lower right",
    fontsize=8,
)
ax.set_title(f"Uso e cobertura da terra - Mocambique {ANO}")
ax.set_xlabel("Longitude")
ax.set_ylabel("Latitude")
fig.savefig(f"mocambique_lulc_{ANO}.png", dpi=150, bbox_inches="tight")
print(f"Mapa guardado em mocambique_lulc_{ANO}.png")

# 6) De mapa para tabela: percentagem de cada classe no pais
validos = mapa.where(mapa.isin(codigos))
total = int(validos.count())
print("\nClasse                 % do pais")
for c in codigos:
    pct = 100 * int((validos == c).sum()) / total
    print(f"{CLASSES[c][0]:<22} {pct:5.1f}")
