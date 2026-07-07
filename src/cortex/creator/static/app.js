/* Criador Visual de Cortexes — SPA mínima, vanilla, sem build.
 * Três telas (Início/diagnóstico, Criar, Meus Cortexes) + mini-chat de teste.
 * Tudo fala com as rotas /api/* do próprio app; nenhum estado além da tela. */

const $ = (sel) => document.querySelector(sel);

async function api(caminho, opcoes) {
  const r = await fetch(caminho, opcoes);
  const corpo = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(corpo.detail || `erro ${r.status}`);
  return corpo;
}

/* ------------------------------ navegação ------------------------------ */

function mostrar(tela) {
  for (const s of document.querySelectorAll("main > section")) s.classList.add("hidden");
  $(`#tela-${tela}`).classList.remove("hidden");
  for (const a of document.querySelectorAll("nav a"))
    a.classList.toggle("ativo", a.dataset.tela === tela);
  if (tela === "inicio") carregarDiagnostico();
  if (tela === "meus") carregarDeploys();
}

document.addEventListener("click", (ev) => {
  const alvo = ev.target.closest("[data-tela]");
  if (!alvo) return;
  ev.preventDefault();
  mostrar(alvo.dataset.tela);
});

/* ----------------------------- diagnóstico ----------------------------- */

async function carregarDiagnostico() {
  const caixa = $("#diagnostico");
  try {
    const amb = await api("/api/ambiente");
    $("#base-dir").textContent = `deploys em ${amb.diretorio_base}`;
    caixa.innerHTML = "";
    for (const item of amb.itens) {
      const div = document.createElement("div");
      div.className = `diag ${item.ok ? "ok" : "falha"} ${item.obrigatorio ? "" : "opcional"}`;
      div.innerHTML = `
        <span class="sinal">${item.ok ? "✓" : "✗"}</span>
        <span class="rotulo">${item.rotulo}</span>
        <span class="muted">${item.detalhe}</span>
        ${item.ok ? "" : `<span class="resolver">Como resolver: <code>${item.como_resolver}</code></span>`}`;
      caixa.appendChild(div);
    }
    const resumo = $("#diag-resumo");
    resumo.className = amb.pronto ? "pronto" : "pendente";
    resumo.textContent = amb.pronto
      ? "Ambiente pronto — você já pode criar o seu primeiro Cortex."
      : "Há pendências obrigatórias acima; resolva antes de criar um Cortex.";
  } catch (e) {
    caixa.innerHTML = `<p class="erro">Não consegui checar o ambiente: ${e.message}</p>`;
  }
}

/* -------------------------------- criar -------------------------------- */

function atualizarCamposProvider() {
  const provider = new FormData($("#form-criar")).get("provider");
  $("#campo-chave").classList.toggle("hidden", provider === "stub");
  $("#campo-base-url").classList.toggle("hidden", provider !== "openai");
}
$("#providers").addEventListener("change", atualizarCamposProvider);

$("#form-criar").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const erro = $("#criar-erro");
  erro.classList.add("hidden");
  const dados = Object.fromEntries(new FormData(ev.target));
  const botao = $("#btn-criar");
  botao.disabled = true;
  botao.textContent = "Criando…";
  try {
    const r = await api("/api/criar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(dados),
    });
    $("#sucesso-caminho").textContent = r.deploy;
    $("#sucesso-passos").innerHTML = r.proximos_passos.map((p) => `<li>${p}</li>`).join("");
    ev.target.classList.add("hidden");
    $("#criar-sucesso").classList.remove("hidden");
  } catch (e) {
    erro.textContent = e.message;
    erro.classList.remove("hidden");
  } finally {
    botao.disabled = false;
    botao.textContent = "Criar Cortex";
  }
});

$("#criar-sucesso .ir-meus").addEventListener("click", () => {
  $("#criar-sucesso").classList.add("hidden");
  $("#form-criar").classList.remove("hidden");
  $("#form-criar").reset();
  atualizarCamposProvider();
});

/* ---------------------------- meus cortexes ---------------------------- */

async function carregarDeploys() {
  const caixa = $("#cards");
  try {
    const { deploys, providers } = await api("/api/deploys");
    if (!deploys.length) {
      caixa.innerHTML =
        '<p class="muted">Nenhum Cortex ainda — crie o primeiro na aba "Criar um Cortex".</p>';
      return;
    }
    caixa.innerHTML = "";
    for (const d of deploys) {
      const card = document.createElement("div");
      card.className = "card-deploy";
      if (d.erro) {
        card.innerHTML = `<h3>${d.pasta}</h3><p class="erro">${d.erro}</p>`;
        caixa.appendChild(card);
        continue;
      }
      card.innerHTML = `
        <h3>${d.nome} <span class="muted">· ${d.funcao}</span></h3>
        <span class="estado ${d.no_ar ? "no-ar" : "parado"}">${d.no_ar ? "● no ar" : "○ parado"}</span>
        <span class="meta">gestor: ${d.gestor}</span>
        <span class="meta">cérebro: ${providers[d.provider] || d.provider}</span>
        <span class="meta">KB: ${d.kb_indexada ? "indexada ✓" : "não indexada"}</span>
        <div class="acoes"></div>`;
      const acoes = card.querySelector(".acoes");
      const botao = (texto, fn, secundario = true) => {
        const b = document.createElement("button");
        if (secundario) b.className = "secundario";
        b.textContent = texto;
        b.addEventListener("click", () => fn(b));
        acoes.appendChild(b);
      };
      if (d.no_ar) {
        botao("Parar", (b) => acaoDeploy(b, d.pasta, "parar"));
        const painel = document.createElement("a");
        painel.href = d.painel_url;
        painel.target = "_blank";
        painel.textContent = "Abrir painel de operação";
        acoes.appendChild(painel);
      } else {
        botao("Subir", (b) => acaoDeploy(b, d.pasta, "subir"), false);
      }
      botao("Indexar KB", (b) => acaoDeploy(b, d.pasta, "kb/indexar"));
      botao("Testar", () => abrirChat(d.pasta, d.nome));
      caixa.appendChild(card);
    }
  } catch (e) {
    caixa.innerHTML = `<p class="erro">Não consegui listar os deploys: ${e.message}</p>`;
  }
}

async function acaoDeploy(botao, pasta, acao) {
  botao.disabled = true;
  try {
    const r = await api(`/api/deploys/${encodeURIComponent(pasta)}/${acao}`, { method: "POST" });
    if (acao === "subir" && r.painel_url) window.open(r.painel_url, "_blank");
    if (acao === "kb/indexar")
      alert(`KB indexada: ${r.documentos} documento(s), ${r.chunks} chunk(s).`);
  } catch (e) {
    alert(e.message);
  }
  carregarDeploys();
}

/* ------------------------------ mini-chat ------------------------------ */

let chatPasta = null;

function abrirChat(pasta, nome) {
  chatPasta = pasta;
  $("#chat-titulo").textContent = `Testar ${nome}`;
  $("#chat-msgs").innerHTML = "";
  $("#chat").classList.remove("hidden");
  $("#chat-texto").focus();
}

function msgChat(classe, texto) {
  const div = document.createElement("div");
  div.className = `msg ${classe}`;
  div.textContent = texto;
  $("#chat-msgs").appendChild(div);
  $("#chat-msgs").scrollTop = $("#chat-msgs").scrollHeight;
}

$("#chat-fechar").addEventListener("click", () => $("#chat").classList.add("hidden"));

$("#chat-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const campo = $("#chat-texto");
  const texto = campo.value.trim();
  if (!texto || !chatPasta) return;
  campo.value = "";
  msgChat("eu", texto);
  try {
    const r = await api(
      `/api/deploys/${encodeURIComponent(chatPasta)}/testar?texto=${encodeURIComponent(texto)}`
    );
    msgChat("cortex", r.resposta);
  } catch (e) {
    msgChat("sistema", e.message);
  }
});

/* -------------------------------- start -------------------------------- */

const telaInicial = location.hash.replace("#", "");
mostrar(["inicio", "criar", "meus"].includes(telaInicial) ? telaInicial : "inicio");
