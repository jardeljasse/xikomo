"""
Treina o modelo sazonal: probabilidade de a epoca ser seca, normal ou chuvosa,
a partir do indice do El Nino (ONI, NOAA) e da localizacao do distrito.

Alvo:      chuva dos 4 meses a partir do mes de sementeira, em % do normal do
           distrito (mediana 2000-2026, NASA GLDAS). Classes: seca (< 80%),
           normal (80-120%), chuvosa (> 120%).
Entradas:  ONI e a sua tendencia, conhecidos 2 a 4 meses antes da sementeira,
           cruzados com a latitude e a longitude do distrito.
Ambito:    sementeiras de outubro a janeiro (a epoca das chuvas).
Modelos:   compara regressao logistica e gradient boosting; fica o que errar menos.
Validacao: deixa-se uma epoca inteira de fora de cada vez e compara-se com a
           frequencia historica da regiao, que e o metodo que ja existia.

Correr (depois de mocambique_precipitacao.py):
    python treinar_modelo.py

Produz app/data/oni.json, app/data/modelo_epoca.joblib e app/data/modelo_epoca.json.
"""
import json
import warnings
from datetime import date
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import requests
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from app.previsao_epoca import ANTECEDENCIAS, CLASSES, ENTRADAS, MESES_VALIDOS, entradas, recuar, regiao

warnings.filterwarnings("ignore")
PASTA = Path(__file__).parent
DADOS = PASTA / "app" / "data"

# 1) Indice do El Nino (ONI): media de 3 meses, atribuida ao mes do meio
texto = requests.get("https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt", timeout=60).text
MEIO = {"DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6,
        "JJA": 7, "JAS": 8, "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12}
oni = {}
for linha in texto.splitlines()[1:]:
    epoca, ano, _, anomalia = linha.split()
    oni[(int(ano), MEIO[epoca])] = float(anomalia)
ultimo = max(oni)
(DADOS / "oni.json").write_text(json.dumps(
    {"fonte": "NOAA CPC, Oceanic Nino Index", "obtido_em": date.today().isoformat(),
     "valores": [[a, m, v] for (a, m), v in sorted(oni.items()) if a >= 1999]}), encoding="utf-8")
print(f"ONI ate {ultimo[1]:02d}/{ultimo[0]}: {oni[ultimo]:+.2f}")

# 2) Alvo: chuva de 4 meses por distrito, ano e mes de sementeira
chuva = pd.read_csv(PASTA / "chuva_mensal_gldas.csv")
distritos = {d["id"]: d for d in json.loads((DADOS / "distritos.json").read_text(encoding="utf-8"))}
chuva["k"] = chuva["ano"] * 12 + chuva["mes"] - 1
larga = chuva.pivot(index="k", columns="distrito_id", values="chuva_mm").sort_index()
soma4 = larga.rolling(4).sum().shift(-3).dropna(how="all")   # 4 meses que comecam em k
linhas = []
for k, linha in soma4.iterrows():
    ano, mes = k // 12, k % 12 + 1
    if mes not in MESES_VALIDOS:
        continue
    for n in ANTECEDENCIAS:
        o, o3 = oni.get(recuar(ano, mes, n)), oni.get(recuar(ano, mes, n + 3))
        if o is None or o3 is None:
            continue
        for distrito_id, total in linha.items():
            d = distritos[distrito_id]
            # epoca agricola (julho a junho): um El Nino inteiro fica no mesmo grupo de validacao
            linhas.append((distrito_id, ano if mes >= 7 else ano - 1, mes, total, d["lat"],
                           *entradas(o, o - o3, d["lat"], d["lon"], n)))
dados = pd.DataFrame(linhas, columns=["distrito_id", "epoca", "mes", "chuva4", "lat_d", *ENTRADAS]).dropna()
dados["normal_mm"] = dados.groupby(["distrito_id", "mes"])["chuva4"].transform("median")
dados = dados[dados["normal_mm"] >= 50].reset_index(drop=True)
dados["pct"] = 100 * dados["chuva4"] / dados["normal_mm"]
dados["classe"] = np.select([dados["pct"] < 80, dados["pct"] > 120], [0, 2], default=1)
dados["regiao"] = dados["lat_d"].map(regiao)
print(f"{len(dados)} exemplos, {dados['epoca'].nunique()} epocas, {dados['distrito_id'].nunique()} distritos")

CANDIDATOS = {
    # poucas epocas independentes (cerca de 27): os modelos tem de ser simples
    "regressao-logistica-enso-v1": lambda: make_pipeline(
        StandardScaler(), LogisticRegression(C=0.01, max_iter=500)),
    "gradient-boosting-enso-v1": lambda: HistGradientBoostingClassifier(
        max_iter=100, learning_rate=0.05, max_leaf_nodes=6, min_samples_leaf=1500,
        l2_regularization=1.0, random_state=0),
}


def brier(prob, real):
    """Erro quadratico medio das probabilidades (0 = perfeito)."""
    return float(((prob - np.eye(3)[real]) ** 2).sum(axis=1).mean())


# 3) Validacao: uma epoca de fora de cada vez, contra a frequencia historica da regiao
X, y = dados[ENTRADAS].to_numpy(), dados["classe"].to_numpy()
grupos, regioes = dados["epoca"].to_numpy(), dados["regiao"].to_numpy()
prob = {nome: np.zeros((len(dados), 3)) for nome in CANDIDATOS}
prob_base = np.zeros((len(dados), 3))
for treino, teste in LeaveOneGroupOut().split(X, y, grupos):
    for nome, criar in CANDIDATOS.items():
        prob[nome][teste] = criar().fit(X[treino], y[treino]).predict_proba(X[teste])
    for r in ("sul", "centro", "norte"):
        t, e = regioes[treino] == r, regioes[teste] == r
        prob_base[teste[e]] = np.bincount(y[treino][t], minlength=3) / t.sum()


def medir(p, mascara):
    m, b, r = p[mascara], prob_base[mascara], y[mascara]
    return {
        "exemplos": int(mascara.sum()),
        "acerto_modelo": round(float((m.argmax(1) == r).mean()), 3),
        "acerto_frequencia": round(float((b.argmax(1) == r).mean()), 3),
        "brier_modelo": round(brier(m, r), 4),
        "brier_frequencia": round(brier(b, r), 4),
        # ganho > 0: o modelo erra menos nas probabilidades do que a frequencia historica
        "ganho": round(1 - brier(m, r) / brier(b, r), 3),
    }


tudo = np.ones(len(dados), dtype=bool)
resultados = {}
print("\nValidacao (ganho > 0: melhor do que a frequencia historica)")
for nome, p in prob.items():
    resultados[nome] = {
        "geral": medir(p, tudo),
        "por_regiao": {r: medir(p, regioes == r) for r in ("sul", "centro", "norte")},
        "epocas_em_que_ganhou": int(sum(
            brier(p[grupos == e], y[grupos == e]) < brier(prob_base[grupos == e], y[grupos == e])
            for e in np.unique(grupos))),
    }
    v = resultados[nome]
    print(f"  {nome}: ganho geral {v['geral']['ganho']:+.3f}; "
          + "; ".join(f"{r} {m['ganho']:+.3f}" for r, m in v["por_regiao"].items())
          + f"; melhor em {v['epocas_em_que_ganhou']} de {len(np.unique(grupos))} epocas")

# 4) Fica o modelo com mais ganho. Depois calibra-se: mistura-se a probabilidade do modelo
#    com a frequencia historica, no peso que menos erra na validacao. Com tao poucas epocas
#    de El Nino forte no registo, o modelo sozinho da probabilidades demasiado extremas.
escolhido = max(resultados, key=lambda n: resultados[n]["geral"]["ganho"])
pesos = np.round(np.arange(0, 1.01, 0.05), 2)
peso = float(min(pesos, key=lambda a: brier(a * prob[escolhido] + (1 - a) * prob_base, y)))
calibrado = peso * prob[escolhido] + (1 - peso) * prob_base
resultados[escolhido] = {
    "geral": medir(calibrado, tudo),
    "por_regiao": {r: medir(calibrado, regioes == r) for r in ("sul", "centro", "norte")},
    "epocas_em_que_ganhou": int(sum(
        brier(calibrado[grupos == e], y[grupos == e]) < brier(prob_base[grupos == e], y[grupos == e])
        for e in np.unique(grupos))),
    "sem_calibrar": resultados[escolhido],
}
v = resultados[escolhido]
print(f"\nCalibracao: peso do modelo {peso:.2f}, peso da frequencia historica {1 - peso:.2f}")
print(f"  depois de calibrar: ganho geral {v['geral']['ganho']:+.3f}; "
      + "; ".join(f"{r} {m['ganho']:+.3f}" for r, m in v["por_regiao"].items())
      + f"; melhor em {v['epocas_em_que_ganhou']} de {len(np.unique(grupos))} epocas")
forte = dados["oni"].to_numpy() >= 1.5
for r in ("sul", "centro"):
    m = forte & (regioes == r)
    print(f"  El Nino forte, {r}: o modelo calibrado dizia {100 * calibrado[m][:, 0].mean():.0f}% de seca; "
          f"aconteceu em {100 * (y[m] == 0).mean():.0f}% dos casos ({len(np.unique(grupos[m]))} epocas)")
modelo = CANDIDATOS[escolhido]().fit(X, y)
joblib.dump(modelo, DADOS / "modelo_epoca.joblib")
(DADOS / "modelo_epoca.json").write_text(json.dumps({
    "nome": escolhido,
    "treinado_em": date.today().isoformat(),
    "entradas": ENTRADAS, "classes": CLASSES,
    "meses_de_sementeira": list(MESES_VALIDOS), "antecedencias_meses": list(ANTECEDENCIAS),
    "exemplos": len(dados), "epocas": int(dados["epoca"].nunique()),
    "oni_mais_recente": {"ano": ultimo[0], "mes": ultimo[1], "valor": oni[ultimo]},
    "oni_intervalo_treino": [float(dados["oni"].min()), float(dados["oni"].max())],
    "peso_do_modelo": peso,
    "frequencia_por_regiao": {r: [round(float(x), 3) for x in np.bincount(y[regioes == r], minlength=3)
                                  / (regioes == r).sum()] for r in ("sul", "centro", "norte")},
    "validacao": resultados[escolhido],
    "todos_os_candidatos": resultados,
}, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"\nEscolhido: {escolhido}. Guardado em app/data/modelo_epoca.joblib e modelo_epoca.json")
