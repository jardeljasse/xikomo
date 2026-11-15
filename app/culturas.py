"""
Catalogo de culturas, pontuacao de compatibilidade e rotacoes.

Isto sao REGRAS, nao um modelo treinado. As gamas de temperatura, agua e pH sao
valores aproximados no estilo FAO ECOCROP e tem de ser confirmadas contra a
base ECOCROP e por tecnicos locais antes de qualquer uso real.

Cada gama e (minimo absoluto, optimo minimo, optimo maximo, maximo absoluto).
"chuva" e a agua necessaria durante o ciclo (mm), nao por ano.
"seca", "renda" e "solo" (0 a 1) servem para a prioridade do agricultor.
"""
from datetime import date, timedelta

import unicodedata

from . import ciclo, db, historico, previsao_epoca

# O catalogo (culturas, pragas e estrategias de rotacao) vive na base de dados: tabelas
# culturas, pragas e rotacoes. Sem base de dados, le-se app/data/catalogo.json.
_catalogo, ORIGEM_CATALOGO = db.carregar_catalogo()
CULTURAS = [{**c, **{k: tuple(c[k]) for k in ("ciclo", "temp", "chuva", "ph", "textura")}}
            for c in _catalogo["culturas"]]
ANUAIS = [c for c in CULTURAS if c["tipo"] != "permanente"]
PERMANENTES = [c for c in CULTURAS if c["tipo"] == "permanente"]
POR_ID = {c["id"]: c for c in CULTURAS}
# Pragas do formulario: id -> (nome, culturas que mais ataca)
PRAGAS = {p["id"]: (p["nome"], tuple(p["culturas"])) for p in _catalogo["pragas"]}
# Outros nomes que as pessoas escrevem, para reconhecer texto livre.
SINONIMOS_CULTURAS = {c["id"]: tuple(c["sinonimos"] or ()) for c in CULTURAS}
SINONIMOS_PRAGAS = {p["id"]: tuple(p["sinonimos"] or ()) for p in _catalogo["pragas"]}
ROTACOES = _catalogo["rotacoes"]


def _simples(texto):
    texto = unicodedata.normalize("NFKD", str(texto).lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() else " " for c in texto).split())


def reconhecer(texto, tipo):
    """Texto livre -> id de uma cultura ('cultura') ou praga ('praga') conhecida, ou None."""
    limpo = _simples(texto)
    if not limpo:
        return None
    if tipo == "cultura":
        nomes = {c["id"]: (_simples(c["nome"]),) + SINONIMOS_CULTURAS.get(c["id"], ()) for c in CULTURAS}
    else:
        nomes = {k: (_simples(v[0]),) + SINONIMOS_PRAGAS.get(k, ()) for k, v in PRAGAS.items()}
    # primeiro o nome exacto; depois o nome mais comprido contido no texto
    for chave, lista in nomes.items():
        if limpo in lista:
            return chave
    achados = [(len(n), chave) for chave, lista in nomes.items() for n in lista
               if f" {n} " in f" {limpo} " or f" {n}s " in f" {limpo} "]
    return max(achados)[1] if achados else None


PRIORIDADES = {
    "equilibrio": "Equilíbrio",
    "agua": "Poupar água",
    "renda": "Mais rendimento",
    "solo": "Melhorar o solo",
}
NOME_MES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
            "agosto", "setembro", "outubro", "novembro", "dezembro"]


def _encaixe(x, gama):
    """1 no meio do optimo, desce ate 0 nos limites absolutos."""
    a, b, c, d = gama
    if x <= a or x >= d:
        return 0.0
    if x < b:
        return (x - a) / (b - a)
    if x > c:
        return (d - x) / (d - c)
    # dentro do optimo: 1 no meio, 0.85 nas pontas, para distinguir as culturas
    meio, meia_largura = (b + c) / 2, (c - b) / 2
    return 1.0 - 0.15 * abs(x - meio) / meia_largura


def clima_do_ciclo(clima, inicio, dias):
    """Chuva total (mm) e temperatura média (C) entre a sementeira e a colheita,
    a partir das medias mensais."""
    chuva = temp = 0.0
    for n in range(dias):
        mes = clima[(inicio + timedelta(days=n)).month - 1]
        dias_mes = 28.25 if mes["mes"] == 2 else (30 if mes["mes"] in (4, 6, 9, 11) else 31)
        chuva += mes["chuva_mm"] / dias_mes
        temp += mes["temp_c"]
    return round(chuva), round(temp / dias, 1)


def pontuar(cultura, e_chuva, temp, solo):
    """Compatibilidade de base (0 a 1) a partir do encaixe da chuva, temperatura e solo."""
    e_temp = _encaixe(temp, cultura["temp"])
    e_ph = _encaixe(solo["ph"], cultura["ph"])
    e_tex = 1.0 if solo["textura"] in cultura["textura"] else 0.6
    # a agua pesa mais: e o factor que mais limita a agricultura de sequeiro
    base = (max(e_chuva, 0.02) ** 0.45) * (max(e_temp, 0.02) ** 0.30) \
        * (max(e_ph, 0.02) ** 0.15) * (e_tex ** 0.10)
    return base, e_temp, e_ph, e_tex


def avaliar(cultura, clima, solo, inicio, anterior=None, praga=None, prioridade="equilibrio",
            serie=None, pesos=None):
    dias = sum(cultura["ciclo"]) // 2
    chuva, temp = clima_do_ciclo(clima, inicio, dias)
    anos = historico.epocas(serie, inicio, dias) if serie else []
    fiabilidade = None
    razoes, avisos = [], []
    if len(anos) >= 10:
        # Com historico: media do encaixe em cada ano, em vez do encaixe da chuva media.
        # Assim uma cultura que falha em muitos anos perde pontos, mesmo se a media parece boa.
        encaixes = [_encaixe(t, cultura["chuva"]) for _, t in anos]
        # com previsao sazonal, os anos parecidos com o que se espera contam mais
        w = [pesos.get(a, 1.0) if pesos else 1.0 for a, _ in anos]
        e_chuva = sum(e * p for e, p in zip(encaixes, w)) / sum(w)
        chuva = round(sum(t for _, t in anos) / len(anos))
        bons = sum(1 for e in encaixes if e >= 0.8)
        secos = sum(1 for (_, t), e in zip(anos, encaixes) if e < 0.8 and t < cultura["chuva"][1])
        molhados = len(anos) - bons - secos
        fiabilidade = {"anos": len(anos), "bons": bons, "secos": secos, "molhados": molhados,
                       "pct": round(100 * bons / len(anos))}
        if bons >= 0.7 * len(anos):
            razoes.append(f"em {bons} de {len(anos)} anos a chuva no ciclo chegou para a cultura "
                          f"(média de {chuva} mm)")
        else:
            falha = f"em {secos} faltou chuva" if secos >= molhados else f"em {molhados} houve chuva a mais"
            avisos.append(f"só em {bons} de {len(anos)} anos a chuva no ciclo foi adequada; {falha} "
                          f"(ideal: {cultura['chuva'][1]}-{cultura['chuva'][2]} mm)")
    else:
        e_chuva = _encaixe(chuva, cultura["chuva"])
        if e_chuva >= 0.8:
            razoes.append(f"chuva esperada no ciclo ({chuva} mm) dentro do que a cultura precisa")
        elif chuva < cultura["chuva"][1]:
            avisos.append(f"chuva esperada no ciclo ({chuva} mm) abaixo do ideal "
                          f"({cultura['chuva'][1]}-{cultura['chuva'][2]} mm)")
        else:
            avisos.append(f"chuva esperada no ciclo ({chuva} mm) acima do ideal: risco de encharcamento")
    base, e_temp, e_ph, e_tex = pontuar(cultura, e_chuva, temp, solo)
    if e_temp >= 0.8:
        razoes.append(f"temperatura média de {temp} °C favorável")
    else:
        avisos.append(f"temperatura média de {temp} °C fora do ideal "
                      f"({cultura['temp'][1]}-{cultura['temp'][2]} °C)")
    if e_ph >= 0.8:
        razoes.append(f"pH do solo de {solo['ph']} adequado")
    else:
        avisos.append(f"pH do solo de {solo['ph']} fora do ideal")
    if e_tex == 1.0:
        razoes.append(f"solo {solo['textura']} adequado à cultura")
    else:
        avisos.append(f"prefere solo {' ou '.join(cultura['textura'])}; o local é {solo['textura']}")

    ajuste = 1.0
    if prioridade in ("agua", "renda", "solo"):
        chave = {"agua": "seca", "renda": "renda", "solo": "solo"}[prioridade]
        ajuste *= 1 + 0.25 * (cultura[chave] - 0.5)
        if cultura[chave] >= 0.7:
            razoes.append({"agua": "tolera bem a falta de água",
                           "renda": "bom valor de venda",
                           "solo": "ajuda a recuperar a fertilidade do solo"}[prioridade])
    if anterior and anterior in POR_ID:
        ant = POR_ID[anterior]
        if ant["id"] == cultura["id"]:
            ajuste *= 0.85
            avisos.append("é a mesma cultura da época anterior: repetir aumenta pragas e cansa o solo")
        elif ant["tipo"] == cultura["tipo"]:
            ajuste *= 0.93
            avisos.append(f"mesmo grupo da cultura anterior ({ant['nome'].lower()})")
        elif ant["tipo"] == "leguminosa" and cultura["tipo"] == "cereal":
            ajuste *= 1.05
            razoes.append(f"aproveita o azoto deixado pela leguminosa anterior ({ant['nome'].lower()})")
        elif ant["tipo"] == "cereal" and cultura["tipo"] == "leguminosa":
            ajuste *= 1.05
            razoes.append(f"boa sucessão depois de um cereal ({ant['nome'].lower()})")
    if praga in PRAGAS and cultura["id"] in PRAGAS[praga][1]:
        ajuste *= 0.8
        avisos.append(f"cultura atacada por {PRAGAS[praga][0].lower()}, que foi observada na parcela")

    colheita = inicio + timedelta(days=dias)
    return {
        "id": cultura["id"], "nome": cultura["nome"], "cientifico": cultura["cientifico"],
        "tipo": cultura["tipo"], "descricao": cultura["descricao"],
        "ciclo_dias": list(cultura["ciclo"]),
        # nunca 100%: ha sempre incerteza nos dados e nas regras
        "compatibilidade": round(100 * min(0.95, base * ajuste)),
        "chuva_ciclo_mm": chuva, "temp_ciclo_c": temp, "fiabilidade": fiabilidade,
        "colheita": colheita.isoformat(),
        "razoes": razoes, "avisos": avisos,
    }


def rotacoes(avaliacoes, anterior, prioridade, solo, chuva_epoca):
    """Estrategias para as proximas tres epocas, lidas da tabela rotacoes: a pratica actual
    e as alternativas que se aplicam a prioridade do agricultor."""
    por_id = {a["id"]: a for a in avaliacoes}
    actual = por_id.get(anterior) or por_id.get("milho") or avaliacoes[0]
    # cada estrategia escolhe as culturas pela compatibilidade, pesada ou nao pela tolerancia
    # a seca ou pelo valor de venda (campo "ordenar")
    peso = lambda a, criterio: a["compatibilidade"] * (
        1 if criterio == "compatibilidade" else 0.5 + POR_ID[a["id"]][criterio])
    opcoes = []
    for r in ROTACOES:
        if "*" not in r["prioridades"] and prioridade not in r["prioridades"]:
            continue
        seq = []
        for passo in r["passos"]:
            if passo.get("actual"):
                seq.append(actual)
            elif "repetir" in passo:
                seq.append(seq[passo["repetir"] - 1])
            else:
                criterio = passo.get("ordenar", r["ordenar"])
                usadas = {c["id"] for c in seq}
                candidatas = [a for a in avaliacoes if a["tipo"] in passo["tipos"] and a["id"] not in usadas]
                if candidatas:
                    seq.append(max(candidatas, key=lambda a: peso(a, criterio)))
        if len(seq) < len(r["passos"]):
            continue   # nao ha culturas de algum dos tipos pedidos
        nomes = {f"c{i + 1}": c["nome"] for i, c in enumerate(seq)}
        nomes.update({f"c{i + 1}_min": c["nome"].lower() for i, c in enumerate(seq)})
        distintas = list({c["id"]: c for c in seq}.values())
        opcoes.append({
            "nome": r["nome"],
            "resumo": r["resumo"].format(**nomes),
            "sequencia": [c["nome"] for c in seq],
            "pontuacao": round(min(95, r["factor"] * sum(c["compatibilidade"] for c in seq) / len(seq))),
            "actual": any(p.get("actual") for p in r["passos"]),
            "porque": r["porque"].format(**nomes),
            "evidencia": [f"{c['nome']}: {c['compatibilidade']}% de compatibilidade" for c in distintas]
                         + [f"chuva média da época: {chuva_epoca} mm", f"solo {solo['textura']}, pH {solo['ph']}"],
            "beneficios": r["beneficios"], "riscos": r["riscos"], "lacunas": r["lacunas"],
        })
    return opcoes


def tempo_para_a_data(clima, previsao, dia, serie=None):
    """Tempo esperado no dia escolhido. Previsao se o dia esta nos proximos 16 dias;
    caso contrario, a media historica do mes."""
    janela = [p for p in previsao if dia.isoformat() <= p["data"] < (dia + timedelta(days=7)).isoformat()]
    do_dia = next((p for p in previsao if p["data"] == dia.isoformat()), None)
    mes = clima[dia.month - 1]
    if do_dia:
        return {
            "fonte": "previsao",
            "rotulo": "Previsão do tempo",
            "chuva_mm": round(do_dia["chuva_mm"], 1),
            "chuva_rotulo": "no dia",
            "temp_c": round((do_dia["tmax"] + do_dia["tmin"]) / 2, 1),
            "tmax": do_dia["tmax"], "tmin": do_dia["tmin"],
            "humidade": do_dia["humidade"],
            "chuva_7dias_mm": round(sum(p["chuva_mm"] for p in janela), 1),
            "dias_janela": len(janela),
            "media_mes_mm": mes["chuva_mm"],
            "nota": "Previsão numérica (Open-Meteo). A confiança desce depois dos primeiros 7 dias.",
        }
    resposta = {
        "fonte": "historico",
        "rotulo": "Média histórica",
        "chuva_mm": mes["chuva_mm"],
        "chuva_rotulo": f"média de {NOME_MES[dia.month - 1]}",
        "temp_c": mes["temp_c"],
        "humidade": mes["humidade"],
        "nota": "Data fora do alcance da previsão (16 dias). Mostra-se o que é normal para o mês "
                "(temperatura e humidade: NASA POWER, 2001-2025), não uma previsão para este ano.",
    }
    if serie:
        normal = historico.mes_normal(serie, dia.month)
        resposta["chuva_mm"] = normal["media"]
        resposta["nota"] += (f" Chuva: em 8 de cada 10 anos, {NOME_MES[dia.month - 1]} teve entre "
                             f"{normal['baixo']} e {normal['alto']} mm (NASA GLDAS, {normal['anos']} anos).")
    return resposta


def alertas(clima, previsao, dia, cobertura):
    """Avisos simples. Os mesmos avisos vao alimentar o servico de SMS."""
    lista = []
    visto = set()
    for p in previsao:
        if p["chuva_mm"] >= 50 and "chuva" not in visto:
            visto.add("chuva")
            lista.append({"nivel": "alto", "data": p["data"],
                          "texto": f"Chuva forte prevista ({p['chuva_mm']:.0f} mm num dia): "
                                   "risco de cheia e de perda de semente."})
        if p["tmax"] >= 38 and "calor" not in visto:
            visto.add("calor")
            lista.append({"nivel": "medio", "data": p["data"],
                          "texto": f"Calor extremo previsto ({p['tmax']:.0f} °C): "
                                   "evite semear e transplantar nesse dia."})
    futuros = [p for p in previsao if p["data"] >= dia.isoformat()][:10]
    mes = clima[dia.month - 1]
    if len(futuros) >= 7:
        total = sum(p["chuva_mm"] for p in futuros)
        if total >= 25:
            lista.append({"nivel": "bom", "data": dia.isoformat(),
                          "texto": f"{total:.0f} mm de chuva previstos nos {len(futuros)} dias "
                                   "seguintes: há humidade para a sementeira."})
        elif mes["chuva_mm"] >= 60:
            lista.append({"nivel": "medio", "data": dia.isoformat(),
                          "texto": f"Só {total:.0f} mm previstos nos {len(futuros)} dias seguintes, "
                                   "num mês que costuma ser chuvoso: chuvas atrasadas, espere para semear."})
    elif mes["chuva_mm"] < 40:
        lista.append({"nivel": "medio", "data": dia.isoformat(),
                      "texto": f"{NOME_MES[dia.month - 1].capitalize()} é normalmente seco aqui "
                               f"({mes['chuva_mm']:.0f} mm): semear só com rega ou em baixas húmidas."})
    if cobertura and cobertura.get("Vegetacao inundada", 0) + cobertura.get("Agua", 0) >= 10:
        lista.append({"nivel": "medio", "data": None,
                      "texto": "Distrito com muitas zonas húmidas ou alagadas: "
                               "escolha parcelas altas na época das chuvas."})
    return lista


def melhores_e_piores_anos(serie, clima, solo, dia, epoca):
    """Para cada ano do registo: a cultura que teria corrido melhor com a chuva desse ano."""
    por_ano = {}
    for c in ANUAIS:
        dias = sum(c["ciclo"]) // 2
        _, temp = clima_do_ciclo(clima, dia, dias)
        for ano, total in historico.epocas(serie, dia, dias):
            nota = round(100 * min(0.95, pontuar(c, _encaixe(total, c["chuva"]), temp, solo)[0]))
            # em caso de empate (ex.: ano tao seco que tudo falha), fica a mais tolerante a seca
            if (nota, c["seca"]) > por_ano.get(ano, (-1, "", 0))[::2]:
                por_ano[ano] = (nota, c["nome"], c["seca"])
    anos = []
    for a in epoca["anos"]:
        if a["ano"] in por_ano:
            anos.append({**a, "pontuacao": por_ano[a["ano"]][0], "melhor_cultura": por_ano[a["ano"]][1]})
    # melhores: epocas normais com a pontuacao mais alta; piores: as mais afastadas do normal
    normais = [a for a in anos if a["classe_id"] == "normal"] or anos
    melhores = sorted(normais, key=lambda a: (-a["pontuacao"], abs(a["pct_normal"] - 100)))[:3]
    piores = sorted((a for a in anos if a["classe_id"] != "normal"),
                    key=lambda a: -abs(a["pct_normal"] - 100))[:3]
    return {"todos": anos, "melhores": melhores, "piores": piores}


def avaliar_permanentes(clima, solo, serie):
    """Culturas permanentes: comparam-se com a chuva e a temperatura de um ano inteiro."""
    temp = round(sum(m["temp_c"] for m in clima) / 12, 1)
    anuais = []
    if serie:
        por_ano = {}
        for (ano, _), v in serie.items():
            por_ano.setdefault(ano, []).append(v)
        anuais = [sum(v) for v in por_ano.values() if len(v) == 12]
    chuva = round(sum(anuais) / len(anuais)) if anuais else round(sum(m["chuva_mm"] for m in clima))
    lista = []
    for c in PERMANENTES:
        e_chuva = (sum(_encaixe(t, c["chuva"]) for t in anuais) / len(anuais)) if anuais \
            else _encaixe(chuva, c["chuva"])
        base = pontuar(c, e_chuva, temp, solo)[0]
        nota = (f"chuva anual média de {chuva} mm; ideal {c['chuva'][1]}-{c['chuva'][2]} mm"
                + ("" if chuva >= c["chuva"][1] else ": só com rega ou junto a água"))
        lista.append({"id": c["id"], "nome": c["nome"], "cientifico": c["cientifico"],
                      "descricao": c["descricao"], "compatibilidade": round(100 * min(0.95, base)),
                      "chuva_anual_mm": chuva, "temp_media_c": temp, "nota": nota})
    return sorted(lista, key=lambda x: -x["compatibilidade"])


def analisar(distrito, clima, previsao, solo, dia, anterior, praga, prioridade):
    serie = historico.serie(distrito["id"])
    epoca = historico.resumo(serie, dia) if serie else None
    sazonal = previsao_epoca.prever(distrito, dia) if epoca else None
    pesos = None
    if sazonal and sazonal["usada"]:
        # peso de cada ano = probabilidade prevista da sua classe / frequencia dessa classe no registo
        tres = lambda pct: "seca" if pct < 80 else ("chuvosa" if pct > 120 else "normal")
        conta = {c: sum(1 for a in epoca["anos"] if tres(a["pct_normal"]) == c) for c in previsao_epoca.CLASSES}
        pesos = {a["ano"]: sazonal["probabilidades"][tres(a["pct_normal"])] / 100
                 / (conta[tres(a["pct_normal"])] / len(epoca["anos"])) for a in epoca["anos"]}
    avaliacoes = sorted(
        (avaliar(c, clima, solo, dia, anterior, praga, prioridade, serie, pesos) for c in ANUAIS),
        key=lambda a: -a["compatibilidade"],
    )
    for a in avaliacoes:   # o que vigiar entre a sementeira e a colheita de cada cultura
        a["ciclo"] = ciclo.vigiar(POR_ID[a["id"]], clima, serie, previsao, dia)
    chuva_epoca, _ = clima_do_ciclo(clima, dia, 120)
    avisos_epoca = alertas(clima, previsao, dia, distrito["cobertura"])
    if sazonal and sazonal["usada"]:
        p = sazonal["probabilidades"]
        if p["seca"] >= 50:
            avisos_epoca.insert(0, {"nivel": "alto", "data": None, "texto":
                f"{sazonal['fase']} em curso: {p['seca']}% de probabilidade de época seca "
                f"(habitual: {sazonal['habitual']['seca']}%). Prefira culturas resistentes à seca e de ciclo curto."})
        elif p["chuvosa"] >= 50:
            avisos_epoca.insert(0, {"nivel": "alto", "data": None, "texto":
                f"{sazonal['fase']} em curso: {p['chuvosa']}% de probabilidade de época muito chuvosa "
                f"(habitual: {sazonal['habitual']['chuvosa']}%). Evite as baixas e prepare a drenagem."})
    if epoca:
        chuva_epoca = epoca["normal_mm"]
        epoca["anos_comparados"] = melhores_e_piores_anos(serie, clima, solo, dia, epoca)
    return {
        "epoca": epoca,
        "previsao_epoca": sazonal,
        "distrito": distrito["distrito"], "provincia": distrito["provincia"],
        "data": dia.isoformat(),
        "tempo": tempo_para_a_data(clima, previsao, dia, serie),
        "chuva_epoca_mm": chuva_epoca,
        "solo": solo,
        "cobertura": distrito["cobertura"], "cobertura_ano": distrito["cobertura_ano"],
        "clima_mensal": clima,
        "previsao": previsao,
        "alertas": avisos_epoca,
        "culturas": avaliacoes,
        "permanentes": avaliar_permanentes(clima, solo, serie),
        "rotacoes": rotacoes(avaliacoes, anterior, prioridade, solo, chuva_epoca),
        "incerteza": [
            "As percentagens vêm de regras baseadas em gamas FAO ECOCROP aproximadas, "
            "não de ensaios feitos em Moçambique.",
            "A chuva vem de um modelo da NASA com células de 28 km: a sua parcela pode ser diferente "
            "da média do distrito.",
            "O passado não garante o futuro: as frequências mostram o que aconteceu em "
            + (f"{epoca['n_epocas']} épocas" if epoca else "anos anteriores") + ", não o que vai acontecer.",
            "Solo: " + solo["fonte"] + ".",
            *(["A ordem das culturas dá mais peso aos anos parecidos com a previsão para esta época. "
               "A previsão sazonal acerta pouco mais do que a frequência histórica: é uma indicação, não uma certeza."]
              if pesos else []),
            "Confirme sempre com o técnico de extensão (SDAE) antes de decidir.",
        ],
    }
