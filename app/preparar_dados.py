"""
Cria app/data/distritos.json: uma entrada por distrito com provincia, centro
(lat/lon) e perfil de cobertura da terra. Corre-se uma vez (ou quando a tabela
distritos_lulc_<ano>.csv mudar):

    python app/preparar_dados.py 2020
"""
import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

ANO = int(sys.argv[1]) if len(sys.argv) > 1 else 2020
RAIZ = Path(__file__).parent.parent
SAIDA = Path(__file__).parent / "data" / "distritos.json"

distritos = gpd.read_file(RAIZ / "moz_ADM2.geojson").to_crs(4326)[["shapeName", "geometry"]]
provincias = gpd.read_file(RAIZ / "moz_ADM1.geojson").to_crs(4326)[["shapeName", "geometry"]]
provincias = provincias.rename(columns={"shapeName": "provincia"})

pontos = distritos.copy()
pontos["geometry"] = pontos.representative_point()
# provincia mais proxima, para as ilhas (ex.: Ibo) nao ficarem sem provincia
junto = gpd.sjoin_nearest(pontos.to_crs(32736), provincias.to_crs(32736), how="left")
junto = junto[~junto.index.duplicated()]

lulc = pd.read_csv(RAIZ / f"distritos_lulc_{ANO}.csv").set_index("Distrito")
colunas = [c for c in lulc.columns if c.endswith("(%)")]

registos = []
for i, linha in distritos.iterrows():
    nome = linha["shapeName"]
    ponto = pontos.geometry[i]
    perfil = lulc.loc[nome] if nome in lulc.index else None
    registos.append(
        {
            "id": i,
            "distrito": nome,
            "provincia": junto.loc[i, "provincia"],
            "lat": round(ponto.y, 4),
            "lon": round(ponto.x, 4),
            "cobertura_ano": ANO,
            "cobertura": (
                {c.replace(" (%)", ""): float(perfil[c]) for c in colunas}
                if perfil is not None
                else None
            ),
        }
    )

registos.sort(key=lambda r: (r["provincia"], r["distrito"]))
SAIDA.parent.mkdir(exist_ok=True)
SAIDA.write_text(json.dumps(registos, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"{len(registos)} distritos guardados em {SAIDA}")

# Contornos simplificados para o mapa da pagina (distrito escolhido e a sua provincia)
from shapely.geometry import mapping


def contorno(geometria, tolerancia):
    g = mapping(geometria.simplify(tolerancia, preserve_topology=True))

    def arredondar(c):
        return [round(c[0], 4), round(c[1], 4)] if isinstance(c[0], (int, float)) else [arredondar(x) for x in c]
    return {"type": g["type"], "coordinates": arredondar(g["coordinates"])}


geo = {
    "distritos": {str(i): contorno(g, 0.003) for i, g in distritos.geometry.items()},
    "provincias": {n: contorno(g, 0.01) for n, g in zip(provincias["provincia"], provincias.geometry)},
}
(SAIDA.parent / "geo.json").write_text(json.dumps(geo), encoding="utf-8")
print(f"Contornos guardados em {SAIDA.parent / 'geo.json'}")
