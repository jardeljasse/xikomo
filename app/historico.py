"""
Leituras a partir do historico de chuva mensal por distrito (tabela chuva_mensal,
NASA GLDAS, desde 2000; criada por mocambique_precipitacao.py).

Modelo: FREQUENCIA HISTORICA. Nao ha aprendizagem automatica aqui. Para uma data de
sementeira, olha-se para o que aconteceu nessa mesma data em cada ano do registo
e conta-se quantas vezes a epoca foi seca, normal ou chuvosa. Ver MODELOS.md.
"""
import calendar
import csv
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from . import db

MODELO = "frequencia-historica-v1"
NOME_MES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
            "agosto", "setembro", "outubro", "novembro", "dezembro"]
_series = {}
_csv = None

# Classes de gravidade da epoca: chuva dos 4 meses depois da sementeira, em
# percentagem do normal (mediana) do proprio distrito. Limites escolhidos por nos.
CLASSES = [
    dict(id="seca_grave", nome="Seca grave", nivel="alto", ate=60,
         acontece="A chuva fica abaixo de 60% do normal. As sementes não germinam bem, "
                  "o milho seca antes de encher o grão e os pastos e poços secam.",
         fazer="Semear só culturas muito resistentes (mexoeira, mapira, mandioca, feijão nhemba), "
               "em área menor e nas baixas húmidas. Guardar semente e alimento. "
               "Falar com o técnico de extensão sobre apoio de emergência."),
    dict(id="seca_moderada", nome="Seca moderada", nivel="medio", ate=80,
         acontece="A chuva fica entre 60% e 80% do normal. Há períodos secos a meio da época "
                  "e a colheita das culturas exigentes em água baixa.",
         fazer="Preferir variedades de ciclo curto e culturas tolerantes à seca. Cobrir o solo "
               "com palha para guardar humidade. Semear em duas datas para repartir o risco."),
    dict(id="normal", nome="Normal", nivel="bom", ate=120,
         acontece="A chuva fica perto do habitual (80% a 120% do normal). "
                  "É a época para a qual as recomendações desta página estão pensadas.",
         fazer="Seguir a rotação recomendada e o calendário habitual de sementeira."),
    dict(id="chuva_moderada", nome="Chuva acima do normal", nivel="medio", ate=150,
         acontece="A chuva fica entre 120% e 150% do normal. Os solos encharcam nas zonas baixas, "
                  "há mais doenças de fungos e os nutrientes são lavados.",
         fazer="Semear em camalhões ou em terreno alto, abrir valas de drenagem e evitar as baixas. "
               "O arroz e a batata-doce aguentam melhor o excesso de água."),
    dict(id="chuva_grave", nome="Chuva excessiva grave", nivel="alto", ate=None,
         acontece="A chuva passa de 150% do normal. Há risco de cheias, de perda total de "
                  "sementeiras e de estradas cortadas.",
         fazer="Não semear junto a rios nem em baixas. Guardar semente em lugar alto e seco. "
               "Seguir os avisos do INAM e das autoridades locais."),
]


def classe(pct):
    for c in CLASSES:
        if c["ate"] is None or pct < c["ate"]:
            return c


def serie(distrito_id):
    """{(ano, mes): mm} do distrito. Base de dados; se falhar, o CSV local."""
    global _csv
    if distrito_id in _series:
        return _series[distrito_id]
    linhas = db.executar("SELECT ano, mes, chuva_mm FROM chuva_mensal WHERE distrito_id = %s",
                         (distrito_id,), ler=True)
    if not linhas:
        if _csv is None:
            _csv = {}
            ficheiro = Path(__file__).parent.parent / "chuva_mensal_gldas.csv"
            if ficheiro.exists():
                with open(ficheiro, encoding="utf-8") as f:
                    for l in csv.DictReader(f):
                        _csv.setdefault(int(l["distrito_id"]), []).append(
                            (int(l["ano"]), int(l["mes"]), float(l["chuva_mm"])))
        linhas = _csv.get(distrito_id, [])
    if not linhas:
        return None
    _series[distrito_id] = {(a, m): v for a, m, v in linhas}
    return _series[distrito_id]


def chuva_periodo(s, inicio, dias):
    """Chuva (mm) que caiu de facto entre `inicio` e `inicio + dias`. None se faltar algum mes."""
    total, dia, fim = 0.0, inicio, inicio + timedelta(days=dias)
    while dia < fim:
        dias_mes = calendar.monthrange(dia.year, dia.month)[1]
        seguinte = min(fim, date(dia.year, dia.month, dias_mes) + timedelta(days=1))
        valor = s.get((dia.year, dia.month))
        if valor is None:
            return None
        total += valor * (seguinte - dia).days / dias_mes
        dia = seguinte
    return total


def epocas(s, dia, dias):
    """[(ano de sementeira, chuva mm)] para a mesma data de sementeira em cada ano do registo."""
    lista = []
    for ano in range(min(a for a, _ in s), max(a for a, _ in s) + 1):
        total = chuva_periodo(s, date(ano, dia.month, min(dia.day, 28)), dias)
        if total is not None:
            lista.append((ano, total))
    return lista


def rotulo_epoca(ano, dia, dias):
    fim = date(ano, dia.month, min(dia.day, 28)) + timedelta(days=dias)
    return f"{ano}/{str(fim.year)[2:]}" if fim.year != ano else str(ano)


def mediana(valores):
    v = sorted(valores)
    n = len(v)
    return (v[n // 2] + v[(n - 1) // 2]) / 2


def percentil(valores, p):
    v = sorted(valores)
    return v[min(len(v) - 1, max(0, round(p / 100 * (len(v) - 1))))]


def mes_normal(s, mes):
    """Chuva habitual de um mes do calendario: media e intervalo em 8 de cada 10 anos."""
    valores = [v for (a, m), v in s.items() if m == mes]
    return {"media": round(sum(valores) / len(valores)), "baixo": round(percentil(valores, 10)),
            "alto": round(percentil(valores, 90)), "anos": len(valores)}


def inicio_das_chuvas(s):
    """Mes em que as chuvas comecam: o primeiro, de setembro a marco, com 50 mm ou mais.
    Com dados mensais so se sabe o mes, nao o dia."""
    contagem, epocas_vistas = Counter(), 0
    for ano in range(min(a for a, _ in s), max(a for a, _ in s)):
        meses = [(ano, m) for m in (9, 10, 11, 12)] + [(ano + 1, m) for m in (1, 2, 3)]
        if any(k not in s for k in meses):
            continue
        epocas_vistas += 1
        primeiro = next((m for (a, m) in meses if s[(a, m)] >= 50), None)
        contagem[primeiro] += 1
    if not epocas_vistas:
        return None
    ordem = [9, 10, 11, 12, 1, 2, 3, None]
    return {
        "epocas": epocas_vistas,
        "mais_comum": NOME_MES[contagem.most_common(1)[0][0] - 1] if contagem.most_common(1)[0][0] else None,
        "por_mes": [{"mes": NOME_MES[m - 1] if m else "não começaram até março", "vezes": contagem[m]}
                    for m in ordem if contagem[m]],
    }


def resumo(s, dia, dias=120):
    """Gravidade da epoca (4 meses depois da sementeira) em cada ano do registo."""
    lista = epocas(s, dia, dias)
    if len(lista) < 10:
        return None
    normal = mediana([t for _, t in lista])
    anos = []
    for ano, total in lista:
        pct = 100 * total / normal if normal > 0 else 100
        c = classe(pct)
        anos.append({"ano": ano, "epoca": rotulo_epoca(ano, dia, dias), "chuva_mm": round(total),
                     "pct_normal": round(pct), "classe": c["nome"], "classe_id": c["id"], "nivel": c["nivel"]})
    contagem = Counter(a["classe_id"] for a in anos)
    return {
        "modelo": MODELO,
        "n_epocas": len(anos),
        "periodo": f"{anos[0]['epoca']} a {anos[-1]['epoca']}",
        "normal_mm": round(normal),
        "classes": [{"id": c["id"], "nome": c["nome"], "nivel": c["nivel"], "vezes": contagem[c["id"]],
                     "pct": round(100 * contagem[c["id"]] / len(anos)),
                     "acontece": c["acontece"], "fazer": c["fazer"]} for c in CLASSES],
        "anos": anos,
        "ultima": anos[-1],
        "inicio_chuvas": inicio_das_chuvas(s),
    }
