# Modelos usados no xikomu

Este documento regista que método produz cada número mostrado na página, porque foi escolhido e quais são os seus limites. Actualizar sempre que um método mudar.

| O que a página mostra | Método | Tipo | Dados | Código |
|---|---|---|---|---|
| Compatibilidade de cada cultura (%) | Regras de encaixe: compara chuva no ciclo, temperatura, pH e textura do solo com as gamas ideais da cultura | Regras (não é aprendizagem automática) | NASA GLDAS, NASA POWER, SoilGrids, gamas no estilo FAO ECOCROP | `app/culturas.py`, `avaliar` e `pontuar` |
| "Em X de N anos a chuva chegou" | Frequência histórica: aplica a regra da chuva a cada ano do registo | Climatologia empírica | NASA GLDAS mensal, 2000–2026 | `app/culturas.py`, `avaliar` |
| Gravidade da época (seca grave a chuva excessiva grave) | Frequência histórica: chuva dos 4 meses depois da sementeira em cada ano, em % da mediana do distrito | Climatologia empírica | NASA GLDAS mensal | `app/historico.py`, `resumo` |
| Previsão para a época (seca, normal, chuvosa) | Regressão logística treinada com o índice do El Niño e a localização do distrito, calibrada com a frequência histórica | Modelo treinado (aprendizagem automática) | NOAA ONI, NASA GLDAS mensal | `treinar_modelo.py`, `app/previsao_epoca.py` |
| Melhores anos e anos mais perigosos | Ordenação das épocas passadas pela distância ao normal e pela compatibilidade da melhor cultura | Análise retrospectiva | NASA GLDAS mensal | `app/culturas.py`, `melhores_e_piores_anos` |
| Períodos a vigiar até à colheita, por cultura | Percorre o ciclo mais longo da cultura: previsão do tempo nos primeiros 16 dias; depois, para cada mês, quantas vezes desde 2000 choveu muito (mais de 250 mm ou de 1,5 vezes a mediana) ou pouco (menos de metade da mediana), e a temperatura normal face ao ideal da cultura | Previsão (forecast) + frequência histórica | Open-Meteo, NASA GLDAS mensal, NASA POWER | `app/ciclo.py`, `vigiar` |
| Mês de início das chuvas | Primeiro mês, de setembro a março, com 50 mm ou mais | Regra sobre o histórico | NASA GLDAS mensal | `app/historico.py`, `inicio_das_chuvas` |
| Tempo dos próximos 16 dias | Previsão numérica do tempo, obtida de um serviço externo | Previsão (forecast) | Open-Meteo | `app/clima.py`, `previsao` |
| Pontuação das rotações (0–100) | Média da compatibilidade das culturas, com bónus para leguminosas e variedade e penalização para monocultura | Regras agronómicas | Resultados acima | `app/culturas.py`, `rotacoes` |

## Porque estes métodos

- **Não há dados de colheitas medidas em Moçambique** para treinar um modelo supervisionado (árvore de decisão, Random Forest, XGBoost). Um modelo treinado sem esse alvo aprenderia apenas as nossas próprias regras.
- **A frequência histórica é verificável:** cada percentagem corresponde a uma contagem de anos que se pode conferir na tabela `chuva_mensal`.
- **É fácil de explicar a um agricultor:** "em 20 de 26 anos a chuva chegou".

## Limites conhecidos

- As gamas das culturas são valores aproximados, ainda por validar contra a base FAO ECOCROP e por técnicos locais.
- A frequência histórica descreve o passado e não distingue este ano dos outros. Para isso existe o modelo treinado descrito abaixo.
- Os dados de chuva são mensais e têm células de cerca de 28 km. Não mostram períodos secos dentro de um mês nem diferenças entre parcelas vizinhas.
- O registo tem 26 épocas. Acontecimentos raros (um em cada 50 anos) não aparecem.
- A vigilância do ciclo trabalha por mês inteiro: sabe que janeiro teve chuva muito forte em 8 de 27 anos, mas não em que dias. Risco alto quer dizer 1 em cada 4 anos ou mais; os limites foram escolhidos por nós. Não altera a percentagem de compatibilidade.
- Os limites das classes de gravidade (60%, 80%, 120%, 150% do normal) foram escolhidos por nós, não vêm de uma norma oficial.

## Onde ficam guardadas as previsões

Cada análise feita na página grava uma linha na tabela `previsoes` da base de dados (Neon): distrito, data de sementeira, nome e versão do modelo (`frequencia-historica-v1`, mais a previsão sazonal quando é usada) e o resultado em JSON (frequência das classes, compatibilidade e fiabilidade de cada cultura, pontuação das rotações). Serve para rever o que o sistema disse e, mais tarde, comparar com o que aconteceu.

## Modelo treinado: previsão para a época

- **O que prevê:** a probabilidade de a chuva dos 4 meses depois da sementeira ficar abaixo de 80% do normal (seca), entre 80% e 120% (normal) ou acima de 120% (chuvosa).
- **Entradas:** o índice ONI do El Niño e a sua tendência, conhecidos 2 a 4 meses antes da sementeira, cruzados com a latitude e a longitude do distrito.
- **Dados de treino:** 50085 exemplos de 27 épocas e 159 distritos, só para sementeiras de outubro a janeiro. Os distritos vizinhos são muito parecidos entre si, por isso a informação independente é a das 27 épocas, não a dos 50085 exemplos.
- **Validação:** deixa-se uma época inteira de fora de cada vez e mede-se o erro das probabilidades (índice de Brier) contra a frequência histórica da região.

| Modelo testado | Ganho geral | Sul | Centro | Norte |
|---|---|---|---|---|
| Regressão logística, depois de calibrar (escolhido) | +0.019 | +0.047 | +0.026 | -0.006 |
| Regressão logística, sem calibrar | +0.017 | +0.044 | +0.029 | -0.010 |
| Gradient boosting | -0.016 | -0.001 | +0.031 | -0.066 |

Ganho positivo quer dizer que o modelo erra menos do que a frequência histórica; +0.047 é um ganho de 4,7%.

- **Porque regressão logística e não árvores:** com tão poucas épocas independentes, o gradient boosting decorou anos concretos e ficou pior do que a frequência histórica. O modelo mais simples generalizou melhor.
- **Calibração:** a probabilidade final é 0.75 do modelo e 0.25 da frequência histórica da região. Sem isto o modelo dava cerca de 90% de seca para o sul em 2026, quando nas duas épocas de El Niño forte do registo a seca aconteceu em cerca de 68% dos casos.
- **Onde é usado:** só no sul e no centro, onde o ganho é positivo. No norte a página mostra apenas a frequência histórica e diz porquê.
- **Efeito nas culturas:** quando há previsão, os anos do registo parecidos com o que se prevê pesam mais na compatibilidade de cada cultura.
- **Limites:** o ganho é pequeno; só há duas épocas de El Niño forte no registo (2015/16 e 2023/24); o valor actual do índice (+2.46) está no limite do que o modelo viu no treino (-1.57 a +2.45).
- **Para voltar a treinar:** `uv run python treinar_modelo.py` (vai buscar o índice mais recente à NOAA).
