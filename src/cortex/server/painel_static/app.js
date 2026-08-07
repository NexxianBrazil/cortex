"use strict";
// Painel do Cortex (Fase 7d) — vanilla JS, sem build. Consome /painel/api/*.
// Read-only na memória; escrita SÓ via aprovar/rejeitar e upload curado de KB.

const $ = (s) => document.querySelector(s);
const el = (t, props = {}, kids = []) => {
  const n = Object.assign(document.createElement(t), props);
  for (const k of [].concat(kids)) n.append(k);
  return n;
};
const esc = (s) => String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

async function api(rota, opts = {}) {
  const r = await fetch(rota, { headers: { "Content-Type": "application/json" }, ...opts });
  if (r.status === 401) { mostrarLogin(); throw new Error("não autenticado"); }
  const corpo = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(corpo.detail || `erro ${r.status}`);
  return corpo;
}

function toast(msg, erro = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.style.borderLeftColor = erro ? "var(--erro)" : "var(--ok)";
  t.classList.remove("hidden");
  setTimeout(() => t.classList.add("hidden"), 3200);
}

function mostrarLogin() { $("#app").classList.add("hidden"); $("#login").classList.remove("hidden"); }
function mostrarApp() { $("#login").classList.add("hidden"); $("#app").classList.remove("hidden"); }

// ----------------------------- páginas ----------------------------------- //

async function pagResumo() {
  const r = await api("/painel/api/resumo");
  const cartao = (n, t) => el("div", { className: "metric" }, [el("div", { className: "n", textContent: n }), el("div", { className: "muted", textContent: t })]);
  return el("div", {}, [
    el("h2", { textContent: `${r.persona} — ${r.papel}` }),
    el("p", { className: "muted", textContent: `Gestor: ${r.gestor} · Operador: ${r.operador}` }),
    el("div", { className: "cards" }, [
      cartao(r.crencas_ativas, "crenças ativas"),
      cartao(r.pendentes, "na fila"),
      cartao(r.episodios, "episódios"),
      cartao(r.custo_tokens_hoje, "tokens hoje"),
    ]),
  ]);
}

async function pagFila() {
  const { propostas } = await api("/painel/api/fila?status=pendente");
  const wrap = el("div", {}, [el("h2", { textContent: `Fila de aprendizado (${propostas.length})` })]);
  if (!propostas.length) wrap.append(el("p", { className: "muted", textContent: "Nada pendente." }));
  for (const p of propostas) {
    const card = el("div", { className: "proposta" + (p.externa ? " ext" : "") });
    card.innerHTML =
      `<div class="linha"><strong>#${p.id}</strong> · ${esc(p.tipo)} · risco ${esc(p.risco)}` +
      (p.externa ? ` · <span class="warn">⚠ fonte externa não autenticada</span>` : "") + `</div>` +
      `<div class="linha"><code>${esc(p.chave)}</code>: ${esc(p.vigente ?? "(assunto novo)")} → <strong>${esc(p.proposto)}</strong></div>` +
      `<div class="linha muted">fonte: ${esc(p.fonte)} (${esc(p.procedencia)}) · porquê: ${esc(p.porque ?? "—")} · escalou: ${esc(p.razao_escalou ?? "—")}</div>`;
    const razao = el("textarea", { placeholder: "justificativa (obrigatória)" });
    const bAp = el("button", { textContent: "Aprovar", disabled: true });
    const bRe = el("button", { textContent: "Rejeitar", className: "secundario", disabled: true });
    razao.addEventListener("input", () => { const v = !razao.value.trim(); bAp.disabled = v; bRe.disabled = v; });
    const decidir = async (acao) => {
      try {
        const r = await api(`/painel/api/fila/${p.id}/${acao}`, { method: "POST", body: JSON.stringify({ razao: razao.value.trim() }) });
        toast(`#${p.id}: ${r.resultado}`); card.remove();
      } catch (e) { toast(e.message, true); }
    };
    bAp.addEventListener("click", () => decidir("aprovar"));
    bRe.addEventListener("click", () => decidir("rejeitar"));
    card.append(el("div", { className: "acoes" }, [razao, bAp, bRe]));
    wrap.append(card);
  }
  return wrap;
}

async function pagMemoria() {
  const wrap = el("div", {}, [el("h2", { textContent: "Memória (somente leitura)" })]);
  const busca = el("input", { placeholder: "buscar por chave/valor/fonte" });
  const corpo = el("div");
  const render = async () => {
    const { crencas } = await api(`/painel/api/memoria?q=${encodeURIComponent(busca.value)}`);
    const linhas = crencas.map((b) => {
      const tr = el("tr", { className: "clic" });
      tr.innerHTML = `<td><code>${esc(b.key)}</code></td><td>${esc(b.value)}</td><td>${b.confianca}</td><td>${b.saliencia}</td><td>${esc(b.fonte)}</td><td>${esc(b.procedencia)}</td>`;
      tr.addEventListener("click", () => verHistorico(b.key));
      return tr;
    });
    corpo.innerHTML = "";
    const tbl = el("table");
    tbl.innerHTML = "<thead><tr><th>chave</th><th>valor</th><th>confiança</th><th>saliência</th><th>fonte</th><th>procedência</th></tr></thead>";
    const tb = el("tbody"); linhas.forEach((l) => tb.append(l)); tbl.append(tb);
    corpo.append(tbl);
  };
  busca.addEventListener("input", () => render());
  wrap.append(el("div", { className: "toolbar" }, [busca]), corpo);
  await render();
  return wrap;
}

async function verHistorico(key) {
  const { historico } = await api(`/painel/api/memoria/${encodeURIComponent(key)}/historico`);
  const linhas = historico.map((h) =>
    `<tr><td>${esc(h.value)}</td><td>${esc(h.status)}</td><td>${esc(h.fonte)} (${esc(h.procedencia)})</td><td>${esc(h.valido_de)}</td><td>${esc(h.valido_ate ?? "—")}</td><td>${esc(h.razao_mudanca ?? "—")}</td></tr>`
  ).join("");
  const cont = $("#conteudo");
  cont.innerHTML = `<button class="link" id="voltar">&larr; voltar</button><h2>Histórico — <code>${esc(key)}</code></h2>` +
    `<table><thead><tr><th>valor</th><th>status</th><th>fonte</th><th>de</th><th>até</th><th>por quê</th></tr></thead><tbody>${linhas}</tbody></table>`;
  $("#voltar").addEventListener("click", () => irPara("memoria"));
}

async function pagKB() {
  const { documentos } = await api("/painel/api/kb");
  const wrap = el("div", {}, [el("h2", { textContent: "Conhecimento (KB)" })]);
  const arquivo = el("input", { type: "file", accept: ".md" });
  const bUp = el("button", { textContent: "Enviar .md" });
  const bRe = el("button", { textContent: "Reindexar", className: "secundario" });
  bUp.addEventListener("click", async () => {
    const f = arquivo.files[0];
    if (!f) return toast("escolha um arquivo .md", true);
    const conteudo = await f.text();
    try { const r = await api("/painel/api/kb/upload", { method: "POST", body: JSON.stringify({ nome: f.name, conteudo }) });
      toast(`${r.arquivo} indexado (${r.documentos} docs)`); irPara("kb"); }
    catch (e) { toast(e.message, true); }
  });
  bRe.addEventListener("click", async () => { try { const r = await api("/painel/api/kb/reindexar", { method: "POST" }); toast(`reindexado: ${r.documentos} docs`); } catch (e) { toast(e.message, true); } });
  wrap.append(el("div", { className: "toolbar" }, [arquivo, bUp, bRe]));
  const tbl = el("table");
  tbl.innerHTML = "<thead><tr><th>arquivo</th><th>título</th><th>autoridade</th><th>vigência</th><th>estado</th></tr></thead>";
  const tb = el("tbody");
  for (const d of documentos) {
    const estado = d.erro ? `<span class="erro">${esc(d.erro)}</span>` : (d.revogado ? `<span class="warn">⚠ revogado</span>` : `<span class="ok">vigente</span>`);
    const vig = d.erro ? "—" : `${esc(d.vigente_desde)} → ${esc(d.vigente_ate ?? "…")}`;
    tb.innerHTML += `<tr><td><code>${esc(d.arquivo)}</code></td><td>${esc(d.titulo ?? "—")}</td><td>${esc(d.autoridade ?? "—")}</td><td>${vig}</td><td>${estado}</td></tr>`;
  }
  tbl.append(tb); wrap.append(tbl);
  return wrap;
}

async function pagAudit() {
  const { linhas } = await api("/painel/api/audit?n=80");
  const wrap = el("div", {}, [el("h2", { textContent: "Audit (últimas decisões/consultas)" })]);
  const tbl = el("table");
  tbl.innerHTML = "<thead><tr><th>quando</th><th>tipo</th><th>detalhe</th></tr></thead>";
  const tb = el("tbody");
  for (const l of linhas.slice().reverse()) {
    const { ts, tipo, ...resto } = l;
    tb.innerHTML += `<tr><td class="muted">${esc(ts)}</td><td>${esc(tipo)}</td><td><code>${esc(JSON.stringify(resto))}</code></td></tr>`;
  }
  tbl.append(tb); wrap.append(tbl);
  return wrap;
}

async function pagConta() {
  const wrap = el("div", {}, [el("h2", { textContent: "Conta do operador" })]);
  const form = el("form", { className: "card" });
  const atual = el("input", { type: "password", placeholder: "senha atual", autocomplete: "current-password" });
  const nova = el("input", { type: "password", placeholder: "nova senha (mín. 8 caracteres)", autocomplete: "new-password" });
  const conf = el("input", { type: "password", placeholder: "repita a nova senha", autocomplete: "new-password" });
  const bt = el("button", { type: "submit", textContent: "Trocar senha" });
  const erro = el("p", { className: "erro" });
  form.append(
    el("p", { className: "muted", textContent: "A senha vale para este deploy e é gravada no cortex.toml. Ao trocar, as outras sessões abertas caem — esta continua ativa." }),
    atual, nova, conf, bt, erro,
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    erro.textContent = "";
    if (nova.value !== conf.value) { erro.textContent = "a confirmação não confere"; return; }
    bt.disabled = true;
    try {
      await api("/painel/api/senha", { method: "POST", body: JSON.stringify({ senha_atual: atual.value, nova_senha: nova.value }) });
      form.reset();
      toast("senha trocada — já vale para os próximos logins");
    } catch (err) { erro.textContent = err.message; }
    bt.disabled = false;
  });
  wrap.append(form);
  return wrap;
}

// ----------------------------- roteamento -------------------------------- //

const PAGINAS = { resumo: pagResumo, fila: pagFila, memoria: pagMemoria, kb: pagKB, audit: pagAudit, conta: pagConta };

function irPara(nome) { if (location.hash !== "#" + nome) location.hash = nome; else rota(); }

async function rota() {
  const nome = (location.hash.slice(1) || "resumo");
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("ativo", a.hash === "#" + nome));
  const fn = PAGINAS[nome] || pagResumo;
  try { const node = await fn(); $("#conteudo").innerHTML = ""; $("#conteudo").append(node); }
  catch (e) { if (e.message !== "não autenticado") $("#conteudo").innerHTML = `<p class="erro">${esc(e.message)}</p>`; }
}

async function iniciar() {
  try {
    const r = await api("/painel/api/resumo");
    $("#persona").textContent = `· ${r.persona}`;
    $("#operador").textContent = r.operador;
    mostrarApp();
    if (!location.hash) location.hash = "resumo";
    rota();
  } catch { mostrarLogin(); }
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { await api("/painel/login", { method: "POST", body: JSON.stringify({ senha: $("#senha").value }) }); iniciar(); }
  catch (err) { $("#login-erro").textContent = err.message; }
});
$("#sair").addEventListener("click", async () => { await api("/painel/logout", { method: "POST" }).catch(() => {}); mostrarLogin(); });
window.addEventListener("hashchange", rota);
iniciar();
