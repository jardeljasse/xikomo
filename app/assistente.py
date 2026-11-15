"""
Assistente agricola: responde a perguntas so com os dados da analise do distrito.

Usa a API da Anthropic (ANTHROPIC_API_KEY no .env). Os dados da analise vao na
primeira mensagem; o modelo e instruido a recusar o que nao estiver la.
"""
import json

import anthropic

from . import db  # carrega o .env

MODELO = "claude-opus-5-5"
_cliente = None

INSTRUCOES = """És o assistente agrícola do xikomu, uma página que ajuda pequenos agricultores e técnicos de extensão de Moçambique a decidir o que semear, onde e quando.

Quem te escreve é normalmente um agricultor ou um técnico com pouco tempo, muitas vezes ao telemóvel. A primeira mensagem da conversa traz, entre as etiquetas <dados>, a análise que a página calculou para o distrito e a data de sementeira escolhidos: tempo esperado, previsão para a época, histórico de chuva desde 2000, solo, cobertura da terra, compatibilidade de cada cultura, rotações sugeridas, alertas e limites dos dados.

Responde apenas com base nesses dados. Eles são a única fonte em que a equipa confia, e uma resposta inventada pode levar alguém a perder uma sementeira. Por isso:
- Se a resposta está nos dados, dá-a e cita os números que a sustentam (por exemplo, "em 20 de 26 anos a chuva chegou").
- Se a pergunta é sobre agricultura neste distrito mas os dados não chegam para responder (preços, doses de adubo ou de pesticida, variedades, doenças, outra data ou outro distrito), diz claramente que a página não tem essa informação e sugere falar com o técnico de extensão (SDAE). Para outra data ou distrito, sugere refazer a análise na página.
- Se a pergunta não tem a ver com a análise (política, saúde, tecnologia, conversa geral), recusa numa frase e lembra o que podes responder.
- Não trates as percentagens como certezas. A compatibilidade não é probabilidade de boa colheita, e a previsão para a época é uma indicação. Quando for relevante, diz o que os dados não sabem; os limites estão no campo "incerteza".

Escreve em português de Moçambique simples, com frases curtas e sem termos técnicos por explicar. Responde em 2 a 5 frases, a não ser que peçam mais detalhe. A resposta aparece como texto simples num ecrã pequeno: não uses Markdown, títulos, negrito nem listas com símbolos. Se te escreverem noutra língua, responde nessa língua."""


def _resumo(a):
    """So o que o assistente precisa da analise, em texto estavel (para a cache do pedido)."""
    e = a.get("epoca")
    dados = {
        "distrito": a["distrito"], "provincia": a["provincia"], "data_de_sementeira": a["data"],
        "tempo_esperado": {k: v for k, v in a["tempo"].items() if k != "fonte"},
        "previsao_proximos_dias": a["previsao"],
        "alertas": [x["texto"] for x in a["alertas"]],
        "previsao_para_a_epoca": a.get("previsao_epoca"),
        "solo": a["solo"],
        "cobertura_da_terra_pct": a["cobertura"],
        "clima_normal_por_mes": a["clima_mensal"],
        "culturas_por_ordem": [
            {"nome": c["nome"], "tipo": c["tipo"], "compatibilidade_pct": c["compatibilidade"],
             "ciclo_dias": c["ciclo_dias"], "colheita_prevista": c["colheita"],
             "anos_com_chuva_adequada": c["fiabilidade"], "a_favor": c["razoes"], "contra": c["avisos"],
             "riscos_ate_a_colheita": c.get("ciclo")}
            for c in a["culturas"]],
        "culturas_permanentes": a.get("permanentes", []),
        "rotacoes": a["rotacoes"],
        "incerteza": a["incerteza"],
    }
    if e:
        dados["historico_da_epoca"] = {
            "epocas": e["n_epocas"], "periodo": e["periodo"], "chuva_normal_4_meses_mm": e["normal_mm"],
            "frequencia": [{"situacao": c["nome"], "vezes": c["vezes"], "o_que_acontece": c["acontece"],
                            "o_que_fazer": c["fazer"]} for c in e["classes"]],
            "ultima_epoca": e["ultima"], "inicio_das_chuvas": e["inicio_chuvas"],
            "todos_os_anos": [{k: x[k] for k in ("epoca", "chuva_mm", "pct_normal", "classe", "melhor_cultura")}
                              for x in e["anos_comparados"]["todos"]],
        }
    return json.dumps(dados, ensure_ascii=False, sort_keys=True)


def responder(analise, conversa):
    """conversa: [{'role': 'user'|'assistant', 'content': str}], a acabar numa pergunta do utilizador.
    Devolve (texto, erro). Um dos dois e None."""
    global _cliente
    if _cliente is None:
        _cliente = anthropic.Anthropic()   # le ANTHROPIC_API_KEY do ambiente
    mensagens = [dict(m) for m in conversa]
    mensagens[0]["content"] = f"<dados>\n{_resumo(analise)}\n</dados>\n\n{mensagens[0]['content']}"
    try:
        resposta = _cliente.beta.messages.create(
            model=MODELO,
            max_tokens=4000,
            system=INSTRUCOES,
            messages=mensagens,
            output_config={"effort": "low"},      # conversa curta: respostas rapidas
            cache_control={"type": "ephemeral"},  # as instrucoes e os dados repetem-se entre perguntas
            # se o modelo recusar por seguranca, a API tenta outro modelo recomendado
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError:
        return None, "A chave da API da Anthropic não é válida. Verifique ANTHROPIC_API_KEY no ficheiro .env."
    except anthropic.RateLimitError:
        return None, "O assistente está com muitos pedidos. Tente de novo dentro de um minuto."
    except anthropic.APIStatusError as erro:
        print("Assistente: erro da API", erro.status_code, getattr(erro, "message", "")[:200])
        return None, "O assistente não conseguiu responder agora. Tente de novo."
    except anthropic.APIConnectionError:
        return None, "Sem ligação ao serviço do assistente. Verifique a internet."
    if resposta.stop_reason == "refusal":
        return None, "O assistente não pode responder a essa pergunta."
    texto = "".join(b.text for b in resposta.content if b.type == "text").strip()
    if not texto:
        return None, "O assistente não devolveu resposta. Tente reformular a pergunta."
    return texto, None
