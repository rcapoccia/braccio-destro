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
  if (v.tipo === "ricerca" && v.documenti) { box.append(schedaRicerca(v)); return; }
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

function schedaRicerca(v) {
  const s = el("div", "scheda ricerca");
  const testa = el("div", "r-testa");
  testa.append(el("span", "r-titolo", "Documenti di " + v.cliente), el("span", "r-num", String(v.documenti.length)));
  s.append(testa);
  const info = "Cercato come " + v.nomi.join(", ") + " · posta arrivata e inviata, ultimi 3 anni" +
    (v.scartati ? " · " + v.scartati + " allegati di altri clienti o non utili esclusi" : "");
  s.append(el("p", "piccolo", info));
  if (!v.documenti.length) { s.append(el("p", "", "Nessun documento trovato. Prova con la ragione sociale completa o la partita IVA.")); s.append(el("span", "ora", v.ora)); return s; }
  const tipi = [...new Set(v.documenti.map(d => d.tipo))];
  const filtri = el("div", "filtri");
  const corpo = el("div");
  const disegna = (filtro) => {
    corpo.replaceChildren();
    $$("button", filtri).forEach(b => b.classList.toggle("attivo", b.dataset.t === filtro));
    tipi.filter(t => filtro === "tutti" || t === filtro).forEach(t => {
      const docs = v.documenti.filter(d => d.tipo === t);
      corpo.append(el("div", "r-gruppo", (v.titoli[t] || t) + " · " + docs.length));
      docs.forEach(d => {
        const riga = el(d.disponibile === false ? "div" : "a", "r-doc");
        if (d.disponibile !== false) { riga.href = "/api/file/" + encodeURIComponent(d.id); riga.target = "_blank"; riga.rel = "noopener"; }
        const testo = el("div", "r-testo");
        testo.append(el("span", "r-desc", d.descrizione || d.nome));
        const dettagli = [d.compagnia, d.numero ? "n. " + d.numero : "", d.scadenza ? "scad. " + d.scadenza : ""].filter(Boolean).join(" · ");
        if (dettagli) testo.append(el("span", "r-det", dettagli));
        testo.append(el("span", "r-meta", [d.data, (d.verso === "inviata" ? "a " : "da ") + d.contatto].filter(Boolean).join(" · ") +
          (d.disponibile === false ? " · scaduto: rifai la ricerca" : "")));
        riga.append(el("span", "r-icona", "📄"), testo, el("span", "r-freccia", "›"));
        corpo.append(riga);
      });
    });
  };
  if (tipi.length > 1) {
    [["tutti", "Tutti"], ...tipi.map(t => [t, v.titoli[t] || t])].forEach(([t, nome]) => {
      const b = el("button", "", nome); b.dataset.t = t; b.onclick = () => disegna(t); filtri.append(b);
    });
    s.append(filtri);
  }
  s.append(corpo);
  disegna("tutti");
  s.append(el("span", "ora", v.ora));
  return s;
}

function schedaParere(v) {
  const m = el("div", "msg braccio");
  if (v.mistral && !v.risposta) { m.append(document.createTextNode(v.mistral)); m.append(el("span", "ora", v.ora)); return m; }
  m.append(document.createTextNode(v.risposta || ""));
  if (v.fonti && v.fonti.length) {
    const d = el("details");
    d.append(el("summary", "piccolo", "Fonti (" + v.fonti.length + ")"));
    v.fonti.forEach(f => {
      const r = el("div", "piccolo");
      r.append(el("b", "", "[" + f.n + "] " + f.fonte + ", pag. " + f.pagina + (f.articolo ? ", " + f.articolo : "")));
      r.append(el("div", "", f.estratto + "…"));
      d.append(r);
    });
    m.append(d);
  }
  if (v.giri && v.giri.length) {
    const corretti = v.giri.filter(g => !g.ok).length;
    const t = "Ricontrollato da " + v.revisore + ": " + (corretti ? corretti + " giro/i di correzione" : "nessuna correzione necessaria");
    const d = el("details");
    d.append(el("summary", "piccolo", t));
    v.giri.forEach(g => { if (g.problemi.length) d.append(el("div", "piccolo", "Giro " + g.giro + ":\n" + g.problemi.map(x => "• " + x).join("\n"))); });
    if (v.inviato_al_revisore) d.append(el("div", "piccolo", "Testo inviato al revisore (senza dati):\n" + v.inviato_al_revisore));
    m.append(d);
  }
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
    if (nome === "collegamenti") { await caricaCollegamenti(); await caricaScaleway(); await caricaDispositivi(); }
    if (nome === "biblioteca") await caricaBiblioteca();
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

async function caricaScaleway() {
  const k = await api("/api/chiavi");
  $("#stato-scaleway").textContent = k.scaleway ? "Collegato." : "Non collegato: il controllo lo fa Mistral.";
}

async function caricaBiblioteca() {
  const b = await api("/api/biblioteca");
  const box = $("#elenco-biblioteca");
  const gruppi = [["ufficiale", "Testi ufficiali"], ["tuo", "I tuoi materiali"]];
  box.replaceChildren();
  gruppi.forEach(([tipo, titolo]) => {
    const lista = b.fonti.filter(f => f.tipo === tipo);
    const s = el("div", "scheda");
    s.append(el("h3", "", titolo + " (" + lista.length + ")"));
    if (!lista.length) s.append(el("p", "piccolo", tipo === "tuo" ? "Nessun materiale caricato." : "In caricamento…"));
    lista.forEach(f => {
      const r = el("div", "riga"); r.style.justifyContent = "space-between";
      r.append(el("span", "piccolo", f.titolo + " · " + f.edizione + " · " + f.brani + " brani"));
      if (tipo === "tuo") { const x = el("button", "leggero", "Togli"); x.onclick = async () => { if (confirm("Togliere " + f.titolo + "?")) { await api("/api/biblioteca/" + encodeURIComponent(f.id) + "/rimuovi", { method: "POST" }); caricaBiblioteca(); } }; r.append(x); }
      s.append(r);
    });
    box.append(s);
  });
  $("#esito-biblioteca").textContent = b.in_corso ? "Sto caricando e indicizzando… (può richiedere qualche minuto)" : (b.esito ? b.esito.join("\n") : "");
  if (b.in_corso) setTimeout(() => { if (!$("#vista-biblioteca").classList.contains("nascosto")) caricaBiblioteca(); }, 5000);
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
$("#salva-scaleway").onclick = async () => {
  const chiave = $("#chiave-scaleway").value.trim();
  if (!chiave) { alert("Incolla prima la chiave."); return; }
  try { await api("/api/chiavi/scaleway", { json: { chiave } }); $("#chiave-scaleway").value = ""; caricaScaleway(); } catch (e) { alert(e.message); }
};
$("#togli-scaleway").onclick = async () => {
  if (!confirm("Scollegare il revisore europeo?")) return;
  await api("/api/chiavi/scaleway", { json: { chiave: "" } }).catch(e => alert(e.message)); caricaScaleway();
};
$("#carica-biblioteca").onclick = async () => {
  const fd = new FormData(); [...$("#file-biblioteca").files].forEach(f => fd.append("file", f));
  try { await api("/api/biblioteca", { method: "POST", body: fd }); $("#file-biblioteca").value = ""; caricaBiblioteca(); } catch (e) { alert(e.message); }
};
$("#aggiorna-ufficiali").onclick = async () => {
  if (!confirm("Riscaricare tutti i testi ufficiali? Ci vogliono alcuni minuti.")) return;
  try { await api("/api/biblioteca/ufficiali", { method: "POST" }); caricaBiblioteca(); } catch (e) { alert(e.message); }
};
$("#modulo").onsubmit = inviaIstruzione;
$("#file").onchange = () => { $("#file-scelti").textContent = [...$("#file").files].map(f => "📎 " + f.name).join("  "); };
$("#testo").oninput = e => { e.target.style.height = "auto"; e.target.style.height = Math.min(e.target.scrollHeight, 160) + "px"; };

fetch("/api/stato").then(r => r.json()).then(s => s.dentro ? avvia() : mostraIngresso()).catch(mostraIngresso);
