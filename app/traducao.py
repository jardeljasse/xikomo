"""
Traducao do texto da pagina para outras linguas.

Cada frase e traduzida uma vez pela API da Anthropic e guardada na tabela
traducoes (Neon). Depois disso vem sempre da base de dados, e a equipa pode
corrigir qualquer traducao la (coluna traducao; marcar revista = true).
Os numeros chegam aqui ja trocados por {0}, {1}...: assim "em 20 de 26 anos"
e "em 19 de 26 anos" sao a mesma frase e so se traduz uma vez.
"""
import json
import threading

import anthropic

from . import db

MODELO = "claude-haiku-5-5"   # o mais barato; cada frase so e traduzida uma vez
LINGUAS = {"en": "English", "ts": "Xitsonga (Tsonga / Xichangana, as spoken in southern Mozambique)"}
_memoria = {lingua: {} for lingua in LINGUAS}
_trinco = threading.Lock()
_cliente = None

INSTRUCOES = """You translate the interface of xikomu, a web page that advises smallholder farmers and extension officers in Mozambique on which crops to sow, where and when. The source text is Portuguese (Mozambican usage). The readers are farmers with little schooling reading on a phone, so the translation has to be plain, short and natural in the target language, never word-for-word.

You receive a JSON array of Portuguese strings and return the translations in the same order, one per input string.

Each string is a fragment of the page: a label, a button, a sentence of advice, or part of a sentence that the page joins with others. Translate each one on its own, and keep leading or trailing punctuation and spaces as they are so the fragments still join correctly.

Keep these exactly as they appear, because the page fills them in or they are names:
- placeholders in curly braces such as {0}, {1}
- the name xikomu, scientific names of plants, units (mm, °C, %, pH), and names of organisations and datasets (NASA, GLDAS, POWER, NOAA, Esri, SoilGrids, SDAE, INAM, El Niño, La Niña)
- names of Mozambican provinces and districts

Use the common local name of a crop in the target language when one exists; otherwise keep the Portuguese name. If a string has nothing to translate (a number, a symbol, a name), return it unchanged."""


def _pedir(lingua, textos):
    """Traduz uma lista pela API. Devolve lista do mesmo tamanho, ou None se falhar."""
    global _cliente
    if _cliente is None:
        _cliente = anthropic.Anthropic()
    try:
        resposta = _cliente.messages.create(
            model=MODELO,
            max_tokens=16000,
            system=INSTRUCOES,
            messages=[{"role": "user", "content":
                       f"Target language: {LINGUAS[lingua]}\n\n{json.dumps(textos, ensure_ascii=False)}"}],
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": {
                    "type": "object",
                    "properties": {"traducoes": {"type": "array", "items": {"type": "string"}}},
                    "required": ["traducoes"], "additionalProperties": False}},
            },
        )
    except anthropic.RateLimitError:
        print("Traducao: limite de pedidos atingido")
        return None
    except anthropic.APIStatusError as erro:
        print("Traducao: erro da API", erro.status_code, str(getattr(erro, "message", ""))[:200])
        return None
    except anthropic.APIConnectionError:
        print("Traducao: sem ligacao a API")
        return None
    if resposta.stop_reason != "end_turn":
        print("Traducao: resposta incompleta:", resposta.stop_reason)
        return None
    try:
        lista = json.loads(next(b.text for b in resposta.content if b.type == "text"))["traducoes"]
    except (StopIteration, ValueError, KeyError):
        return None
    return lista if len(lista) == len(textos) else None


def traduzir(lingua, textos):
    """{texto de origem: traducao} para os textos pedidos. O que falhar fica de fora
    (a pagina mostra o portugues)."""
    memoria = _memoria[lingua]
    falta = [t for t in dict.fromkeys(textos) if t not in memoria]
    if falta:
        linhas = db.executar("SELECT origem, traducao FROM traducoes WHERE lingua = %s AND origem = ANY(%s)",
                             (lingua, falta), ler=True)
        memoria.update(dict(linhas or []))
        falta = [t for t in falta if t not in memoria]
    if falta:
        with _trinco:   # um pedido de cada vez, para dois visitantes nao pagarem a mesma traducao
            falta = [t for t in falta if t not in memoria]
            for i in range(0, len(falta), 40):
                lote = falta[i:i + 40]
                novas = _pedir(lingua, lote)
                if novas is None:
                    continue
                memoria.update(zip(lote, novas))
                db.executar("INSERT INTO traducoes (lingua, origem, traducao) VALUES (%s, %s, %s) "
                            "ON CONFLICT DO NOTHING", [(lingua, o, t) for o, t in zip(lote, novas)], varios=True)
    return {t: memoria[t] for t in textos if t in memoria}
