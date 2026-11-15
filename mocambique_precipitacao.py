"""
Chuva mensal (NASA GLDAS, ~28 km, desde 2000) por distrito de Mocambique, lida do
servico ArcGIS por tras do mapa "Monthly Precipitation" da Esri:
https://www.arcgis.com/home/item.html?id=01fa55f171eb48a7ac9c460c0339e6c1

Precisa de ARCGIS_API_KEY no ficheiro .env (o servico exige conta ArcGIS).

Correr:
    python mocambique_precipitacao.py          # todos os meses
    python mocambique_precipitacao.py 2020     # so um ano (para testar)

Produz chuva_mensal_gldas.csv e, se houver DATABASE_URL no .env, preenche a
tabela chuva_mensal na base de dados.
"""
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import requests
from dotenv import load_dotenv
from rasterio.features import rasterize
from rasterio.transform import from_origin

PASTA = Path(__file__).parent
load_dotenv(PASTA / ".env")
CHAVE = os.environ.get("ARCGIS_API_KEY")
SERVICO = "https://earthobs2.arcgis.com/arcgis/rest/services/GLDAS_Precipitation/ImageServer"
VARIAVEL = "Rainfall (mm)"
SO_ANO = int(sys.argv[1]) if len(sys.argv) > 1 else None

CELULA = 0.25                          # resolucao do GLDAS, em graus
OESTE, SUL, LESTE, NORTE = 30.0, -27.0, 41.0, -10.25   # caixa de Mocambique, alinhada a 0.25
LARGURA, ALTURA = int((LESTE - OESTE) / CELULA), int((NORTE - SUL) / CELULA)
FINO = 10                              # cada celula e dividida em 10x10 para pesar pela area

if not CHAVE:
    sys.exit("Falta ARCGIS_API_KEY no ficheiro .env")
sessao = requests.Session()


def pedir(caminho, **parametros):
    r = sessao.get(f"{SERVICO}/{caminho}", params={"token": CHAVE, **parametros}, timeout=120)
    r.raise_for_status()
    return r


# 1) Catalogo: um raster por mes (o servico devolve 100 linhas de cada vez)
meses, inicio = [], 0
while True:
    pagina = pedir(
        "query", f="json", where=f"Variable = '{VARIAVEL}' AND Tag = 'Actual'",
        outFields="OBJECTID,StdTime", returnGeometry="false", orderByFields="StdTime",
        resultOffset=inicio, resultRecordCount=100,
    ).json()
    if "error" in pagina:
        sys.exit(f"O servico recusou o pedido: {pagina['error'].get('message')}")
    linhas = pagina.get("features", [])
    for linha in linhas:
        dia = datetime.fromtimestamp(linha["attributes"]["StdTime"] / 1000, timezone.utc)
        meses.append((linha["attributes"]["OBJECTID"], dia.year, dia.month))
    if not linhas:
        break
    inicio += len(linhas)
if SO_ANO:
    meses = [m for m in meses if m[1] == SO_ANO]
print(f"{len(meses)} meses a ler ({meses[0][1]}-{meses[0][2]:02d} a {meses[-1][1]}-{meses[-1][2]:02d})")

# 2) Distritos numa grelha fina, para a media de cada distrito ser pesada pela area
distritos = json.loads((PASTA / "app" / "data" / "distritos.json").read_text(encoding="utf-8"))
nomes = {d["distrito"]: d["id"] for d in distritos}
formas = gpd.read_file(PASTA / "moz_ADM2.geojson").to_crs(4326)
zonas = rasterize(
    [(g, nomes[n] + 1) for g, n in zip(formas.geometry, formas["shapeName"])],
    out_shape=(ALTURA * FINO, LARGURA * FINO),
    transform=from_origin(OESTE, NORTE, CELULA / FINO, CELULA / FINO),
    all_touched=True, dtype="int32",
).ravel()
contagem = np.bincount(zonas, minlength=len(distritos) + 1)


def preencher(a):
    """Celulas sem dados (mar) recebem o valor da celula valida vizinha, para os
    distritos costeiros e as ilhas nao ficarem vazios."""
    a = a.copy()
    for _ in range(6):
        falta = np.isnan(a)
        if not falta.any():
            break
        b = np.pad(a, 1, constant_values=np.nan)
        viz = np.stack([b[:-2, 1:-1], b[2:, 1:-1], b[1:-1, :-2], b[1:-1, 2:]])
        validos = (~np.isnan(viz)).sum(0)
        media = np.where(validos > 0, np.nansum(viz, 0) / np.maximum(validos, 1), np.nan)
        a[falta] = media[falta]
    return a


# 3) Um pedido por mes: imagem de Mocambique -> media por distrito
registos = []
for n, (oid, ano, mes) in enumerate(meses, 1):
    r = pedir(
        "exportImage", f="image", bbox=f"{OESTE},{SUL},{LESTE},{NORTE}", bboxSR=4326, imageSR=4326,
        size=f"{LARGURA},{ALTURA}", format="tiff", pixelType="F32", noData=-9999,
        interpolation="RSP_NearestNeighbor",
        renderingRule=json.dumps({"rasterFunction": "None"}),
        mosaicRule=json.dumps({"mosaicMethod": "esriMosaicLockRaster", "lockRasterIds": [oid]}),
    )
    if "image" not in r.headers.get("content-type", ""):
        sys.exit(f"Erro em {ano}-{mes:02d}: {r.text[:200].replace(CHAVE, '<chave>')}")
    with rasterio.open(io.BytesIO(r.content)) as src:
        chuva = src.read(1).astype("float64")
    chuva[(chuva < 0) | (chuva > 5000)] = np.nan
    cheio = np.nan_to_num(preencher(chuva), nan=0.0)
    fino = np.repeat(np.repeat(cheio, FINO, axis=0), FINO, axis=1).ravel()
    medias = np.bincount(zonas, weights=fino, minlength=len(distritos) + 1) / np.maximum(contagem, 1)
    for d in distritos:
        valor = medias[d["id"] + 1]
        if contagem[d["id"] + 1] == 0:
            # distrito sem celulas proprias (ilha coberta por um vizinho): usa a celula do seu centro
            valor = cheio[int((NORTE - d["lat"]) / CELULA), int((d["lon"] - OESTE) / CELULA)]
        registos.append((d["id"], ano, mes, round(float(valor), 1)))
    if n % 24 == 0 or n == len(meses):
        print(f"  {n}/{len(meses)} meses")

tabela = pd.DataFrame(registos, columns=["distrito_id", "ano", "mes", "chuva_mm"])
por_id = {d["id"]: d for d in distritos}
tabela.insert(1, "distrito", tabela["distrito_id"].map(lambda i: por_id[i]["distrito"]))
tabela.insert(2, "provincia", tabela["distrito_id"].map(lambda i: por_id[i]["provincia"]))
csv = PASTA / "chuva_mensal_gldas.csv"
if not SO_ANO:
    tabela.to_csv(csv, index=False)
    print(f"Tabela guardada em {csv.name} ({len(tabela)} linhas)")

# 4) Base de dados
if os.environ.get("DATABASE_URL") and not os.environ.get("SEM_BD"):
    import psycopg

    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=20) as ligacao:
        ligacao.execute("""
            CREATE TABLE IF NOT EXISTS chuva_mensal (
                distrito_id integer REFERENCES distritos(id),
                ano         integer,
                mes         integer CHECK (mes BETWEEN 1 AND 12),
                chuva_mm    double precision,
                fonte       text DEFAULT 'NASA GLDAS (Esri Living Atlas)',
                PRIMARY KEY (distrito_id, ano, mes)
            )""")
        with ligacao.cursor() as cur:
            cur.execute("CREATE TEMP TABLE nova (distrito_id integer, ano integer, mes integer, chuva_mm double precision)")
            with cur.copy("COPY nova FROM STDIN") as copia:
                for linha in registos:
                    copia.write_row(linha)
            cur.execute("""
                INSERT INTO chuva_mensal (distrito_id, ano, mes, chuva_mm)
                SELECT * FROM nova
                ON CONFLICT (distrito_id, ano, mes) DO UPDATE SET chuva_mm = EXCLUDED.chuva_mm""")
        total = ligacao.execute("SELECT count(*) FROM chuva_mensal").fetchone()[0]
    print(f"Base de dados: tabela chuva_mensal com {total} linhas")

# 5) Resumo: chuva anual media por provincia
anual = tabela.groupby(["provincia", "distrito", "ano"])["chuva_mm"].sum().reset_index()
completos = anual[anual["ano"].isin(tabela.groupby("ano")["mes"].nunique().loc[lambda s: s == 12].index)]
if len(completos):
    print("\nChuva anual media por provincia (mm)")
    print(completos.groupby("provincia")["chuva_mm"].mean().round(0).sort_values().to_string())
