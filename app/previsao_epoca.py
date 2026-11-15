"""
Previsao sazonal treinada: probabilidade de a epoca ser seca, normal ou chuvosa,
a partir do indice do El Nino (ONI). O modelo e treinado por treinar_modelo.py.

So e usada onde a validacao mostrou ganho sobre a frequencia historica (ver
app/data/modelo_epoca.json e MODELOS.md). Fora disso devolve o motivo e a
pagina mostra apenas a frequencia historica.
"""
import json
from pathlib import Path

DADOS = Path(__file__).parent / "data"
MESES_VALIDOS = (10, 11, 12, 1)        # meses de sementeira em que o modelo foi treinado
ANTECEDENCIAS = (2, 3, 4)              # meses entre o ONI conhecido e a sementeira
ENTRADAS = ["oni", "oni_tendencia", "oni_x_lat", "oni_x_lon", "lat", "lon", "antecedencia"]
CLASSES = ["seca", "normal", "chuvosa"]
_modelo = _info = _oni = None


def regiao(lat):
    return "sul" if lat < -21 else ("centro" if lat < -16 else "norte")


def recuar(ano, mes, n):
    """(ano, mes) n meses antes."""
    k = ano * 12 + (mes - 1) - n
    return k // 12, k % 12 + 1


def entradas(oni, tendencia, lat, lon, antecedencia):
    """A mesma lista de entradas no treino e na previsao."""
    return [oni, tendencia, oni * lat, oni * lon, lat, lon, antecedencia]


def fase(oni):
    if oni >= 1.5:
        return "El Niño forte"
    if oni >= 0.5:
        return "El Niño"
    if oni <= -1.5:
        return "La Niña forte"
    if oni <= -0.5:
        return "La Niña"
    return "neutro"


def _carregar():
    global _modelo, _info, _oni
    if _info is None and (DADOS / "modelo_epoca.json").exists():
        import joblib
        _info = json.loads((DADOS / "modelo_epoca.json").read_text(encoding="utf-8"))
        _modelo = joblib.load(DADOS / "modelo_epoca.joblib")
        _oni = {(a, m): v for a, m, v in
                json.loads((DADOS / "oni.json").read_text(encoding="utf-8"))["valores"]}
    return _info is not None


def prever(distrito, dia):
    """Devolve None se nao houver modelo; senao um dicionario com 'usada' True ou False."""
    if not _carregar():
        return None
    ultimo = max(_oni)
    valor = _oni[ultimo]
    base = {"modelo": _info["nome"], "usada": False, "oni": valor, "fase": fase(valor),
            "oni_periodo": f"{ultimo[1]:02d}/{ultimo[0]}"}
    n = (dia.year * 12 + dia.month) - (ultimo[0] * 12 + ultimo[1])
    reg = regiao(distrito["lat"])
    validacao = _info["validacao"]["por_regiao"][reg]
    if dia.month not in MESES_VALIDOS:
        return {**base, "motivo": "O modelo só foi treinado para sementeiras de outubro a janeiro."}
    if n > max(ANTECEDENCIAS):
        return {**base, "motivo": "A data está a mais de 4 meses do último valor conhecido do El Niño."}
    if validacao["ganho"] <= 0:
        return {**base, "motivo": f"Na validação, o modelo não acertou mais do que a frequência histórica "
                                  f"na zona {reg} do país."}
    n = max(n, min(ANTECEDENCIAS))
    anterior = _oni.get(recuar(ultimo[0], ultimo[1], 3), valor)
    prob = _modelo.predict_proba([entradas(valor, valor - anterior, distrito["lat"], distrito["lon"], n)])[0]
    # calibracao: mistura com a frequencia historica da regiao, no peso escolhido na validacao
    peso, habitual = _info["peso_do_modelo"], _info["frequencia_por_regiao"][reg]
    prob = [peso * float(p) + (1 - peso) * h for p, h in zip(prob, habitual)]
    return {**base, "usada": True, "antecedencia_meses": n, "regiao": reg,
            "probabilidades": {c: round(100 * float(p)) for c, p in zip(CLASSES, prob)},
            "habitual": {c: round(100 * h) for c, h in zip(CLASSES, habitual)},
            "validacao": validacao, "epocas_treino": _info["epocas"]}
