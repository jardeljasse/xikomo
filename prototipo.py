"""
Prototipo Alert Farm App: perfil de uso e cobertura da terra por distrito.

Le o GeoTIFF criado por mocambique_lulc.py (a "base de dados"), cruza com os
distritos de Mocambique (geoBoundaries) e produz:
  - distritos_lulc_<ano>.csv     tabela: uma linha por distrito, % de cada classe
  - prototipo_mapa_<ano>.html    mapa interactivo (abrir no browser)

Correr (depois de `python mocambique_lulc.py <ano>`):
    python prototipo.py 2020               # cria a tabela e o mapa
    python prototipo.py 2020 Chokwe        # mostra tambem o perfil de um distrito

Nota: isto descreve o que cobre o solo hoje. Nao e ainda recomendacao de culturas.
"""
import sys
import unicodedata
from pathlib import Path

import folium
import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import requests
from rasterio.features import rasterize

ANO = int(sys.argv[1]) if len(sys.argv) > 1 else 2023
PROCURA = " ".join(sys.argv[2:])
PASTA = Path(__file__).parent
TIF = PASTA / f"mocambique_lulc_{ANO}.tif"

CLASSES = {
    1: ("Agua", "#419bdf"),
    2: ("Arvores", "#397d49"),
    4: ("Vegetacao inundada", "#7a87c6"),
    5: ("Culturas", "#e49635"),
    7: ("Area construida", "#c4281b"),
    8: ("Solo nu", "#a59b8f"),
    11: ("Pastagem / mato", "#e3e2c3"),
}
CODIGOS = sorted(CLASSES)
COLUNAS = [f"{CLASSES[c][0]} (%)" for c in CODIGOS]


def sem_acentos(texto):
    texto = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in texto if not unicodedata.combining(c)).lower()


def fronteiras(nivel):
    """Descarrega (uma vez) as fronteiras ADM1/ADM2 e guarda uma copia local."""
    ficheiro = PASTA / f"moz_{nivel}.geojson"
    if not ficheiro.exists():
        meta = requests.get(
            f"https://www.geoboundaries.org/api/current/gbOpen/MOZ/{nivel}/", timeout=60
        ).json()
        ficheiro.write_bytes(requests.get(meta["gjDownloadURL"], timeout=300).content)
    return gpd.read_file(ficheiro).to_crs(4326)


def leitura(linha):
    """Frases simples a partir das percentagens. Regras, nao um modelo."""
    notas = []
    if linha["Culturas (%)"] >= 15:
        notas.append("muita area ja cultivada")
    elif linha["Culturas (%)"] < 2:
        notas.append("pouca area cultivada visivel a esta resolucao")
    if linha["Arvores (%)"] >= 50:
        notas.append("dominado por floresta: abrir novos campos tem custo ambiental")
    if linha["Vegetacao inundada (%)"] + linha["Agua (%)"] >= 10:
        notas.append("zonas humidas ou alagadas importantes: risco de cheia")
    if linha["Area construida (%)"] >= 5:
        notas.append("distrito urbanizado")
    if linha["Solo nu (%)"] >= 2:
        notas.append("solo nu presente: risco de erosao")
    return "; ".join(notas) or "sem sinal forte nesta camada"


# 1) Ler a base de dados (GeoTIFF)
if not TIF.exists():
    sys.exit(f"Falta {TIF.name}. Correr primeiro: python mocambique_lulc.py {ANO}")
with rasterio.open(TIF) as src:
    lulc = src.read(1)
    transform = src.transform
    limites = src.bounds

# 2) Distritos, com o nome da provincia
distritos = fronteiras("ADM2")[["shapeName", "geometry"]].rename(
    columns={"shapeName": "Distrito"}
)
provincias = fronteiras("ADM1")[["shapeName", "geometry"]].rename(
    columns={"shapeName": "Provincia"}
)
pontos = distritos.copy()
pontos["geometry"] = pontos.representative_point()
# provincia mais proxima (e nao "dentro de"), para as ilhas como o Ibo nao ficarem sem provincia
junto = gpd.sjoin_nearest(pontos.to_crs(32736), provincias.to_crs(32736), how="left")
distritos["Provincia"] = junto[~junto.index.duplicated()]["Provincia"].values

# 3) Cada pixel fica com o numero do seu distrito
zonas = rasterize(
    [(g, i + 1) for i, g in enumerate(distritos.geometry)],
    out_shape=lulc.shape,
    transform=transform,
    dtype="int32",
)

# 4) Area de cada pixel em km2 (os pixeis em graus sao mais estreitos longe do equador)
RAIO_KM = 6371.0
dlon, dlat = abs(transform.a), abs(transform.e)
lat = transform.f + transform.e * (np.arange(lulc.shape[0]) + 0.5)
area_linha = (RAIO_KM * np.radians(dlat)) * (RAIO_KM * np.radians(dlon) * np.cos(np.radians(lat)))
area = np.repeat(area_linha[:, None], lulc.shape[1], axis=1)

# 5) Somar a area por distrito e por classe
n = len(distritos) + 1
tabela = pd.DataFrame({"Distrito": distritos["Distrito"], "Provincia": distritos["Provincia"]})
somas = {
    c: np.bincount(zonas.ravel(), weights=(area * (lulc == c)).ravel(), minlength=n)[1:]
    for c in CODIGOS
}
total = sum(somas.values())
tabela["Area (km2)"] = total.round(0)
for c, coluna in zip(CODIGOS, COLUNAS):
    tabela[coluna] = (100 * somas[c] / np.where(total > 0, total, np.nan)).round(1)
tabela["Classe dominante"] = [
    CLASSES[CODIGOS[i]][0] for i in np.argmax([somas[c] for c in CODIGOS], axis=0)
]
tabela["Leitura"] = tabela.apply(leitura, axis=1)

csv = PASTA / f"distritos_lulc_{ANO}.csv"
tabela.sort_values(["Provincia", "Distrito"]).to_csv(csv, index=False)
print(f"Tabela guardada em {csv.name} ({len(tabela)} distritos)")

# 6) Mapa interactivo: imagem de cobertura da terra + distritos clicaveis
passo = max(1, max(lulc.shape) // 3000)  # reduzir a imagem para o HTML nao ficar pesado
pequeno = lulc[::passo, ::passo]
rgba = np.zeros(pequeno.shape + (4,), dtype="uint8")
for c in CODIGOS:
    cor = CLASSES[c][1].lstrip("#")
    rgba[pequeno == c] = [int(cor[i : i + 2], 16) for i in (0, 2, 4)] + [255]
rgba[zonas[::passo, ::passo] == 0] = 0  # transparente fora do pais

mapa = folium.Map(location=[-18.7, 35.5], zoom_start=6, tiles="OpenStreetMap")
folium.raster_layers.ImageOverlay(
    rgba,
    bounds=[[limites.bottom, limites.left], [limites.top, limites.right]],
    mercator_project=True,
    opacity=0.8,
    name=f"Cobertura da terra {ANO}",
).add_to(mapa)

camada = gpd.GeoDataFrame(tabela, geometry=distritos.geometry.simplify(0.005), crs=4326)
campos = ["Distrito", "Provincia", "Area (km2)", "Classe dominante"] + COLUNAS + ["Leitura"]
folium.GeoJson(
    camada[campos + ["geometry"]],
    name="Distritos",
    style_function=lambda f: {"color": "#333333", "weight": 0.8, "fillOpacity": 0},
    highlight_function=lambda f: {"weight": 3, "color": "#000000"},
    tooltip=folium.GeoJsonTooltip(fields=["Distrito", "Provincia"]),
    popup=folium.GeoJsonPopup(fields=campos, max_width=350),
).add_to(mapa)

legenda = "".join(
    f'<div><span style="background:{cor};width:12px;height:12px;display:inline-block;'
    f'margin-right:6px;border:1px solid #666"></span>{nome}</div>'
    for nome, cor in CLASSES.values()
)
mapa.get_root().html.add_child(
    folium.Element(
        '<div style="position:fixed;bottom:24px;left:12px;z-index:9999;background:white;'
        'color:#222;padding:8px 10px;border:1px solid #999;border-radius:4px;'
        f'font:12px sans-serif"><b>Cobertura da terra {ANO}</b>{legenda}</div>'
    )
)
folium.LayerControl().add_to(mapa)
html = PASTA / f"prototipo_mapa_{ANO}.html"
mapa.save(html)
print(f"Mapa guardado em {html.name} (abrir no browser, clicar num distrito)")

# 7) Perfil de um distrito, se foi pedido
if PROCURA:
    achados = tabela[tabela["Distrito"].map(sem_acentos).str.contains(sem_acentos(PROCURA))]
    if achados.empty:
        print(f'\nNenhum distrito com "{PROCURA}" no nome.')
    for _, d in achados.iterrows():
        print(f"\n{d['Distrito']} ({d['Provincia']}) - {d['Area (km2)']:.0f} km2")
        for coluna in COLUNAS:
            print(f"  {coluna:<24} {d[coluna]:5.1f}")
        print(f"  Leitura: {d['Leitura']}")
