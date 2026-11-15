"""
Base de dados (Postgres na Neon). A ligacao vem de DATABASE_URL no ficheiro .env.

Se nao houver DATABASE_URL, ou a base de dados nao responder, a aplicacao
continua a funcionar com a cache em ficheiros (app/cache/).

Criar as tabelas e carregar os distritos (uma vez):
    uv run python -m app.db

As culturas, as pragas e as rotacoes vivem nas tabelas culturas, pragas e rotacoes.
Editam-se na base de dados; reinicia-se o servidor para as alteracoes entrarem.
Para repor os valores de origem (app/data/catalogo.json):
    uv run python -m app.db --repor-catalogo
"""
import json
import os
import threading
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

try:
    import psycopg
except ImportError:  # sem o driver, usa-se so a cache em ficheiros
    psycopg = None

AQUI = Path(__file__).parent
load_dotenv(AQUI.parent / ".env")
URL = os.environ.get("DATABASE_URL")
ACTIVA = bool(URL and psycopg)

_ligacao = None
_trinco = threading.Lock()

ESQUEMA = """
CREATE TABLE IF NOT EXISTS distritos (
    id            integer PRIMARY KEY,
    distrito      text NOT NULL,
    provincia     text NOT NULL,
    lat           double precision NOT NULL,
    lon           double precision NOT NULL,
    cobertura_ano integer,
    cobertura     jsonb
);
CREATE TABLE IF NOT EXISTS clima_mensal (
    distrito_id integer REFERENCES distritos(id),
    mes         integer CHECK (mes BETWEEN 1 AND 12),
    chuva_mm    double precision,
    temp_c      double precision,
    humidade    integer,
    PRIMARY KEY (distrito_id, mes)
);
CREATE TABLE IF NOT EXISTS solo (
    distrito_id integer PRIMARY KEY REFERENCES distritos(id),
    ph          double precision,
    areia       integer,
    argila      integer,
    textura     text,
    fonte       text
);
CREATE TABLE IF NOT EXISTS previsao_diaria (
    distrito_id integer REFERENCES distritos(id),
    obtida_em   date,
    data        date,
    chuva_mm    double precision,
    tmax        double precision,
    tmin        double precision,
    humidade    integer,
    PRIMARY KEY (distrito_id, obtida_em, data)
);
CREATE TABLE IF NOT EXISTS analises (
    id              bigserial PRIMARY KEY,
    criada_em       timestamptz DEFAULT now(),
    distrito_id     integer REFERENCES distritos(id),
    data_sementeira date,
    anterior        text,
    praga           text,
    prioridade      text,
    melhor_cultura  text,
    compatibilidade integer
);
CREATE TABLE IF NOT EXISTS traducoes (
    lingua    text,
    origem    text,                     -- frase em portugues, com os numeros trocados por {0}, {1}...
    traducao  text,
    revista   boolean DEFAULT false,    -- true depois de uma pessoa confirmar a traducao
    criada_em timestamptz DEFAULT now(),
    PRIMARY KEY (lingua, origem)
);
CREATE TABLE IF NOT EXISTS perguntas (
    id          bigserial PRIMARY KEY,
    criada_em   timestamptz DEFAULT now(),
    distrito_id integer REFERENCES distritos(id),
    pergunta    text,
    resposta    text
);
CREATE TABLE IF NOT EXISTS previsoes (
    id              bigserial PRIMARY KEY,
    criada_em       timestamptz DEFAULT now(),
    distrito_id     integer REFERENCES distritos(id),
    data_sementeira date,
    modelo          text,
    resultado       jsonb
);
"""


def executar(sql, parametros=(), ler=False, varios=False):
    """Corre um comando. Reabre a ligacao uma vez se ela tiver caido.
    Devolve None se a base de dados nao estiver disponivel."""
    global _ligacao
    if not ACTIVA:
        return None
    with _trinco:
        for tentativa in (1, 2):
            try:
                if _ligacao is None or _ligacao.closed:
                    _ligacao = psycopg.connect(URL, autocommit=True, connect_timeout=15)
                with _ligacao.cursor() as cur:
                    if varios:
                        cur.executemany(sql, parametros)
                    else:
                        cur.execute(sql, parametros)
                    return cur.fetchall() if ler else True
            except psycopg.Error as erro:
                _ligacao = None
                if tentativa == 2:
                    print("Base de dados indisponivel:", type(erro).__name__)
    return None


def ler(nome, distrito_id):
    """nome: 'clima', 'solo' ou 'previsao'. Devolve None se nao houver dados."""
    if nome == "clima":
        linhas = executar("SELECT mes, chuva_mm, temp_c, humidade FROM clima_mensal "
                          "WHERE distrito_id = %s ORDER BY mes", (distrito_id,), ler=True)
        if linhas and len(linhas) == 12:
            return [dict(zip(("mes", "chuva_mm", "temp_c", "humidade"), l)) for l in linhas]
    elif nome == "solo":
        linhas = executar("SELECT ph, areia, argila, textura, fonte FROM solo "
                          "WHERE distrito_id = %s", (distrito_id,), ler=True)
        if linhas:
            dados = dict(zip(("ph", "areia", "argila", "textura", "fonte"), linhas[0]))
            dados["estimativa_regional"] = False
            return dados
    elif nome == "previsao":
        linhas = executar("SELECT data::text, chuva_mm, tmax, tmin, humidade FROM previsao_diaria "
                          "WHERE distrito_id = %s AND obtida_em = %s ORDER BY data",
                          (distrito_id, date.today()), ler=True)
        if linhas:
            return [dict(zip(("data", "chuva_mm", "tmax", "tmin", "humidade"), l)) for l in linhas]
    return None


def guardar(nome, distrito_id, dados):
    """Devolve True se ficou guardado na base de dados."""
    if nome == "clima":
        return bool(executar(
            "INSERT INTO clima_mensal VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            [(distrito_id, m["mes"], m["chuva_mm"], m["temp_c"], m["humidade"]) for m in dados],
            varios=True))
    if nome == "solo":
        return bool(executar(
            "INSERT INTO solo VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (distrito_id, dados["ph"], dados["areia"], dados["argila"], dados["textura"], dados["fonte"])))
    if nome == "previsao":
        return bool(executar(
            "INSERT INTO previsao_diaria VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            [(distrito_id, date.today(), p["data"], p["chuva_mm"], p["tmax"], p["tmin"], p["humidade"])
             for p in dados], varios=True))
    return False


def registar_analise(distrito_id, dia, anterior, praga, prioridade, melhor):
    """Guarda o que foi pedido (sem dados pessoais), para saber o que os agricultores procuram."""
    executar("INSERT INTO analises (distrito_id, data_sementeira, anterior, praga, prioridade, "
             "melhor_cultura, compatibilidade) VALUES (%s, %s, %s, %s, %s, %s, %s)",
             (distrito_id, dia, anterior, praga, prioridade, melhor["id"], melhor["compatibilidade"]))


def registar_previsao(distrito_id, dia, modelo, resultado):
    """Guarda o que o modelo disse, para a equipa poder rever e comparar mais tarde."""
    executar("INSERT INTO previsoes (distrito_id, data_sementeira, modelo, resultado) "
             "VALUES (%s, %s, %s, %s)", (distrito_id, dia, modelo, json.dumps(resultado, ensure_ascii=False)))


ESQUEMA_CATALOGO = """
CREATE TABLE IF NOT EXISTS culturas (
    id         text PRIMARY KEY,
    ordem      integer,
    nome       text NOT NULL,
    cientifico text,
    tipo       text NOT NULL,           -- cereal, leguminosa, raiz, oleaginosa, horticola, permanente
    ciclo      integer[],               -- dias: minimo, maximo
    temp       double precision[],      -- C: minimo absoluto, optimo minimo, optimo maximo, maximo absoluto
    chuva      integer[],               -- mm no ciclo (ou por ano, nas permanentes), mesma ordem
    ph         double precision[],      -- mesma ordem
    textura    text[],                  -- solos preferidos: arenoso, franco, argiloso
    seca       double precision,        -- 0 a 1: tolerancia a seca
    renda      double precision,        -- 0 a 1: valor de venda
    solo       double precision,        -- 0 a 1: contributo para a fertilidade
    descricao  text,
    sinonimos  text[]
);
CREATE TABLE IF NOT EXISTS pragas (
    id        text PRIMARY KEY,
    ordem     integer,
    nome      text NOT NULL,
    culturas  text[],                   -- ids das culturas que ataca
    sinonimos text[]
);
CREATE TABLE IF NOT EXISTS rotacoes (
    id          text PRIMARY KEY,
    ordem       integer,                -- posicao na pagina
    prioridades text[],                 -- prioridades do agricultor em que aparece; '*' = sempre
    nome        text NOT NULL,
    factor      double precision,       -- multiplica a media da compatibilidade
    ordenar     text,                   -- compatibilidade, seca ou renda
    passos      jsonb,                  -- uma entrada por epoca: tipos de cultura, repetir ou actual
    resumo      text,                   -- {c1} {c2} {c3}: nomes; {c2_min}: em minusculas
    porque      text,
    beneficios  text[],
    riscos      text[],
    lacunas     text[]
);
"""
COLUNAS = {
    "culturas": ["id", "nome", "cientifico", "tipo", "ciclo", "temp", "chuva", "ph", "textura",
                 "seca", "renda", "solo", "descricao", "sinonimos"],
    "pragas": ["id", "nome", "culturas", "sinonimos"],
    "rotacoes": ["id", "prioridades", "nome", "factor", "ordenar", "passos", "resumo", "porque",
                 "beneficios", "riscos", "lacunas"],
}


def semear_catalogo(repor=False):
    """Copia app/data/catalogo.json para a base de dados. Por defeito so preenche tabelas vazias,
    para nao apagar o que a equipa ja editou la; repor=True substitui tudo."""
    if not executar(ESQUEMA_CATALOGO):
        return False
    catalogo = json.loads((AQUI / "data" / "catalogo.json").read_text(encoding="utf-8"))
    for tabela, colunas in COLUNAS.items():
        vazia = executar(f"SELECT count(*) FROM {tabela}", ler=True)[0][0] == 0
        if not (vazia or repor):
            continue
        if repor:
            executar(f"DELETE FROM {tabela}")
        linhas = []
        for n, item in enumerate(catalogo[tabela]):
            valores = [json.dumps(item[c], ensure_ascii=False) if c == "passos" else item[c] for c in colunas]
            linhas.append([item.get("ordem", n)] + valores)
        marcas = ", ".join(["%s"] * (len(colunas) + 1))
        executar(f"INSERT INTO {tabela} (ordem, {', '.join(colunas)}) VALUES ({marcas})", linhas, varios=True)
    return True


def carregar_catalogo():
    """Culturas, pragas e rotacoes: da base de dados; se nao houver, do ficheiro catalogo.json."""
    if ACTIVA and semear_catalogo():
        catalogo = {}
        for tabela, colunas in COLUNAS.items():
            linhas = executar(f"SELECT ordem, {', '.join(colunas)} FROM {tabela} ORDER BY ordem, id", ler=True)
            if not linhas:
                break
            catalogo[tabela] = [dict(zip(["ordem"] + colunas, l)) for l in linhas]
        else:
            return catalogo, "base de dados"
    return json.loads((AQUI / "data" / "catalogo.json").read_text(encoding="utf-8")), "ficheiro catalogo.json"


def preparar():
    """Cria as tabelas e carrega os distritos."""
    if not ACTIVA:
        print("Sem DATABASE_URL no .env (ou sem o driver psycopg): nada a fazer.")
        return False
    if not executar(ESQUEMA):
        return False
    distritos = json.loads((AQUI / "data" / "distritos.json").read_text(encoding="utf-8"))
    executar(
        "INSERT INTO distritos VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET "
        "cobertura_ano = EXCLUDED.cobertura_ano, cobertura = EXCLUDED.cobertura",
        [(d["id"], d["distrito"], d["provincia"], d["lat"], d["lon"], d["cobertura_ano"],
          json.dumps(d["cobertura"], ensure_ascii=False)) for d in distritos],
        varios=True)
    return True


if __name__ == "__main__":
    import sys
    if preparar():
        # `python -m app.db --repor-catalogo` substitui culturas, pragas e rotacoes pelo catalogo.json
        semear_catalogo(repor="--repor-catalogo" in sys.argv)
        for tabela in ("distritos", "culturas", "pragas", "rotacoes", "clima_mensal", "chuva_mensal",
                       "solo", "previsao_diaria", "analises", "previsoes", "perguntas"):
            print(f"{tabela:<16}", executar(f"SELECT count(*) FROM {tabela}", ler=True)[0][0], "linhas")
