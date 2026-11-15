// xikomu: pagina unica. Fala com /api/distritos, /api/opcoes e /api/analise.
const $ = (id) => document.getElementById(id);
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];
const COBERTURA = { "Agua": "água", "Arvores": "árvores", "Vegetacao inundada": "vegetação inundada", "Culturas": "culturas", "Area construida": "área construída", "Solo nu": "solo nu", "Pastagem / mato": "pastagem / mato" };
const TIPOS = { cereal: "Cereal", leguminosa: "Leguminosa", raiz: "Raiz", oleaginosa: "Oleaginosa", horticola: "Hortícola", permanente: "Permanente" };
let ultimaConsulta = null;   // parametros da ultima analise, para o assistente e o mapa
let conversa = [];
let mapa = null;
// Servico publico da Esri com o uso e cobertura da terra (Sentinel-2, 10 m, um mapa por ano)
const LULC = "https://ic.imagery1.arcgis.com/arcgis/rest/services/Sentinel2_10m_LandCover/ImageServer";
const LULC_CORES = [["Água", "#1a5bab"], ["Árvores", "#358221"], ["Vegetação inundada", "#87d19e"], ["Culturas", "#ffdb5c"],
  ["Área construída", "#ed022a"], ["Solo nu", "#ede9e4"], ["Pastagem / mato", "#efcfa8"]];
let lulcUltimoAno = null;
let chuvaMeses = null;       // primeiro e ultimo mes com mapa de chuva, e a escala de cores

let distritos = [];
let prioridade = "equilibrio";
let hoje = new Date().toISOString().slice(0, 10);

const info = (texto) => `<details class="info"><summary aria-label="O que significa?">!</summary><span>${esc(texto)}</span></details>`;
const SELO_NIVEL = { alto: "alto", medio: "medio", bom: "bom" };
const dataLonga = (iso) => { const [a, m, d] = iso.split("-").map(Number); return `${d} de ${MESES[m - 1]} de ${a}`; };
const dataCurta = (iso) => { const [, m, d] = iso.split("-").map(Number); return `${d}/${m}`; };
const somarDias = (iso, n) => { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };

function mostrarErro(texto) {
  $("erro").textContent = texto;
  $("erro").hidden = !texto;
}

async function pedir(url) {
  let r;
  try { r = await fetch(url); } catch { throw new Error("Sem ligação ao servidor. Verifique a internet e tente de novo."); }
  if (!r.ok) {
    const corpo = await r.json().catch(() => ({}));
    throw new Error(typeof corpo.detail === "string" ? corpo.detail : "O servidor não conseguiu responder. Tente de novo.");
  }
  return r.json();
}

// ---------- Formulario ----------
function encherDistritos(provincia, escolhido) {
  const lista = distritos.filter((d) => d.provincia === provincia);
  $("distrito").innerHTML = '<option value="">Escolha o distrito</option>' +
    lista.map((d) => `<option value="${d.id}" data-nt>${esc(d.distrito)}</option>`).join("");
  $("distrito").disabled = !lista.length;
  if (escolhido != null) $("distrito").value = escolhido;
}

async function iniciar() {
  try {
    const [ds, op] = await Promise.all([pedir("/api/distritos"), pedir("/api/opcoes")]);
    distritos = ds;
    hoje = op.hoje;
    const provincias = [...new Set(ds.map((d) => d.provincia))].sort((a, b) => a.localeCompare(b, "pt"));
    $("provincia").innerHTML = '<option value="">Escolha a província</option>' +
      provincias.map((p) => `<option data-nt>${esc(p)}</option>`).join("");
    $("prioridades").innerHTML = Object.entries(op.prioridades).map(([k, v]) =>
      `<button type="button" role="radio" data-p="${k}" aria-checked="${k === prioridade}">${esc(v)}</button>`).join("");
    $("data").min = hoje;
    $("data").max = op.data_max;
    $("data").value = hoje;
    $("lista-culturas").innerHTML = op.culturas.map((c) => `
      <article class="cartao">
        <img class="foto" src="img/${c.id}.jpg" alt="" loading="lazy">
        <span class="selo tipo">${TIPOS[c.tipo]}</span>
        <h3>${esc(c.nome)}</h3>
        <p class="cient"><i>${esc(c.cientifico)}</i></p>
        <p class="desc">${esc(c.descricao)}</p>
        <dl>
          <dt>Ciclo</dt><dd>${c.ciclo[0]}–${c.ciclo[1]} dias</dd>
          <dt>Água no ciclo</dt><dd>${c.chuva[1]}–${c.chuva[2]} mm</dd>
          <dt>Temperatura</dt><dd>${c.temp[1]}–${c.temp[2]} °C</dd>
          <dt>pH do solo</dt><dd>${c.ph[1]}–${c.ph[2]}</dd>
          <dt>Solo</dt><dd>${esc(c.textura.join(", "))}</dd>
        </dl>
      </article>`).join("");
    pedir("img/creditos.json").then((cr) => {
      $("creditos").innerHTML = Object.values(cr).map((x) =>
        `<li><a href="${esc(x.pagina)}" target="_blank" rel="noopener">${esc(x.titulo)}</a> — ${esc(x.autor)}, ${esc(x.licenca)}</li>`).join("");
    }).catch(() => {});
    // Ligacao directa, ex.: /?distrito=7&data=2026-11-20 (util para partilhar e para os SMS)
    const q = new URLSearchParams(location.search);
    const d = distritos.find((x) => String(x.id) === q.get("distrito"));
    if (d) {
      $("provincia").value = d.provincia;
      encherDistritos(d.provincia, d.id);
      if (/^\d{4}-\d{2}-\d{2}$/.test(q.get("data") || "")) $("data").value = q.get("data");
      $("formulario").requestSubmit();
    }
  } catch (e) {
    mostrarErro(e.message);
  }
}

$("provincia").addEventListener("change", (e) => { encherDistritos(e.target.value); $("local-nota").hidden = true; });
$("atalhos").addEventListener("click", (e) => {
  if (e.target.dataset.dias != null) $("data").value = somarDias(hoje, Number(e.target.dataset.dias));
});
$("prioridades").addEventListener("click", (e) => {
  if (!e.target.dataset.p) return;
  prioridade = e.target.dataset.p;
  $("prioridades").querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", b.dataset.p === prioridade));
});

$("usar-local").addEventListener("click", () => {
  if (!navigator.geolocation) return mostrarErro("Este aparelho não permite obter a localização. Escolha o distrito manualmente.");
  mostrarErro("");
  navigator.geolocation.getCurrentPosition((pos) => {
    const { latitude: lat, longitude: lon } = pos.coords;
    const dist = (d) => (d.lat - lat) ** 2 + ((d.lon - lon) * Math.cos(lat * Math.PI / 180)) ** 2;
    const perto = distritos.reduce((a, b) => (dist(a) < dist(b) ? a : b));
    if (Math.sqrt(dist(perto)) > 3) return mostrarErro("A sua localização parece estar fora de Moçambique. Escolha o distrito manualmente.");
    $("provincia").value = perto.provincia;
    encherDistritos(perto.provincia, perto.id);
    $("local-nota").innerHTML = `✓ Distrito mais próximo: <span data-nt>${esc(perto.distrito)}, ${esc(perto.provincia)}</span>`;
    $("local-nota").hidden = false;
  }, () => mostrarErro("Não foi possível obter a localização. Escolha o distrito manualmente."));
});

$("formulario").addEventListener("submit", async (e) => {
  e.preventDefault();
  mostrarErro("");
  if (!$("distrito").value) return mostrarErro("Escolha a província e o distrito.");
  if (!$("data").value) return mostrarErro("Escolha a data em que pensa semear.");
  const botao = $("enviar");
  botao.disabled = true;
  botao.textContent = "A analisar…";
  try {
    const q = new URLSearchParams({
      distrito_id: $("distrito").value, data: $("data").value, prioridade,
    });
    mostrar(await pedir("/api/analise?" + q));
    ultimaConsulta = Object.fromEntries(q);
    reiniciarConversa();
    if (window.matchMedia("(max-width: 900px)").matches) $("painel").scrollIntoView();
  } catch (erro) {
    mostrarErro(erro.message);
  } finally {
    botao.disabled = false;
    botao.innerHTML = 'Ver culturas recomendadas <span aria-hidden="true">→</span>';
  }
});

// ---------- Resultado ----------
function blocoSazonal(s) {
  if (!s) return "";
  if (!s.usada) return `<p class="nota">Previsão para esta época: não disponível. ${esc(s.motivo)} Mostra-se abaixo o que foi habitual. Estado do El Niño (${esc(s.oni_periodo)}): ${esc(s.fase)}.</p>`;
  const p = s.probabilidades, h = s.habitual, nomes = { seca: "Época seca", normal: "Época normal", chuvosa: "Época chuvosa" };
  return `
    <div class="sazonal">
      <h4>Previsão para esta época${info("Probabilidade calculada por um modelo treinado com " + s.epocas_treino + " épocas, a partir do estado do El Niño. Seca quer dizer menos de 80% da chuva normal; chuvosa, mais de 120%. Em anos que o modelo não viu no treino, errou " + Math.round(100 * s.validacao.ganho) + "% menos do que a frequência histórica nesta zona do país: é uma indicação, não uma certeza.")}</h4>
      <p class="nota" style="margin:0">Estado do Pacífico em ${esc(s.oni_periodo)}: <b>${esc(s.fase)}</b> (índice ${s.oni > 0 ? "+" : ""}${s.oni}).</p>
      <div class="tres">${["seca", "normal", "chuvosa"].map((c) => `<div><span>${nomes[c]}</span><b>${p[c]}%</b><small>habitual: ${h[c]}%</small></div>`).join("")}</div>
      <p class="nota" style="margin:0">Modelo: ${esc(s.modelo)}. A ordem das culturas abaixo já tem esta previsão em conta.</p>
    </div>`;
}

function blocoEpoca(e, sazonal) {
  const u = e.ultima, ic = e.inicio_chuvas;
  return blocoSazonal(sazonal) + `
    <div class="linha-titulo"><h3>Como costuma ser a época${info("Para cada ano desde 2000, somámos a chuva dos 4 meses depois desta data e comparámos com o normal do distrito. A percentagem mostra em quantas épocas aconteceu cada situação. É o que foi habitual, não uma previsão para este ano.")}</h3><span>${e.n_epocas} épocas · ${esc(e.periodo)}</span></div>
    <p class="nota" style="margin-top:0">Chuva normal nos 4 meses depois desta data: <b>${e.normal_mm} mm</b>.
      Última época registada (${esc(u.epoca)}): <b>${u.chuva_mm} mm</b>, ${u.pct_normal}% do normal — ${esc(u.classe.toLowerCase())}.
      ${ic && ic.mais_comum ? `As chuvas costumam começar em <b>${esc(ic.mais_comum)}</b>.` : ""}</p>
    <ul class="classes">${e.classes.map((c) => `
      <li>
        <div class="topo-classe">${esc(c.nome)}<span>${c.vezes} de ${e.n_epocas} épocas · ${c.pct}%</span></div>
        <div class="barra"><i class="${c.nivel}" style="width:${c.pct}%"></i></div>
        <details><summary>O que acontece e o que fazer</summary>
          <p><b>O que acontece:</b> ${esc(c.acontece)}</p><p><b>O que fazer:</b> ${esc(c.fazer)}</p>
        </details>
      </li>`).join("")}
    </ul>`;
}

function mostrarHistoria(a) {
  const e = a.epoca;
  if (!e) {
    $("historia-conteudo").innerHTML = '<p class="caixa-vazia">Não há histórico de chuva disponível para este distrito.</p>';
    return;
  }
  const c = e.anos_comparados, ic = e.inicio_chuvas;
  const ano = (x, mau) => `
    <div class="ano">
      <div><b>${esc(x.epoca)}</b><small>${esc(x.classe)}</small>${mau ? `<a class="video" target="_blank" rel="noopener" href="https://www.youtube.com/results?search_query=${encodeURIComponent((x.pct_normal < 100 ? "seca" : "cheias") + " Moçambique " + (x.epoca.includes("/") ? x.ano + 1 : x.ano))}">Ver vídeos deste ano ↗</a>` : ""}</div>
      <div style="text-align:right">${x.chuva_mm} mm · ${x.pct_normal}% do normal
        <small>${mau ? "cultura que melhor teria aguentado" : "melhor cultura"}: ${esc(x.melhor_cultura)} (${x.pontuacao}%)</small></div>
    </div>`;
  $("historia-conteudo").innerHTML = `
    <p class="nota" style="margin:0 0 16px"><span data-nt>${esc(a.distrito)}, ${esc(a.provincia)}</span> · sementeira a ${dataLonga(a.data).replace(/ de \d{4}$/, "")} de cada ano · ${e.n_epocas} épocas (${esc(e.periodo)})</p>
    <div class="dois">
      <div class="anos"><h3>Os melhores anos${info("Épocas com chuva perto do normal em que a melhor cultura teve a compatibilidade mais alta. A percentagem do normal compara a chuva desse ano com a chuva habitual do distrito (100% = normal).")}</h3>
        <figure class="foto-evento"><img src="img/ev_colheita.jpg" alt="Agricultor a colher milho" loading="lazy"><figcaption>Uma boa colheita. Imagem ilustrativa, não é deste distrito.</figcaption></figure>
        ${c.melhores.map((x) => ano(x, false)).join("")}</div>
      <div class="anos"><h3>Os anos mais perigosos${info("As épocas mais afastadas do normal: muito menos chuva (seca) ou muito mais (cheias). Abaixo de 60% ou acima de 150% do normal consideramos grave.")}</h3>
        ${c.piores.length ? (c.piores[0].pct_normal > 100
          ? '<figure class="foto-evento"><img src="img/ev_cheia.jpg" alt="Pessoas à espera de socorro num telhado rodeado de água" loading="lazy"><figcaption>Cheias de 2000 em Moçambique. Imagem ilustrativa, não é deste distrito.</figcaption></figure>'
          : '<figure class="foto-evento"><img src="img/ev_seca.jpg" alt="Campo de milho seco" loading="lazy"><figcaption>Milho num ano de seca. Imagem ilustrativa, não é deste distrito.</figcaption></figure>') : ""}
        ${c.piores.length ? c.piores.map((x) => ano(x, true)).join("") : '<p class="nota">Nenhuma época fora do normal no registo.</p>'}</div>
    </div>
    <p class="nota">Semear na altura certa, com a cultura certa, pode ajudar muitas famílias das zonas rurais deste distrito a colher mais e a perder menos em anos difíceis.</p>
    <details class="avancado">
      <summary>Detalhes avançados: mapa, todos os anos e dados do distrito</summary>
      <h4>Mapa de <span data-nt>${esc(a.distrito)}, ${esc(a.provincia)}</span></h4>
      <div id="mapa" role="img" aria-label="Mapa com o distrito de ${esc(a.distrito)} realçado"></div>
      <div class="mapa-controlos">
        <label>Camada <select id="mapa-camada" aria-label="Camada do mapa">
          <option value="lulc">Uso e cobertura da terra</option>
          <option value="chuva">Chuva do mês</option>
          <option value="">Só o mapa de base</option></select></label>
        <label id="mapa-mes-rotulo" hidden>Mês <select id="mapa-mes" aria-label="Mês do mapa de chuva">${MESES.map((m, i) => `<option value="${i + 1}">${m}</option>`).join("")}</select></label>
        <label>Ano <select id="mapa-ano" aria-label="Ano do mapa"></select></label>
        <span class="legenda" id="mapa-legenda"></span>
      </div>
      <p class="nota">Contorno a brilhar: o distrito escolhido. Linha tracejada: a província. As duas camadas vêm em directo dos serviços do ArcGIS: cobertura da terra (Sentinel-2, 10 m, um mapa por ano) e chuva mensal da NASA (células de 28 km, suavizadas no desenho).</p>
      ${ic ? `<h4>Mês em que as chuvas começaram (${ic.epocas} épocas)</h4>
        <p class="nota" style="margin:0">${ic.por_mes.map((m) => `${esc(m.mes)}: ${m.vezes}`).join(" · ")}. Conta-se o primeiro mês, a partir de setembro, com 50 mm ou mais.</p>` : ""}
      <h4>Chuva nos 4 meses depois da sementeira, ano a ano</h4>
      <div class="tabela-rolar"><table>
        <thead><tr><th>Época</th><th>Chuva</th><th>% do normal</th><th>Situação</th><th>Melhor cultura</th></tr></thead>
        <tbody>${c.todos.map((x) => `<tr><td>${esc(x.epoca)}</td><td>${x.chuva_mm} mm</td><td>${x.pct_normal}%</td><td>${esc(x.classe)}</td><td>${esc(x.melhor_cultura)} (${x.pontuacao}%)</td></tr>`).join("")}</tbody>
      </table></div>
      <h4>Clima normal mês a mês (NASA POWER)</h4>
      <div class="tabela-rolar"><table>
        <thead><tr><th>Mês</th><th>Chuva</th><th>Temperatura</th><th>Humidade</th></tr></thead>
        <tbody>${a.clima_mensal.map((m) => `<tr><td>${MESES[m.mes - 1]}</td><td>${m.chuva_mm} mm</td><td>${m.temp_c} °C</td><td>${m.humidade}%</td></tr>`).join("")}</tbody>
      </table></div>
      <h4>Solo e cobertura da terra</h4>
      <p class="nota" style="margin:0">Solo ${esc(a.solo.textura)}, pH ${a.solo.ph}, ${a.solo.areia}% de areia e ${a.solo.argila}% de argila. Fonte: ${esc(a.solo.fonte)}.
        ${a.cobertura ? `Cobertura da terra em ${a.cobertura_ano}: ${Object.entries(a.cobertura).filter(([, v]) => v > 0).sort((x, y) => y[1] - x[1]).map(([k, v]) => `${esc(COBERTURA[k] || k)} ${v}%`).join(" · ")}.` : ""}</p>
      <p class="nota">Modelo: ${esc(e.modelo)} (frequência histórica, sem aprendizagem automática). Cada análise fica guardada na base de dados para revisão.</p>
    </details>`;
  const detalhes = $("historia-conteudo").querySelector(".avancado");
  detalhes.addEventListener("toggle", () => { if (detalhes.open) desenharMapa($("distrito").value); });
  if (new URLSearchParams(location.search).has("avancado")) { $("historia").querySelector("details").open = true; detalhes.open = true; }   // ligacao directa aos detalhes
}

// ---------- Mapa do distrito ----------
async function desenharMapa(distritoId) {
  if (!window.L || !$("mapa")) return;
  if (mapa) { mapa.remove(); mapa = null; }
  try {
    const g = await pedir("/api/geo/" + distritoId);
    mapa = L.map("mapa", { scrollWheelZoom: false });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 13, attribution: "© OpenStreetMap" }).addTo(mapa);
    if (g.provincia) L.geoJSON(g.provincia, { style: { color: "#0f172a", weight: 1.5, dashArray: "6 6", fill: false }, interactive: false }).addTo(mapa);
    const distrito = L.geoJSON(g.distrito, { style: { color: "#f59e0b", weight: 4, fillColor: "#facc15", fillOpacity: 0.18, className: "brilho" } }).addTo(mapa);
    mapa.fitBounds(distrito.getBounds().pad(0.6));

    // Cobertura da terra em directo: pede-se uma imagem do que esta a vista, para o ano escolhido
    if (!lulcUltimoAno) {
      try {
        const servico = await (await fetch(LULC + "?f=json")).json();
        lulcUltimoAno = new Date(servico.timeInfo.timeExtent[1]).getUTCFullYear();
      } catch { lulcUltimoAno = 2023; }
    }
    if (!chuvaMeses) chuvaMeses = await pedir("/api/chuva-meses").catch(() => null);
    if (!chuvaMeses) $("mapa-camada").querySelector('[value="chuva"]').remove();
    mapa.createPane("cobertura").style.zIndex = 350;   // por cima do mapa de base, por baixo dos contornos
    let camada = null;
    const prepararControlos = () => {
      // os anos e a legenda dependem da camada escolhida
      const tipo = $("mapa-camada").value, anos = [];
      const [de, ate] = tipo === "chuva" ? [chuvaMeses.primeiro[0], chuvaMeses.ultimo[0]] : [2017, lulcUltimoAno];
      for (let a = ate; a >= de; a--) anos.push(a);
      $("mapa-ano").innerHTML = anos.map((a) => `<option>${a}</option>`).join("");
      $("mapa-ano").parentElement.hidden = !tipo;
      $("mapa-mes-rotulo").hidden = tipo !== "chuva";
      if (tipo === "chuva") $("mapa-mes").value = chuvaMeses.ultimo[1];
      $("mapa-legenda").innerHTML = tipo === "chuva"
        ? chuvaMeses.escala.map((e) => `<span><i style="background:${e.cor}"></i>${e.desde_mm}+ mm</span>`).join("")
        : tipo === "lulc" ? LULC_CORES.map(([n, c]) => `<span><i style="background:${c}"></i>${n}</span>`).join("") : "";
    };
    const actualizar = () => {
      const tipo = $("mapa-camada").value;
      if (!tipo) { if (camada) { mapa.removeLayer(camada); camada = null; } return; }
      const limites = mapa.getBounds(), tamanho = mapa.getSize(), ano = Number($("mapa-ano").value);
      const so = L.CRS.EPSG3857.project(limites.getSouthWest()), ne = L.CRS.EPSG3857.project(limites.getNorthEast());
      const caixa = `${so.x},${so.y},${ne.x},${ne.y}`;
      const url = tipo === "chuva"
        ? `/api/chuva-mapa.png?ano=${ano}&mes=${$("mapa-mes").value}&bbox=${caixa}&largura=${tamanho.x}&altura=${tamanho.y}`
        : `${LULC}/exportImage?bbox=${caixa}&bboxSR=3857&imageSR=3857&size=${tamanho.x},${tamanho.y}` +
          `&format=png32&transparent=true&time=${Date.UTC(ano, 0, 1)},${Date.UTC(ano, 11, 31)}&f=image`;
      const nova = L.imageOverlay(url, limites, { opacity: 0.65, interactive: false, pane: "cobertura" });
      nova.once("load", () => { if (camada) mapa.removeLayer(camada); camada = nova; });
      nova.once("error", () => { mapa.removeLayer(nova); });   // ex.: mes sem dados; fica a imagem anterior
      nova.addTo(mapa);
    };
    mapa.on("moveend", actualizar);
    $("mapa-camada").onchange = () => { prepararControlos(); actualizar(); };
    $("mapa-ano").onchange = actualizar;
    $("mapa-mes").onchange = actualizar;
    prepararControlos();
    actualizar();
  } catch (e) {
    $("mapa").textContent = "Não foi possível carregar o mapa.";
  }
}

// ---------- Assistente ----------
function fala(papel, texto) {
  const p = document.createElement("p");
  p.className = "fala " + papel;
  p.textContent = texto;
  $("conversa").appendChild(p);
  $("conversa").scrollTop = $("conversa").scrollHeight;
  return p;
}

function reiniciarConversa() {
  conversa = [];
  $("conversa").innerHTML = "";
  fala("assistant", "Pode perguntar sobre a análise de " + $("distrito").selectedOptions[0].textContent + ". Só respondo com os dados desta página.");
  $("sugestoes").hidden = false;
  $("pergunta").disabled = $("perguntar").disabled = false;
}

async function perguntar(texto) {
  texto = texto.trim();
  if (!texto || !ultimaConsulta) return;
  fala("user", texto);
  conversa.push({ role: "user", content: texto });
  $("pergunta").value = "";
  $("pergunta").disabled = $("perguntar").disabled = true;
  $("sugestoes").hidden = true;
  const espera = fala("assistant espera", "A pensar…");
  try {
    const r = await fetch("/api/assistente", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...ultimaConsulta, distrito_id: Number(ultimaConsulta.distrito_id), mensagens: conversa.slice(-12) }),
    });
    const corpo = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof corpo.detail === "string" ? corpo.detail : "O assistente não conseguiu responder. Tente de novo.");
    espera.remove();
    fala("assistant", corpo.resposta);
    conversa.push({ role: "assistant", content: corpo.resposta });
  } catch (e) {
    espera.remove();
    conversa.pop();   // a pergunta nao teve resposta: nao fica no historico enviado
    fala("erro-fala", e.message === "Failed to fetch" ? "Sem ligação ao servidor. Tente de novo." : e.message);
  } finally {
    $("pergunta").disabled = $("perguntar").disabled = false;
    $("pergunta").focus({ preventScroll: true });
  }
}
function abrirAssistente(abrir) {
  $("janela-assistente").hidden = !abrir;
  $("bot-botao").setAttribute("aria-expanded", abrir);
  if (abrir && !$("pergunta").disabled) $("pergunta").focus({ preventScroll: true });
}
$("bot-botao").addEventListener("click", () => abrirAssistente($("janela-assistente").hidden));
$("fechar-assistente").addEventListener("click", () => abrirAssistente(false));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") abrirAssistente(false); });
$("pergunta-form").addEventListener("submit", (e) => { e.preventDefault(); perguntar($("pergunta").value); });
$("sugestoes").addEventListener("click", (e) => { if (e.target.tagName === "BUTTON") perguntar(e.target.textContent); });


function cartaoCultura(c, n) {
  return `
    <div class="cultura">
      <span class="num">${n}</span>
      <h3>${esc(c.nome)}</h3>
      <span class="selo">${c.compatibilidade}% compatibilidade</span>
      <div class="corpo">
        <p class="cient"><i>${esc(c.cientifico)}</i> · ciclo de ${c.ciclo_dias[0]}–${c.ciclo_dias[1]} dias · colheita por volta de ${dataLonga(c.colheita)}</p>
        <p class="desc">${esc(c.descricao)}</p>
        <ul class="pontos">
          ${c.razoes.map((r) => `<li>${esc(r)}</li>`).join("")}
          ${c.avisos.map((r) => `<li class="aviso">${esc(r)}</li>`).join("")}
        </ul>
        ${c.ciclo ? blocoCiclo(c.ciclo, n === 1) : ""}
      </div>
    </div>`;
}

// O que vigiar entre a sementeira e a colheita (ciclo mais longo da cultura)
function blocoCiclo(k, aberto) {
  const n = k.riscos.length;
  const resumo = n
    ? `${n} ${n === 1 ? "período" : "períodos"} a vigiar nos ${k.dias} dias até à colheita${k.altos ? ` (${k.altos} de risco alto)` : ""}`
    : `Sem períodos de risco conhecidos nos ${k.dias} dias até à colheita`;
  return `
    <details class="ciclo ${k.altos ? "tem-alto" : ""}" ${aberto ? "open" : ""}>
      <summary>${resumo}</summary>
      <p class="nota" style="margin-top:6px">Da sementeira até ${dataLonga(k.fim)}.${aberto ? info("Percorremos o ciclo mais longo da cultura, do dia da sementeira até à colheita. Nos primeiros 16 dias usamos a previsão do tempo; no resto, o que aconteceu em cada mês desde 2000. Risco alto: aconteceu em pelo menos 1 de cada 4 anos. São períodos em que deve redobrar os cuidados, não certezas.") : ""}</p>
      ${n ? `<ul class="vigiar">${k.riscos.map((r) => `
        <li class="${r.nivel}"><b>${esc(r.quando)}</b> · ${esc(r.fase)}<br><strong>${esc(r.risco)}</strong> <small>(${esc(r.base)})</small><br>${esc(r.cuidado)}</li>`).join("")}</ul>`
          : '<p class="nota">Mantenha a vigilância normal e acompanhe os alertas.</p>'}
      ${k.omitidos ? `<p class="nota">Há mais ${k.omitidos} períodos de menor risco.</p>` : ""}
    </details>`;
}

function mostrar(a) {
  const t = a.tempo;
  const previsao = t.fonte === "previsao";
  const dias = a.previsao.map((p) => `
    <div class="dia ${p.data === a.data ? "escolhido" : ""}">
      <b>${dataCurta(p.data)}</b>
      <span class="chuva">${p.chuva_mm.toFixed(0)} mm</span><br>${p.tmax.toFixed(0)}° / ${p.tmin.toFixed(0)}°
    </div>`).join("");
  const alertas = a.alertas.map((x) => `
    <li class="${x.nivel}">${x.data ? `<time>${dataLonga(x.data)}</time>` : ""}${esc(x.texto)}</li>`).join("");
  const cob = a.cobertura
    ? Object.entries(a.cobertura).filter(([, v]) => v >= 1).sort((x, y) => y[1] - x[1])
        .map(([k, v]) => `${esc(COBERTURA[k] || k.toLowerCase())} ${v.toFixed(0)}%`).join(" · ")
    : "";

  // periodo que a recomendacao cobre: da sementeira ate a colheita mais tardia das 3 culturas mostradas
  const tardia = a.culturas.slice(0, 3).filter((c) => c.ciclo)
    .reduce((m, c) => (!m || c.ciclo.fim > m.ciclo.fim ? c : m), null);
  const fimPeriodo = tardia ? tardia.ciclo.fim : a.data;
  const futuros = a.previsao.filter((p) => p.data >= a.data);
  const prevAte = futuros.length ? futuros[futuros.length - 1].data : null;
  const periodo = tardia ? `
    <p class="periodo">Período coberto por esta recomendação: <b>${dataLonga(a.data)} a ${dataLonga(fimPeriodo)}</b>
    (${tardia.ciclo.dias} dias, até à colheita mais tardia das 3 culturas recomendadas: ${esc(tardia.nome)}).<br>
    ${prevAte ? `Previsão do tempo: <b>${dataLonga(a.data)} a ${dataLonga(prevAte)}</b>. ` : ""}${prevAte && prevAte < fimPeriodo ? `De ${dataLonga(somarDias(prevAte, 1))} a ${dataLonga(fimPeriodo)}: o que costuma acontecer em cada mês, com dados desde 2000.` : !prevAte ? "Sem previsão do tempo para esta data: usamos o que costuma acontecer em cada mês, com dados desde 2000." : ""}</p>` : "";

  $("resultado").innerHTML = `
    <p class="etiqueta">Recomendação para ${dataLonga(a.data)}${fimPeriodo > a.data ? ` – ${dataLonga(fimPeriodo)}` : ""}</p>
    <h2>Culturas para a sua machamba.</h2>
    <p class="sub" data-nt>${esc(a.distrito)}, ${esc(a.provincia)}</p>

    ${(a.entradas || []).length ? `<div class="entradas">${a.entradas.map((x) => x.reconhecido
      ? `${esc(x.campo)}: reconhecemos “${esc(x.texto)}” como <b>${esc(x.reconhecido)}</b>.`
      : `${esc(x.campo)}: não reconhecemos “${esc(x.texto)}”. Fica registado para a equipa, mas não entra no cálculo.`).join("<br>")}</div>` : ""}

    <div class="mosaico">${a.culturas.slice(0, 4).map((c, i) => `
      <figure><img src="img/${c.id}.jpg" alt="${esc(c.nome)}"><figcaption>${i + 1}. ${esc(c.nome)} · ${c.compatibilidade}%</figcaption></figure>`).join("")}
    </div>

    <div class="linha-titulo"><h3>Tempo esperado${info(previsao ? "Previsão do tempo para o dia escolhido. A humidade é a percentagem de vapor de água no ar: acima de 80% favorece doenças de fungos." : "Valores normais para este mês, calculados com muitos anos de dados. Não são uma previsão para este ano. A humidade é a percentagem de vapor de água no ar.")}</h3><span class="selo ${t.fonte}">${esc(t.rotulo)}</span></div>
    ${periodo}
    <div class="trio">
      <div><b>Chuva</b><span class="valor">${t.chuva_mm} <small>mm</small></span><span>${esc(t.chuva_rotulo)}</span></div>
      <div><b>Temperatura</b><span class="valor">${t.temp_c} <small>°C</small></span><span>${previsao ? `máx. ${t.tmax}° · mín. ${t.tmin}°` : "média do mês"}</span></div>
      <div><b>Humidade</b><span class="valor">${t.humidade} <small>%</small></span><span>${previsao ? "média do dia" : "média do mês"}</span></div>
    </div>
    <p class="nota">${previsao ? `Chuva prevista nos ${t.dias_janela} dias a partir desta data: <b>${t.chuva_7dias_mm} mm</b>. ` : ""}${esc(t.nota)}</p>
    <p class="nota">Chuva média nos 4 meses depois da sementeira: <b>${a.chuva_epoca_mm} mm</b>. Solo ${esc(a.solo.textura)}, pH ${a.solo.ph}.${cob ? ` Cobertura da terra no distrito (${a.cobertura_ano}): ${cob}.` : ""}</p>

    ${alertas ? `<ul class="alertas">${alertas}</ul>` : ""}
    ${dias ? `<div class="linha-titulo"><h3>Próximos ${a.previsao.length} dias</h3><span>chuva · máx. / mín.</span></div><div class="dias">${dias}</div>` : ""}

    ${a.epoca ? blocoEpoca(a.epoca, a.previsao_epoca) : ""}

    <div class="linha-titulo"><h3>Mais recomendadas${info("A percentagem de compatibilidade diz até que ponto a chuva, a temperatura e o solo do local servem para a cultura. 95% é o máximo: quase tudo dentro do ideal. Abaixo de 50%, pelo menos um factor está longe do que a cultura precisa. Não é a probabilidade de boa colheita.")}</h3><span>${a.culturas.length} culturas analisadas</span></div>
    <div id="top">${a.culturas.slice(0, 3).map((c, i) => cartaoCultura(c, i + 1)).join("")}</div>
    <div id="resto" hidden>${a.culturas.slice(3).map((c, i) => cartaoCultura(c, i + 4)).join("")}</div>
    ${a.culturas.length > 3 ? '<button type="button" class="mais" id="ver-mais">Ver mais</button>' : ""}

    ${(a.permanentes || []).length ? `
    <div class="linha-titulo"><h3>Culturas permanentes${info("Culturas que ficam vários anos no terreno, como a cana e as árvores de fruto. Não entram na rotação. A percentagem compara a chuva de um ano inteiro, a temperatura média e o solo com o que a cultura precisa.")}</h3><span>fora da rotação</span></div>
    ${a.permanentes.map((c) => `
      <div class="permanente">
        <img src="img/${c.id}.jpg" alt="" loading="lazy">
        <h3>${esc(c.nome)}</h3><span class="selo">${c.compatibilidade}% compatibilidade</span>
        <p>${esc(c.descricao)} ${esc(c.nota.charAt(0).toUpperCase() + c.nota.slice(1))}.</p>
      </div>`).join("")}` : ""}

    <button type="button" class="botao" id="comparar">Comparar rotações <span aria-hidden="true">→</span></button>`;
  $("comparar").addEventListener("click", () => abrirSeccao("rotacoes"));
  $("ver-mais")?.addEventListener("click", (e) => { $("resto").hidden = false; e.target.remove(); });
  $("resultado").hidden = false;

  mostrarHistoria(a);
  const melhor = Math.max(...a.rotacoes.map((r) => r.pontuacao));
  const lista = (titulo, itens) => `<h4>${titulo}</h4><ul>${itens.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
  $("rotacoes-conteudo").innerHTML = `
    <p class="nota" style="margin:0 0 16px"><span data-nt>${esc(a.distrito)}, ${esc(a.provincia)}</span> · sementeira a ${dataLonga(a.data)}</p>
    <div class="rotacoes">${a.rotacoes.map((r) => `
      <article class="rotacao ${r.pontuacao === melhor ? "melhor" : ""}">
        <header>
          <div><h3>${esc(r.nome)}</h3>${r.actual ? '<span class="selo">a sua prática</span>' : r.pontuacao === melhor ? '<span class="selo previsao">melhor pontuação</span>' : ""}</div>
          <span class="pont">${r.pontuacao}<small>/100</small>${info("Média da compatibilidade das culturas da rotação, com um bónus quando há leguminosas e variedade, e uma penalização quando se repete a mesma cultura. Serve para comparar as três opções entre si.")}</span>
        </header>
        <div class="seq">${r.sequencia.map((s, i) => `${i ? "<i>→</i>" : ""}<span>${i + 1}.ª ${esc(s)}</span>`).join("")}</div>
        <p>${esc(r.porque)}</p>
        ${lista("Evidência", r.evidencia)}
        ${lista("Benefícios", r.beneficios)}
        ${lista("Riscos", r.riscos)}
        ${lista("O que falta saber", r.lacunas)}
      </article>`).join("")}
    </div>
    <div class="incerteza"><b>Antes de decidir</b>
      <ul><li>A pontuação compara as opções entre si; não é uma previsão de colheita.</li>
      <li>As três épocas usam o clima médio da mesma data de sementeira.</li>
      <li>Confirme com o técnico de extensão (SDAE) do seu distrito.</li></ul>
    </div>`;
}

// Seccoes escondidas ou recolhidas: abrem quando se vai para la
function abrirSeccao(id) {
  const alvo = $(id);
  alvo.hidden = false;
  const detalhes = alvo.querySelector(":scope > details");
  if (detalhes) detalhes.open = true;
  alvo.scrollIntoView();
}

$("mais-culturas").addEventListener("click", (e) => {
  const recolhida = $("lista-culturas").classList.toggle("recolhida");
  e.target.textContent = recolhida ? "Ver mais culturas" : "Ver menos";
  if (recolhida) $("culturas").scrollIntoView();
});

// ---------- Linguas ----------
// O portugues e o texto de origem. Para as outras linguas, cada pedaco de texto da pagina e
// enviado a /api/traduzir (que guarda as traducoes na base de dados) e trocado no sitio.
const NOMES_LINGUA = { pt: "pt", en: "en", ts: "ts" };
let lingua = "pt";
const originais = new WeakMap();    // no de texto -> { pt: texto original, mostrado: o que la pusemos }
const traduzidas = { en: new Map(), ts: new Map() };
const NUMERO = /\d+(?:[.,]\d+)*/g;

function nosDeTexto() {
  const lista = [];
  const andar = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => {
      const p = n.parentElement;
      if (!p || !n.nodeValue.trim() || !/\p{L}{2}/u.test(n.nodeValue)) return NodeFilter.FILTER_REJECT;
      if (p.closest("script, style, [data-nt], .leaflet-container")) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  while (andar.nextNode()) lista.push(andar.currentNode);
  return lista;
}

async function aplicarLingua() {
  const nos = nosDeTexto();
  for (const n of nos) {
    const g = originais.get(n);
    if (!g || n.nodeValue !== g.mostrado) originais.set(n, { pt: n.nodeValue, mostrado: n.nodeValue });   // texto novo
  }
  const campos = [...document.querySelectorAll("input[placeholder]")];
  campos.forEach((c) => { if (!c.dataset.pt) c.dataset.pt = c.placeholder; });
  document.documentElement.lang = NOMES_LINGUA[lingua];
  if (lingua === "pt") {
    nos.forEach((n) => { const g = originais.get(n); n.nodeValue = g.mostrado = g.pt; });
    campos.forEach((c) => { c.placeholder = c.dataset.pt; });
    return;
  }
  // frase -> modelo sem numeros ("em 20 de 26 anos" -> "em {0} de {1} anos"), para se traduzir uma so vez
  const modelo = (t) => { let i = 0; return t.trim().replace(NUMERO, () => `{${i++}}`); };
  const feitas = traduzidas[lingua], pedida = lingua;
  const todos = [...nos.map((n) => originais.get(n).pt), ...campos.map((c) => c.dataset.pt)];
  const falta = [...new Set(todos.map(modelo))].filter((m) => !feitas.has(m));
  $("linguas").classList.toggle("ocupado", falta.length > 0);
  for (let i = 0; i < falta.length; i += 60) {
    try {
      const r = await fetch("/api/traduzir", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lingua: pedida, textos: falta.slice(i, i + 60) }) });
      if (r.ok) Object.entries((await r.json()).traducoes).forEach(([o, t]) => feitas.set(o, t));
    } catch { /* sem ligacao: fica em portugues */ }
    if (lingua !== pedida) return;            // a pessoa mudou de lingua entretanto
    pintar(nos, campos, feitas, modelo);      // mostra o que ja chegou
  }
  pintar(nos, campos, feitas, modelo);
  $("linguas").classList.remove("ocupado");
}

// a pagina fica escondida ate a primeira traducao chegar, para nao piscar em portugues
function revelar() { document.documentElement.classList.remove("espera-lingua"); }

function pintar(nos, campos, feitas, modelo) {
  const traduz = (pt) => {
    const t = feitas.get(modelo(pt));
    if (t == null) return null;
    const numeros = pt.match(NUMERO) || [];
    const cheio = t.replace(/\{(\d+)\}/g, (m, i) => numeros[i] ?? m);
    return pt.match(/^\s*/)[0] + cheio + pt.match(/\s*$/)[0];   // mantem os espacos das pontas
  };
  observador2.disconnect();
  nos.forEach((n) => { const g = originais.get(n), t = traduz(g.pt); if (t != null && n.isConnected) n.nodeValue = g.mostrado = t; });
  campos.forEach((c) => { const t = traduz(c.dataset.pt); if (t != null) c.placeholder = t.trim(); });
  revelar();
  observador2.observe(document.body, { childList: true, subtree: true, characterData: true });
}

// quando a pagina desenha conteudo novo (resultados, historico), traduz tambem
let espera = null;
const observador2 = new MutationObserver(() => {
  if (lingua === "pt") return;
  clearTimeout(espera);
  espera = setTimeout(aplicarLingua, 200);
});
observador2.observe(document.body, { childList: true, subtree: true, characterData: true });

// "Mudar idioma": abre e fecha a lista
function menuIdioma(abrir) {
  $("linguas").hidden = !abrir;
  $("idioma-botao").setAttribute("aria-expanded", abrir);
}
$("idioma-botao").addEventListener("click", () => menuIdioma($("linguas").hidden));
document.addEventListener("click", (e) => { if (!e.target.closest("#idioma")) menuIdioma(false); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") menuIdioma(false); });

$("linguas").addEventListener("click", (e) => {
  const nova = e.target.dataset.lingua;
  menuIdioma(false);
  if (!nova || nova === lingua) return;
  lingua = nova;
  try { localStorage.setItem("xikomu-lingua-v2", lingua); } catch { /* sem armazenamento: so nao fica lembrado */ }
  $("linguas").querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", b.dataset.lingua === lingua));
  aplicarLingua();
});

iniciar().then(() => {
  let guardada = new URLSearchParams(location.search).get("lingua");
  try { guardada = guardada || localStorage.getItem("xikomu-lingua-v2"); } catch { /* ignora */ }
  guardada = guardada || "en";   // o ingles e a lingua principal
  if (guardada !== "pt" && NOMES_LINGUA[guardada]) {
    $("linguas").querySelector(`[data-lingua="${guardada}"]`).click();
    setTimeout(revelar, 4000);   // se a traducao demorar, mostra-se a pagina na mesma
  } else revelar();
});
