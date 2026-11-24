"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
let mailScelta = null;

// --- Chiamate al server -------------------------------------------------------
async function api(percorso, opzioni = {}) {
  const o = { credentials: "same-origin", ...opzioni };
  o.headers = { "x-braccio": "1", ...(o.headers || {}) };
  if (o.json !== undefined) {
    o.body = JSON.stringify(o.json);
    o.headers["content-type"] = "application/json";
    o.method = o.method || "POST";
  }
  const r = await fetch(percorso, o);
  let dati = null;
  try { dati = await r.json(); } catch (_) {}
  if (r.status === 401) { mostraIngresso(); throw new Error((dati && dati.errore) || "Entra di nuovo."); }
  if (!r.ok) throw new Error((dati && (dati.detail || dati.errore)) || "Errore " + r.status);
  return dati;
}

function el(tag, classe, testo) {
  const e = document.createElement(tag);
  if (classe) e.className = classe;
  if (testo !== undefined) e.textContent = testo;   // mai innerHTML con contenuti delle mail
  return e;
}

// --- Passkey (Face ID / impronta) --------------------------------------------------
const b64uInBuf = s => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(s.length / 4) * 4, "=")), c => c.charCodeAt(0)).buffer;
const bufInB64u = b => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

async function registra() {
  const codice = $("#codice").value.trim();
  const nome = $("#nome-dispositivo").value.trim() || navigator.platform || "Dispositivo";
  errIngresso("");
  try {
    const opz = await api("/api/registra/inizio", { json: { codice } });
    opz.challenge = b64uInBuf(opz.challenge);
    opz.user.id = b64uInBuf(opz.user.id);
    (opz.excludeCredentials || []).forEach(c => c.id = b64uInBuf(c.id));
    const cred = await navigator.credentials.create({ publicKey: opz });
    const credenziale = {
      id: cred.id, rawId: bufInB64u(cred.rawId), type: cred.type,
      response: { clientDataJSON: bufInB64u(cred.response.clientDataJSON),
                  attestationObject: bufInB64u(cred.response.attestationObject),
                  transports: cred.response.getTransports ? cred.response.getTransports() : [] },
    };
    await api("/api/registra/fine", { json: { codice, nome, credenziale } });
    avvia();
  } catch (e) { errIngresso(e.message || "Attivazione non riuscita."); }
}

async function entra() {
  errIngresso("");
  try {
    const opz = await api("/api/accesso/inizio", { json: {} });
    opz.challenge = b64uInBuf(opz.challenge);
    (opz.allowCredentials || []).forEach(c => c.id = b64uInBuf(c.id));
    const cred = await navigator.credentials.get({ publicKey: opz });
    const credenziale = {
      id: cred.id, rawId: bufInB64u(cred.rawId), type: cred.type,
      response: { clientDataJSON: bufInB64u(cred.response.clientDataJSON),
                  authenticatorData: bufInB64u(cred.response.authenticatorData),
                  signature: bufInB64u(cred.response.signature),
                  userHandle: cred.response.userHandle ? bufInB64u(cred.response.userHandle) : null },
    };
    await api("/api/accesso/fine", { json: { credenziale } });
    avvia();
  } catch (e) { errIngresso(e.name === "NotAllowedError" ? "Accesso annullato." : (e.message || "Accesso non riuscito.")); }
}

function errIngresso(t) { $("#errore-ingresso").textContent = t; }

async function mostraIngresso() {
  $("#app").classList.add("nascosto");
  $("#ingresso").classList.remove("nascosto");
  const s = await fetch("/api/stato").then(r => r.json()).catch(() => ({}));
  $("#primo-accesso").classList.toggle("nascosto", !!s.registrato);
  $("#accesso").classList.toggle("nascosto", !s.registrato);
}

// --- Chat -------------------------------------------------------------------------
function disegnaMessaggio(v) {
  const box = $("#messaggi");
  if (v.chi === "roberto") {
    const m = el("div", "msg roberto", v.testo);
    if (v.allegati && v.allegati.length) m.append(el("span", "ora", "📎 " + v.allegati.join(", ")));
    if (v.mail) m.append(el("span", "ora", "su una mail della posta"));
    m.append(el("span", "ora", v.ora));
    box.append(m);
    return;
  }
  if (v.tipo === "bozza") { box.append(schedaBozza(v)); return; }
  if (v.tipo === "parere") { box.append(schedaParere(v)); return; }
  const m = el("div", "msg braccio" + (v.tipo === "errore" ? " errore-msg" : ""));
  if (v.titolo) m.append(el("span", "titolo-r", v.titolo));
  m.append(document.createTextNode(v.testo || ""));
  if (v.file && v.file.length) {
    const f = el("div", "file");
    v.file.forEach(x => {
      if (x.disponibile === false) { f.append(el("span", "piccolo", x.nome + " (scaduto: rifai la ricerca)")); return; }
      const a = el("a", "", "📄 " + x.nome + " · " + x.kb + " KB");
      a.href = "/api/file/" + encodeURIComponent(x.id); a.target = "_blank"; a.rel = "noopener";
      f.append(a);
    });
    m.append(f);
  }
  m.append(el("span", "ora", v.ora));
  box.append(m);
}

function schedaBozza(v) {
  const n = $("#modello-bozza").content.firstElementChild.cloneNode(true);
  $(".b-a", n).value = (v.a || []).join(", ");
  $(".b-cc", n).value = (v.cc || []).join(", ");
  $(".b-oggetto", n).value = v.oggetto || "";
  $(".b-testo", n).value = v.testo || "";
  if (v.allegati && v.allegati.length) $(".b-allegati", n).textContent = "📎 " + v.allegati.join(", ");
  if (v.nota) { const x = el("div", "nota", v.nota); $(".b-allegati", n).after(x); }
  const id = v.id_bozza || (v.mid ? btoa(v.mid).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "") : null);
  const finito = v.stato_bozza && v.stato_bozza !== "da approvare";
  if (finito || !id) {
    $$("input,textarea,button", n).forEach(x => x.disabled = true);
    $(".b-esito", n).textContent = finito ? "Bozza " + v.stato_bozza + "." : "";
    $(".b-esito", n).className = "b-esito stato-ok";
  }
  $(".b-invia", n).onclick = async () => {
    const a = $(".b-a", n).value.split(/[,;]/).map(s => s.trim()).filter(Boolean);
    if (!a.length) { $(".b-esito", n).textContent = "Manca il destinatario."; return; }
    if (!confirm("Inviare la mail a " + a.join(", ") + "?")) return;
    const bottoni = $$("button", n); bottoni.forEach(b => b.disabled = true);
    $(".b-esito", n).textContent = "Invio in corso…";
    try {
      await api("/api/bozze/" + id + "/invia", { json: {
        a, cc: $(".b-cc", n).value.split(/[,;]/).map(s => s.trim()).filter(Boolean),
        oggetto: $(".b-oggetto", n).value, testo: $(".b-testo", n).value } });
      $$("input,textarea", n).forEach(x => x.disabled = true);
      $(".b-esito", n).textContent = "Inviata. La trovi nella posta inviata.";
      $(".b-esito", n).className = "b-esito stato-ok";
      contaBozze();
    } catch (e) { $(".b-esito", n).textContent = e.message; bottoni.forEach(b => b.disabled = false); }
  };
  $(".b-scarta", n).onclick = async () => {
    if (!confirm("Scartare questa bozza?")) return;
    try {
      await api("/api/bozze/" + id + "/scarta", { method: "POST" });
      $$("input,textarea,button", n).forEach(x => x.disabled = true);
      $(".b-esito", n).textContent = "Bozza scartata.";
      contaBozze();
    } catch (e) { $(".b-esito", n).textContent = e.message; }
  };
  return n;
}

function schedaParere(v) {
  const m = el("div", "msg braccio");
  const blocco = (titolo, testo) => { if (!testo) return; m.append(el("span", "titolo-r", titolo)); m.append(el("div", "", testo)); m.append(el("br")); };
  if (v.sintesi) blocco(v.concordano ? "In sintesi (Mistral e Grok concordano)" : "In sintesi (Mistral e Grok NON concordano)", v.sintesi);
  if (v.differenze && v.differenze.length) blocco("Dove non concordano", v.differenze.map(x => "• " + x).join("\n"));
  if (v.da_verificare && v.da_verificare.length) blocco("Da verificare", v.da_verificare.map(x => "• " + x).join("\n"));
  blocco("Parere Mistral", v.mistral);
  blocco("Parere Grok", v.grok);
  if (v.inviato_a_grok) { const d = el("details"); d.append(el("summary", "piccolo", "Cosa è stato inviato a Grok (senza dati)"), el("div", "piccolo", v.inviato_a_grok)); m.append(d); }
  if (v.nota) m.append(el("div", "nota", v.nota));
  m.append(el("span", "ora", v.ora));
  return m;
}

async function caricaChat() {
  const chat = await api("/api/chat");
  $("#messaggi").replaceChildren();
  if (!chat.length) $("#messaggi").append(el("div", "msg braccio",
    "Ciao Roberto. Scrivimi cosa devo fare, ad esempio: «cerca le polizze e i preventivi di E-ALL». " +
    "Per lavorare su una mail, sceglila dalla sezione Posta."));
  chat.forEach(disegnaMessaggio);
  scorriGiu();
}

function scorriGiu() { const m = $("main"); m.scrollTop = m.scrollHeight; }

async function inviaIstruzione(ev) {
  ev.preventDefault();
  const testo = $("#testo").value.trim();
  if (!testo) return;
  const fd = new FormData();
  fd.append("testo", testo);
  if (mailScelta) fd.append("uid_mail", mailScelta.uid);
  [...$("#file").files].forEach(f => fd.append("file", f));
  $("#invia").disabled = true;
  try {
    const { lavoro } = await api("/api/chat", { method: "POST", body: fd });
    $("#testo").value = ""; $("#file").value = ""; $("#file-scelti").textContent = "";
    togliContesto();
    await caricaChat();
    const attesa = el("div", "attesa", "Sto lavorando… (una ricerca può richiedere un minuto)");
    $("#messaggi").append(attesa); scorriGiu();
    while (true) {
      await new Promise(r => setTimeout(r, 3000));
      const s = await api("/api/lavoro/" + lavoro);
      if (s.stato === "fatto") break;
    }
    await caricaChat();
    contaBozze();
  } catch (e) { alert(e.message); }
  finally { $("#invia").disabled = false; }
}

function togliContesto() { mailScelta = null; $("#contesto").classList.add("nascosto"); }

// --- Viste ------------------------------------------------------------------------
async function mostraVista(nome) {
  $$("nav button").forEach(b => b.classList.toggle("attivo", b.dataset.vista === nome));
  $$(".vista").forEach(v => v.classList.add("nascosto"));
  $("#vista-" + nome).classList.remove("nascosto");
  $("#barra").classList.toggle("nascosto", nome !== "chat");
  try {
    if (nome === "bozze") await caricaBozze();
    if (nome === "posta") await caricaPosta();
    if (nome === "collegamenti") { await caricaCollegamenti(); await caricaGrok(); await caricaDispositivi(); }
    if (nome === "chat") scorriGiu();
  } catch (e) { $("#vista-" + nome).append(el("p", "errore", e.message)); }
}

async function caricaBozze() {
  const box = $("#elenco-bozze");
  box.replaceChildren(el("p", "aiuto", "Carico le bozze…"));
  const bozze = await api("/api/bozze");
  box.replaceChildren();
  if (!bozze.length) { box.append(el("p", "aiuto", "Nessuna bozza da approvare.")); return; }
  bozze.forEach(b => {
    const v = el("button", "voce");
    const su = el("span", "su"); su.append(el("span", "", b.a || "(senza destinatario)"), el("span", "", b.data));
    v.append(su, el("span", "og", b.oggetto));
    v.onclick = async () => {
      v.disabled = true;
      try { const d = await api("/api/bozze/" + b.id); v.replaceWith(schedaBozza({ ...d, id_bozza: b.id })); }
      catch (e) { alert(e.message); v.disabled = false; }
    };
    box.append(v);
  });
}

async function contaBozze() {
  try { const b = await api("/api/bozze"); $("#num-bozze").textContent = b.length ? "(" + b.length + ")" : ""; } catch (_) {}
}

async function caricaPosta() {
  const box = $("#elenco-posta");
  box.replaceChildren(el("p", "aiuto", "Carico la posta…"));
  const mail = await api("/api/posta");
  box.replaceChildren();
  mail.forEach(m => {
    const v = el("button", "voce");
    const su = el("span", "su"); su.append(el("span", "", m.da), el("span", "", m.data));
    v.append(su, el("span", "og", m.oggetto));
    v.onclick = () => {
      mailScelta = m;
      $("#contesto-testo").textContent = "Mail: " + m.da + " – " + m.oggetto;
      $("#contesto").classList.remove("nascosto");
      mostraVista("chat");
      $("#testo").focus();
    };
    box.append(v);
  });
}

async function caricaCollegamenti() {
  const box = $("#elenco-collegamenti");
  const lista = await api("/api/collegamenti");
  box.replaceChildren(...lista.map(c => {
    const s = el("div", "scheda");
    s.append(el("h3", "", c.nome));
    s.append(el("p", "piccolo", c.collegato_il ? "Login fatto il " + c.collegato_il : "Non ancora collegato"));
    const riga = el("div", "riga");
    const apri = el("button", "primario", c.collegato_il ? "Rifai il login" : "Collega");
    const fatto = el("button", "", "Ho finito");
    fatto.classList.toggle("nascosto", !c.aperto);
    apri.onclick = async () => {
      apri.disabled = true;
      const finestra = window.open("", "_blank");
      try {
        const r = await api("/api/collegamenti/" + c.id + "/apri", { method: "POST" });
        setTimeout(() => { if (finestra) finestra.location = r.schermo; else location.href = r.schermo; }, 1500);
        fatto.classList.remove("nascosto");
        s.append(el("p", "piccolo", "Si apre lo schermo del server: fai il login come al solito, poi torna qui e tocca \"Ho finito\". Si chiude da solo dopo 20 minuti."));
      } catch (e) { if (finestra) finestra.close(); alert(e.message); }
      finally { apri.disabled = false; }
    };
    fatto.onclick = async () => { await api("/api/collegamenti/" + c.id + "/fatto", { method: "POST" }); caricaCollegamenti(); };
    riga.append(fatto, apri);
    s.append(riga);
    return s;
  }));
}

async function caricaGrok() {
  const k = await api("/api/chiavi");
  $("#stato-grok").textContent = k.grok ? "Collegato." : "Non collegato.";
}

async function caricaDispositivi() {
  const d = await api("/api/dispositivi");
  $("#elenco-dispositivi").replaceChildren(...d.map(x => el("div", "", "• " + x.nome + " (dal " + x.creata + ")")));
}

async function avvia() {
  $("#ingresso").classList.add("nascosto");
  $("#app").classList.remove("nascosto");
  await caricaChat().catch(() => {});
  contaBozze();
}

// --- Eventi -----------------------------------------------------------------------
$("#registra").onclick = registra;
$("#entra").onclick = entra;
$("#mostra-codice").onclick = e => { e.preventDefault(); $("#primo-accesso").classList.remove("nascosto"); $("#accesso").classList.add("nascosto"); };
$("#esci").onclick = async () => { await api("/api/esci", { method: "POST" }).catch(() => {}); mostraIngresso(); };
$$("nav button").forEach(b => b.onclick = () => mostraVista(b.dataset.vista));
$$(".scorciatoie button").forEach(b => b.onclick = () => { $("#testo").value = b.dataset.testo; $("#testo").focus(); });
$("#togli-contesto").onclick = togliContesto;
$("#salva-grok").onclick = async () => {
  const chiave = $("#chiave-grok").value.trim();
  if (!chiave) { alert("Incolla prima la chiave."); return; }
  try { await api("/api/chiavi/grok", { json: { chiave } }); $("#chiave-grok").value = ""; caricaGrok(); } catch (e) { alert(e.message); }
};
$("#togli-grok").onclick = async () => {
  if (!confirm("Scollegare Grok?")) return;
  await api("/api/chiavi/grok", { json: { chiave: "" } }).catch(e => alert(e.message)); caricaGrok();
};
$("#modulo").onsubmit = inviaIstruzione;
$("#file").onchange = () => { $("#file-scelti").textContent = [...$("#file").files].map(f => "📎 " + f.name).join("  "); };
$("#testo").oninput = e => { e.target.style.height = "auto"; e.target.style.height = Math.min(e.target.scrollHeight, 160) + "px"; };

fetch("/api/stato").then(r => r.json()).then(s => s.dentro ? avvia() : mostraIngresso()).catch(mostraIngresso);
