"""
Servidor do prototipo xikomu.

Correr (na pasta shifl-field):
    uv run uvicorn app.server:app --reload --port 8000
e abrir http://localhost:8000
"""
import json
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

import requests
from rasterio.errors import RasterioError as rasterio_erro
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import assistente, clima, culturas, db, mapa_chuva, sms, traducao

AQUI = Path(__file__).parent
DISTRITOS = json.loads((AQUI / "data" / "distritos.json").read_text(encoding="utf-8"))
POR_ID = {d["id"]: d for d in DISTRITOS}
MAPUTO = ZoneInfo("Africa/Maputo")

app = FastAPI(title="xikomu")
db.preparar()  # cria as tabelas na primeira vez; nao faz nada sem DATABASE_URL
sms.preparar()


@app.get("/api/distritos")
def distritos():
    return [{k: d[k] for k in ("id", "distrito", "provincia", "lat", "lon")} for d in DISTRITOS]


@app.get("/api/opcoes")
def opcoes():
    """Listas para o formulario e para a seccao Culturas."""
    return {
        "culturas": [
            {k: c[k] for k in ("id", "nome", "cientifico", "tipo", "descricao", "ciclo",
                               "temp", "chuva", "ph", "textura")}
            for c in culturas.CULTURAS
        ],
        "pragas": {k: v[0] for k, v in culturas.PRAGAS.items()},
        "prioridades": culturas.PRIORIDADES,
        "hoje": date.today().isoformat(),
        "data_max": (date.today() + timedelta(days=365)).isoformat(),
    }


_geo = None


@app.get("/api/geo/{distrito_id}")
def geo(distrito_id: int):
    """Contorno do distrito e da sua provincia, para o mapa."""
    global _geo
    if _geo is None:
        _geo = json.loads((AQUI / "data" / "geo.json").read_text(encoding="utf-8"))
    distrito = POR_ID.get(distrito_id)
    if not distrito or str(distrito_id) not in _geo["distritos"]:
        raise HTTPException(404, "Distrito desconhecido.")
    return {"distrito": _geo["distritos"][str(distrito_id)],
            "provincia": _geo["provincias"].get(distrito["provincia"]),
            "centro": [distrito["lat"], distrito["lon"]]}


@app.get("/api/chuva-meses")
def chuva_meses():
    """Meses com mapa de chuva disponivel e a escala de cores."""
    try:
        meses = sorted(mapa_chuva.catalogo())
    except (requests.RequestException, RuntimeError):
        raise HTTPException(502, "O mapa de chuva não está disponível agora.")
    return {"primeiro": meses[0], "ultimo": meses[-1],
            "escala": [{"desde_mm": limite, "cor": cor} for limite, cor in mapa_chuva.ESCALA]}


@app.get("/api/chuva-mapa.png")
def chuva_mapa(ano: int, mes: int, bbox: str, largura: int, altura: int):
    """Imagem da chuva de um mes para a area visivel do mapa."""
    try:
        cantos = [float(x) for x in bbox.split(",")]
    except ValueError:
        cantos = []
    if len(cantos) != 4 or not (1 <= largura <= 2000 and 1 <= altura <= 2000):
        raise HTTPException(422, "Pedido de mapa inválido.")
    try:
        png = mapa_chuva.imagem(ano, mes, ",".join(str(c) for c in cantos), largura, altura)
    except (requests.RequestException, RuntimeError, rasterio_erro):
        raise HTTPException(502, "O mapa de chuva não está disponível agora.")
    if png is None:
        raise HTTPException(404, "Não há mapa de chuva para esse mês.")
    return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


class Assinatura(BaseModel):
    telefone: str = Field(max_length=30)
    distrito_id: int = 0
    lingua: str = Field(default="pt", pattern="^(pt|en|ts)$")


def _sem_bd():
    if not db.ACTIVA:
        raise HTTPException(503, "Os alertas por SMS precisam da base de dados.")


@app.post("/api/sms/assinar")
def sms_assinar(p: Assinatura):
    """Inscreve (ou actualiza) um numero nos alertas de um distrito e manda a mensagem de boas-vindas."""
    _sem_bd()
    telefone = sms.normalizar(p.telefone)
    if not telefone:
        raise HTTPException(422, "Número inválido. Use um telemóvel de Moçambique (82 a 87), por exemplo 84 123 4567.")
    distrito = POR_ID.get(p.distrito_id)
    if not distrito:
        raise HTTPException(422, "Escolha o distrito.")
    linha = db.executar(
        "INSERT INTO assinantes (telefone, distrito_id, lingua, operadora) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (telefone) DO UPDATE SET distrito_id = EXCLUDED.distrito_id, lingua = EXCLUDED.lingua, "
        "activo = true, cancelado_em = NULL RETURNING id",
        (telefone, p.distrito_id, p.lingua, sms.operadora(telefone)), ler=True)
    if not linha:
        raise HTTPException(503, "Não foi possível guardar a inscrição. Tente de novo.")
    assinante = {"id": linha[0][0], "telefone": telefone, "distrito_id": p.distrito_id}
    texto = sms.compor("boas_vindas", p.lingua, p.distrito_id, distrito=distrito["distrito"])
    estado = sms.enviar(assinante, "boas_vindas", None, texto)
    return {"telefone": sms.mascarar(telefone), "operadora": sms.operadora(telefone),
            "distrito": distrito["distrito"], "estado": estado}


@app.post("/api/sms/cancelar")
def sms_cancelar(p: Assinatura):
    _sem_bd()
    telefone = sms.normalizar(p.telefone)
    if not telefone:
        raise HTTPException(422, "Número inválido.")
    linhas = db.executar("UPDATE assinantes SET activo = false, cancelado_em = now() "
                         "WHERE telefone = %s AND activo RETURNING id", (telefone,), ler=True)
    return {"cancelado": bool(linhas)}


@app.post("/api/sms/verificar")
def sms_verificar():
    """Corre os alertas agora. So em modo simulado: com envio real isto corre por cron
    (enviar_alertas.py), para ninguem poder gastar SMS a partir da pagina."""
    _sem_bd()
    if sms.MODO != "simulado":
        raise HTTPException(403, "Com envio real, os alertas correm só pelo enviar_alertas.py.")
    return sms.verificar_todos(POR_ID)


@app.get("/api/sms/mensagens")
def sms_mensagens(limite: int = 30):
    """Ultimas mensagens, com o numero mascarado."""
    _sem_bd()
    linhas = db.executar(
        "SELECT s.criada_em, a.telefone, a.operadora, s.distrito_id, s.tipo, s.texto, s.caracteres, "
        "s.segmentos, s.estado FROM sms s JOIN assinantes a ON a.id = s.assinante_id "
        "ORDER BY s.id DESC LIMIT %s", (max(1, min(limite, 100)),), ler=True) or []
    return {"modo": sms.MODO, "mensagens": [{
        "quando": l[0].astimezone(MAPUTO).strftime("%Y-%m-%d %H:%M"), "telefone": sms.mascarar(l[1]), "operadora": l[2],
        "distrito": POR_ID.get(l[3], {}).get("distrito"), "tipo": l[4], "texto": l[5],
        "caracteres": l[6], "segmentos": l[7], "estado": l[8]} for l in linhas]}


class Traduzir(BaseModel):
    lingua: str = Field(pattern="^(en|ts)$")
    textos: list[str] = Field(min_length=1, max_length=120)


@app.post("/api/traduzir")
def traduzir(p: Traduzir):
    """Traducoes para os textos da pagina (da base de dados; o que faltar e traduzido e guardado)."""
    textos = [t for t in p.textos if 0 < len(t) <= 1500]
    return {"traducoes": traducao.traduzir(p.lingua, textos)}


class Mensagem(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=1500)


class Pergunta(BaseModel):
    distrito_id: int
    data: date
    anterior: str = ""
    praga: str = ""
    prioridade: str = "equilibrio"
    anterior_texto: str = ""
    praga_texto: str = ""
    mensagens: list[Mensagem] = Field(min_length=1, max_length=20)


@app.post("/api/assistente")
def perguntar(p: Pergunta):
    """Pergunta ao assistente, que so responde com os dados da analise deste distrito e data."""
    if p.mensagens[0].role != "user" or p.mensagens[-1].role != "user":
        raise HTTPException(422, "A conversa tem de começar e acabar numa pergunta.")
    dados = analise(p.distrito_id, p.data, p.anterior, p.praga, p.prioridade,
                    p.anterior_texto, p.praga_texto, registar=False)
    texto, erro = assistente.responder(dados, [m.model_dump() for m in p.mensagens])
    if erro:
        raise HTTPException(502, erro)
    db.executar("INSERT INTO perguntas (distrito_id, pergunta, resposta) VALUES (%s, %s, %s)",
                (p.distrito_id, p.mensagens[-1].content, texto))
    return {"resposta": texto}


@app.get("/api/analise")
def analise(distrito_id: int, data: date, anterior: str = "", praga: str = "",
            prioridade: str = "equilibrio", anterior_texto: str = "", praga_texto: str = "",
            registar: bool = True):
    distrito = POR_ID.get(distrito_id)
    if not distrito:
        raise HTTPException(404, "Distrito desconhecido.")
    if prioridade not in culturas.PRIORIDADES:
        prioridade = "equilibrio"
    # texto livre ("Outra"): tenta reconhecer; se nao reconhecer, guarda-se mas nao entra no calculo
    entradas = []
    for campo, tipo, texto in (("Cultura anterior", "cultura", anterior_texto), ("Praga", "praga", praga_texto)):
        texto = " ".join(texto.split())[:60]
        if not texto:
            continue
        achado = culturas.reconhecer(texto, tipo)
        if tipo == "cultura":
            anterior = achado or ""
            nome = culturas.POR_ID[achado]["nome"] if achado else None
        else:
            praga = achado or ""
            nome = culturas.PRAGAS[achado][0] if achado else None
        entradas.append({"campo": campo, "texto": texto, "reconhecido": nome})
    if anterior not in culturas.POR_ID:
        anterior = ""
    if praga not in culturas.PRAGAS:
        praga = ""
    try:
        medias = clima.climatologia(distrito)
    except (requests.RequestException, KeyError, ValueError):
        raise HTTPException(502, "Nao foi possivel obter o clima da NASA POWER. Tente de novo.")
    resultado = culturas.analisar(
        distrito, medias, clima.previsao(distrito), clima.solo(distrito),
        data, anterior or None, praga or None, prioridade,
    )
    resultado["entradas"] = entradas
    if not registar:   # chamada interna do assistente: nao conta como nova analise
        return resultado
    escrito = {e["campo"]: e["texto"] for e in entradas if not e["reconhecido"]}
    db.registar_analise(distrito_id, data,
                        anterior or (f"texto: {escrito['Cultura anterior']}" if "Cultura anterior" in escrito else None),
                        praga or (f"texto: {escrito['Praga']}" if "Praga" in escrito else None),
                        prioridade, resultado["culturas"][0])
    if resultado["epoca"]:
        e = resultado["epoca"]
        db.registar_previsao(distrito_id, data, e["modelo"], {
            "epocas_usadas": e["n_epocas"], "chuva_normal_mm": e["normal_mm"],
            "frequencia_classes_pct": {c["id"]: c["pct"] for c in e["classes"]},
            "culturas": [{"id": c["id"], "compatibilidade": c["compatibilidade"],
                          "anos_com_chuva_adequada_pct": c["fiabilidade"]["pct"] if c["fiabilidade"] else None}
                         for c in resultado["culturas"]],
            "rotacoes": [{"nome": r["nome"], "pontuacao": r["pontuacao"], "sequencia": r["sequencia"]}
                         for r in resultado["rotacoes"]],
            "prioridade": prioridade, "anterior": anterior or None, "praga": praga or None,
            "previsao_sazonal": resultado["previsao_epoca"],
        })
    return resultado


@app.get("/")
def pagina():
    return FileResponse(AQUI / "static" / "index.html")


app.mount("/", StaticFiles(directory=AQUI / "static"), name="static")
