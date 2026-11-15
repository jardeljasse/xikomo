"""
Vigilancia do ciclo: o que pode correr mal entre a sementeira e a colheita.

Recomendar uma data so pelo dia da sementeira e arriscado: pode haver cheias ou
um periodo seco um mes depois. Aqui percorre-se o ciclo MAXIMO da cultura
(ex.: milho, 120 dias) e assinalam-se os periodos a vigiar, com o cuidado a ter.

Fontes, por ordem de confianca:
  - primeiros 16 dias: previsao do tempo (dias concretos);
  - resto do ciclo: o que aconteceu em cada mes do calendario desde 2000
    (frequencia historica, NASA GLDAS) e a temperatura normal do mes (NASA POWER).
Os limites (50 mm num dia, 1,5 vezes a mediana, etc.) foram escolhidos por nos.
"""
import calendar
from datetime import date, timedelta

from .historico import NOME_MES, mediana

FASES = [(0.20, "germinação e arranque"), (0.45, "crescimento"),
         (0.80, "floração e enchimento"), (9.99, "maturação e colheita")]
CUIDADOS = {
    ("cheia", 0): "Semeie em camalhões ou em terreno alto: a água parada mata a planta nova.",
    ("cheia", 1): "Abra valas de drenagem, evite as baixas e vigie as doenças de fungos.",
    ("cheia", 2): "Abra valas de drenagem, evite as baixas e vigie as doenças de fungos.",
    ("cheia", 3): "Colha logo que esteja maduro e seque bem antes de guardar.",
    ("seca", 0): "Semeie só com o solo húmido e guarde semente para voltar a semear, se for preciso.",
    ("seca", 1): "Cubra o solo com palha e faça a sacha cedo, para guardar a humidade.",
    ("seca", 2): "É a fase em que a falta de água mais reduz a colheita: regue, se puder.",
}


def _fase(fraccao):
    for i, (limite, nome) in enumerate(FASES):
        if fraccao < limite:
            return i, nome


def _periodo(de, ate):
    """'5 a 31 de dezembro'."""
    return f"{de.day} a {ate.day} de {NOME_MES[de.month - 1]}"


def _nivel(vezes, anos):
    """alto: aconteceu em 1 de cada 4 anos ou mais; medio: em 1 de cada 8 (e pelo menos 3 vezes)."""
    if vezes / anos >= 0.25:
        return "alto"
    if vezes / anos >= 0.12 and vezes >= 3:
        return "medio"
    return None


def vigiar(cultura, clima, serie, previsao, inicio):
    dias = cultura["ciclo"][1]
    fim = inicio + timedelta(days=dias)
    riscos = []

    def juntar(quando_de, quando_ate, fase, risco, nivel, base, cuidado):
        riscos.append({"de": quando_de.isoformat(), "quando": _periodo(quando_de, quando_ate) if quando_ate != quando_de
                       else f"{quando_de.day} de {NOME_MES[quando_de.month - 1]}",
                       "fase": fase, "risco": risco, "nivel": nivel, "base": base, "cuidado": cuidado})

    # A) Previsao do tempo: dias concretos dentro do ciclo
    janela = [p for p in previsao if inicio.isoformat() <= p["data"] <= fim.isoformat()]
    forte = next((p for p in janela if p["chuva_mm"] >= 50), None)
    if forte:
        d = date.fromisoformat(forte["data"])
        juntar(d, d, _fase((d - inicio).days / dias)[1], "Chuva forte prevista", "alto",
               f"previsão do tempo: {forte['chuva_mm']:.0f} mm num dia",
               "Semeie depois deste dia: a chuva forte arrasta a semente e encharca o solo.")
    calor = next((p for p in janela if p["tmax"] >= 38), None)
    if calor:
        d = date.fromisoformat(calor["data"])
        juntar(d, d, _fase((d - inicio).days / dias)[1], "Calor extremo previsto", "medio",
               f"previsão do tempo: {calor['tmax']:.0f} °C",
               "Não semeie nem transplante nesse dia; regue ao fim da tarde, se puder.")
    primeiros = janela[:10]
    if len(primeiros) >= 8 and sum(p["chuva_mm"] for p in primeiros) < 10 and clima[inicio.month - 1]["chuva_mm"] >= 60:
        juntar(inicio, date.fromisoformat(primeiros[-1]["data"]), FASES[0][1], "Sem chuva nos primeiros dias", "medio",
               f"previsão do tempo: {sum(p['chuva_mm'] for p in primeiros):.0f} mm em {len(primeiros)} dias",
               "A semente pode não germinar: espere pela chuva ou regue.")

    # B) Mes a mes, ate ao fim do ciclo
    dia = inicio
    while dia < fim:
        ultimo = date(dia.year, dia.month, calendar.monthrange(dia.year, dia.month)[1])
        ate = min(ultimo, fim)
        if (ate - dia).days + 1 >= 7:   # pedacos de mes muito curtos nao contam
            meio = (dia - inicio).days + ((ate - dia).days + 1) / 2
            n_fase, nome_fase = _fase(meio / dias)
            valores = [v for (a, m), v in serie.items() if m == dia.month] if serie else []
            teve_cheia = False
            if len(valores) >= 10:
                normal, anos = mediana(valores), len(valores)
                limite = max(250, 1.5 * normal)
                vezes = sum(1 for v in valores if v >= limite)
                nivel = _nivel(vezes, anos)
                if nivel:
                    teve_cheia = True
                    juntar(dia, ate, nome_fase, "Chuva muito forte", nivel,
                           f"em {vezes} de {anos} anos choveu mais de {limite:.0f} mm neste mês",
                           CUIDADOS[("cheia", n_fase)])
                if n_fase < 3 and normal >= 60:
                    limite = 0.5 * normal
                    vezes = sum(1 for v in valores if v < limite)
                    nivel = _nivel(vezes, anos)
                    if nivel:
                        juntar(dia, ate, nome_fase, "Período seco", nivel,
                               f"em {vezes} de {anos} anos choveu menos de {limite:.0f} mm neste mês",
                               CUIDADOS[("seca", n_fase)])
                elif n_fase < 3 and normal < 30:
                    juntar(dia, ate, nome_fase, "Mês normalmente seco", "medio",
                           f"chuva habitual de {normal:.0f} mm neste mês",
                           "Só com rega ou em baixas húmidas.")
                if n_fase == 3 and normal >= 100 and not teve_cheia:
                    juntar(dia, ate, nome_fase, "Chuva na colheita", "medio",
                           f"chuva habitual de {normal:.0f} mm neste mês",
                           "Colha logo que esteja maduro, seque bem e guarde em lugar seco.")
            temp = clima[dia.month - 1]["temp_c"]
            if n_fase == 2 and temp > cultura["temp"][2]:
                juntar(dia, ate, nome_fase, "Calor acima do ideal", "medio",
                       f"temperatura normal de {temp} °C; o ideal vai até {cultura['temp'][2]} °C",
                       "O calor na floração reduz a colheita: mantenha o solo coberto e húmido.")
            elif n_fase in (1, 2) and temp < cultura["temp"][1]:
                juntar(dia, ate, nome_fase, "Frio abaixo do ideal", "medio",
                       f"temperatura normal de {temp} °C; o ideal começa em {cultura['temp'][1]} °C",
                       "A planta cresce mais devagar: conte com mais dias até à colheita.")
        dia = ultimo + timedelta(days=1)

    # no maximo 6: primeiro os de nivel alto, depois por data
    escolhidos = sorted(riscos, key=lambda r: (r["nivel"] != "alto", r["de"]))[:6]
    escolhidos.sort(key=lambda r: r["de"])
    return {"dias": dias, "fim": fim.isoformat(), "riscos": escolhidos,
            "altos": sum(1 for r in escolhidos if r["nivel"] == "alto"),
            "omitidos": len(riscos) - len(escolhidos)}
