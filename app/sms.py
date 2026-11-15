"""
Alertas por SMS para agricultores com telemovel simples.

Tipos de alerta (ver note.txt):
  bom    - vem uma boa altura para semear (chuva prevista no inicio da epoca)
  mau    - vem um periodo mau: chuva forte (cheia), calor extremo ou epoca seca provavel
  curto  - as chuvas acabam daqui a ~2 meses, mas ainda da para culturas de ciclo curto

Envio: por agora SIMULADO. As mensagens ficam na tabela sms com estado 'simulado'
e aparecem na pagina. Para enviar a serio, implementar _enviar_fornecedor() e definir
SMS_FORNECEDOR no .env.

Correr a verificacao (uma vez por dia, por exemplo com cron):
    uv run python enviar_alertas.py
"""
import os
import re
import unicodedata
from datetime import date, timedelta

import requests

from . import clima, culturas, db, traducao

MODO = os.environ.get("SMS_FORNECEDOR", "simulado")
SITE = os.environ.get("SITE_URL", "").rstrip("/")   # ex.: https://xikomu.example; vazio enquanto nao houver
OPERADORAS = {"82": "Tmcel", "83": "Tmcel", "84": "Vodacom", "85": "Vodacom", "86": "Movitel", "87": "Movitel"}
PRIORIDADE = {"mau": 0, "curto": 1, "bom": 2}       # numa verificacao, cada pessoa recebe no maximo um alerta
NOME_MES = culturas.NOME_MES

# Textos de origem (portugues). {chaves} sao preenchidas no fim; o resto e traduzido.
MODELOS = {
    "boas_vindas": "xikomu {distrito}: inscrito nos alertas de sementeira e de tempo.",
    "bom": "xikomu {distrito}: boa altura para semear, {mm} mm de chuva ate {data}. "
           "Melhores: {c1}, {c2}, {c3}.",
    "cheia": "xikomu {distrito}: ALERTA chuva forte a {data} ({mm} mm), risco de cheia. "
             "Nao semeie nas baixas e guarde a semente no alto.",
    "calor": "xikomu {distrito}: ALERTA calor de {t} graus a {data}. Nao semeie nem transplante nesses dias.",
    "seca": "xikomu {distrito}: ALERTA epoca seca provavel ({p}% de probabilidade). "
            "Nao e boa altura para semear sem rega.",
    "curto": "xikomu {distrito}: a chuva costuma parar em {mes} ({dias} dias). "
             "Ainda da para culturas rapidas: {c1} {d1} dias, {c2} {d2} dias.",
    "rodape_link": " Mais info: {url} ou centro digital.",
    "rodape": " Mais info: pagina xikomu ou centro digital.",
}

ESQUEMA = """
CREATE TABLE IF NOT EXISTS assinantes (
    id           bigserial PRIMARY KEY,
    telefone     text UNIQUE NOT NULL,      -- 258 + 9 digitos
    distrito_id  integer REFERENCES distritos(id),
    lingua       text DEFAULT 'pt',
    operadora    text,
    activo       boolean DEFAULT true,
    criado_em    timestamptz DEFAULT now(),
    cancelado_em timestamptz
);
CREATE TABLE IF NOT EXISTS sms (
    id           bigserial PRIMARY KEY,
    criada_em    timestamptz DEFAULT now(),
    assinante_id bigint REFERENCES assinantes(id),
    distrito_id  integer REFERENCES distritos(id),
    tipo         text,                      -- boas_vindas, bom, mau, curto
    chave        text,                      -- evita repetir o mesmo alerta
    texto        text,
    caracteres   integer,
    segmentos    integer,                   -- quantos SMS a rede cobra
    estado       text                       -- simulado, enviado, falhou
);
CREATE INDEX IF NOT EXISTS sms_assinante_chave ON sms (assinante_id, chave);
"""


def preparar():
    return bool(db.executar(ESQUEMA))


# ---------- Numeros de telefone ----------
def normalizar(telefone):
    """'84 123 4567', '+258841234567' ... -> '258841234567'. None se nao for um telemovel de Mocambique."""
    digitos = re.sub(r"\D", "", telefone or "")
    if digitos.startswith("258"):
        digitos = digitos[3:]
    if len(digitos) == 9 and digitos[:2] in OPERADORAS:
        return "258" + digitos
    return None


def operadora(telefone):
    return OPERADORAS.get(telefone[3:5], "desconhecida")


def mascarar(telefone):
    """'258841234567' -> '+258 84 *** **67' (para mostrar na pagina sem expor o numero)."""
    return f"+258 {telefone[3:5]} *** **{telefone[-2:]}"


# ---------- Tamanho do SMS ----------
GSM7 = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZ"
           "ÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
GSM7_DUPLO = set("^{}\\[~]|€")


def segmentos(texto):
    """Quantos SMS a rede cobra. Com so caracteres GSM-7: 160 num SMS, 153 por parte.
    Com qualquer outro (ex.: 'ç', 'ã'): 70 e 67 — por isso tiramos os acentos."""
    if all(c in GSM7 or c in GSM7_DUPLO for c in texto):
        n = sum(2 if c in GSM7_DUPLO else 1 for c in texto)
        return n, 1 if n <= 160 else -(-n // 153)
    return len(texto), 1 if len(texto) <= 70 else -(-len(texto) // 67)


def sem_acentos(texto):
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in texto if not unicodedata.combining(c))


# ---------- Texto na lingua do assinante ----------
def compor(chave, lingua, distrito_id, **valores):
    """Modelo traduzido + valores (nomes de culturas e meses tambem traduzidos) + rodape."""
    rodape = "rodape_link" if SITE else "rodape"
    modelos = [MODELOS[chave], MODELOS[rodape]]
    palavras = [v for k, v in valores.items() if isinstance(v, str) and (k.startswith("c") or k == "mes")]
    if lingua != "pt":
        t = traducao.traduzir(lingua, modelos + palavras)
        modelos = [t.get(m, m) for m in modelos]
        valores = {k: t.get(v, v) if isinstance(v, str) and v in palavras else v for k, v in valores.items()}
    url = f"{SITE}/?distrito={distrito_id}" if SITE else ""
    try:
        texto = modelos[0].format(**valores) + modelos[1].format(url=url)
    except (KeyError, IndexError):   # traducao estragou um marcador: usa o portugues
        texto = MODELOS[chave].format(**valores) + MODELOS[rodape].format(url=url)
    return sem_acentos(texto)


# ---------- Decidir que alerta enviar ----------
def alertas_para(distrito, hoje):
    """Lista de (tipo, chave, modelo, valores), do mais urgente para o menos."""
    medias = clima.climatologia(distrito)
    dias = clima.previsao(distrito)
    a = culturas.analisar(distrito, medias, dias, clima.solo(distrito), hoje, None, None, "equilibrio")
    lista = []
    nome = distrito["distrito"]
    dm = lambda iso: f"{int(iso[8:10])}/{int(iso[5:7])}"

    # 2) periodo mau
    dias = [p for p in dias if p["data"] >= hoje.isoformat()]   # so o que ainda vem
    cheia = next((p for p in dias if p["chuva_mm"] >= 50), None)
    if cheia:
        lista.append(("mau", f"cheia:{cheia['data']}", "cheia",
                      dict(distrito=nome, data=dm(cheia["data"]), mm=round(cheia["chuva_mm"]))))
    calor = next((p for p in dias if p["tmax"] >= 40), None)
    if calor:
        lista.append(("mau", f"calor:{calor['data']}", "calor",
                      dict(distrito=nome, data=dm(calor["data"]), t=round(calor["tmax"]))))
    s = a.get("previsao_epoca")
    if s and s.get("usada") and s["probabilidades"]["seca"] >= 50:
        lista.append(("mau", f"seca:{hoje.year}-{hoje.month}", "seca",
                      dict(distrito=nome, p=s["probabilidades"]["seca"])))

    mes_chuvoso = medias[hoje.month - 1]["chuva_mm"] >= 60
    boas = [c for c in a["culturas"] if c["compatibilidade"] >= 50]

    # 3) as chuvas acabam em breve, mas ainda da para culturas de ciclo curto
    if mes_chuvoso:
        for n in range(1, 5):
            m = (hoje.month - 1 + n) % 12 + 1
            if medias[m - 1]["chuva_mm"] < 40:
                inicio = date(hoje.year + (hoje.month + n - 1) // 12, m, 1)
                faltam = (inicio - hoje).days
                rapidas = sorted((c for c in boas if c["ciclo_dias"][0] <= faltam), key=lambda c: c["ciclo_dias"][0])
                if 30 <= faltam <= 100 and len(rapidas) >= 2:
                    lista.append(("curto", f"curto:{inicio.isoformat()}", "curto", dict(
                        distrito=nome, mes=NOME_MES[m - 1], dias=faltam,
                        c1=rapidas[0]["nome"], d1=rapidas[0]["ciclo_dias"][0],
                        c2=rapidas[1]["nome"], d2=rapidas[1]["ciclo_dias"][0])))
                break

    # 1) boa altura para semear
    proximos = dias[:10]
    total = sum(p["chuva_mm"] for p in proximos)
    if mes_chuvoso and len(proximos) >= 7 and total >= 25 and not cheia and len(boas) >= 3:
        lista.append(("bom", f"bom:{hoje.isocalendar()[0]}-{hoje.isocalendar()[1]}", "bom", dict(
            distrito=nome, mm=round(total), data=dm(proximos[-1]["data"]),
            c1=boas[0]["nome"], c2=boas[1]["nome"], c3=boas[2]["nome"])))
    return sorted(lista, key=lambda x: PRIORIDADE[x[0]])


# ---------- Enviar ----------
def _enviar_fornecedor(telefone, texto):
    """Ponto de ligacao a um fornecedor real (Twilio, Africa's Talking, operadora).
    Devolve 'enviado' ou 'falhou'. Ainda nao implementado."""
    raise NotImplementedError("Envio real ainda nao configurado; use SMS_FORNECEDOR=simulado.")


def enviar(assinante, tipo, chave, texto):
    caracteres, partes = segmentos(texto)
    estado = "simulado" if MODO == "simulado" else _enviar_fornecedor(assinante["telefone"], texto)
    db.executar("INSERT INTO sms (assinante_id, distrito_id, tipo, chave, texto, caracteres, segmentos, estado) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (assinante["id"], assinante["distrito_id"], tipo, chave, texto, caracteres, partes, estado))
    return estado


def ja_enviado(assinante_id, chave):
    return bool(db.executar("SELECT 1 FROM sms WHERE assinante_id = %s AND chave = %s LIMIT 1",
                            (assinante_id, chave), ler=True))


def hoje_ja_recebeu(assinante_id, hoje):
    return bool(db.executar("SELECT 1 FROM sms WHERE assinante_id = %s AND tipo <> 'boas_vindas' "
                            "AND criada_em::date = %s LIMIT 1", (assinante_id, hoje), ler=True))


def verificar_todos(distritos_por_id, hoje=None):
    """Corre os alertas para todos os assinantes activos. Devolve um resumo."""
    hoje = hoje or date.today()
    assinantes = db.executar("SELECT id, telefone, distrito_id, lingua FROM assinantes WHERE activo", ler=True) or []
    resumo = {"assinantes": len(assinantes), "enviadas": 0, "sem_alerta": 0, "erros": 0}
    por_distrito = {}
    for id_, telefone, distrito_id, lingua in assinantes:
        a = {"id": id_, "telefone": telefone, "distrito_id": distrito_id, "lingua": lingua}
        try:
            if distrito_id not in por_distrito:   # o calculo e o mesmo para todos no distrito
                por_distrito[distrito_id] = alertas_para(distritos_por_id[distrito_id], hoje)
        except (requests.RequestException, KeyError, ValueError):
            resumo["erros"] += 1
            continue
        if hoje_ja_recebeu(id_, hoje):   # no maximo um alerta por pessoa por dia
            resumo["sem_alerta"] += 1
            continue
        for tipo, chave, modelo, valores in por_distrito[distrito_id]:
            if not ja_enviado(id_, chave):
                enviar(a, tipo, chave, compor(modelo, lingua, distrito_id, **valores))
                resumo["enviadas"] += 1
                break
        else:
            resumo["sem_alerta"] += 1
    return resumo
