/* Tire Size AI: standalone version (one HTML file, no Python).
   - The document collection is built into this file (PDFs + their page text).
   - Embeddings and answers come from your local Ollama.
   - The search index is kept in this browser (IndexedDB), so it's built only once. */
const BUNDLE = JSON.parse(document.getElementById("bundle").textContent);
const DATA = BUNDLE.data;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const now = () => new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const fmtSize = b => b > 1e6 ? (b / 1e6).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1e3)) + " KB";
const CHUNK = 900, OVERLAP = 150, NUM_CTX = 8192;
const CAT_ORDER = ["Guides", "Car specs", "Standards & research", "Uploaded"];
const STATUS_LABEL = { ready: "ready", indexing: "indexing…", waiting: "not indexed", missing: "not included" };
let store = {};            // name -> { name, title, category, sources, size, pages, version, chunks, blob? }
let indexing = null;       // { file, done, total } while indexing
let indexError = "";
let openFile = null, busy = false, cat = "All", blobUrls = {};

if (window.pdfjsLib) pdfjsLib.GlobalWorkerOptions.workerSrc = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";

const SUGGEST = [
  "What tire size does the Honda City use?",
  "Can I fit 265/65R18 on my Isuzu D-Max?",
  "What does 185/55R16 87H mean?",
  "Which cars can share wheels with a Toyota Fortuner?",
];

// ---------- settings (remembered in this browser) ----------
const CFG_KEYS = ["cfgUrl", "cfgChat", "cfgEmbed", "cfgTimeout", "topk", "multi", "think"];
function loadCfg() { try { const c = JSON.parse(localStorage.getItem("tireai-cfg") || "{}"); CFG_KEYS.forEach(k => { if (c[k] !== undefined) { const el = $(k); el.type === "checkbox" ? el.checked = c[k] : el.value = c[k]; } }); } catch (e) {} }
function saveCfg() { try { const c = {}; CFG_KEYS.forEach(k => { const el = $(k); c[k] = el.type === "checkbox" ? el.checked : el.value; }); localStorage.setItem("tireai-cfg", JSON.stringify(c)); } catch (e) {} }
CFG_KEYS.forEach(k => $(k).addEventListener("change", () => { saveCfg(); if (k.startsWith("cfg")) checkOllama(); }));
const cfg = () => ({ url: $("cfgUrl").value.replace(/\/+$/, ""), chat: $("cfgChat").value.trim(), embed: $("cfgEmbed").value.trim(), timeout: (+$("cfgTimeout").value || 600) * 1000 });

// ---------- IndexedDB ----------
let dbp = null;
function db() {
  if (!dbp) dbp = new Promise((res, rej) => {
    const r = indexedDB.open("tire-size-ai-standalone", 1);
    r.onupgradeneeded = () => r.result.createObjectStore("docs", { keyPath: "name" });
    r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
  });
  return dbp;
}
async function dbAll() { try { const d = await db(); return await new Promise((res, rej) => { const q = d.transaction("docs").objectStore("docs").getAll(); q.onsuccess = () => res(q.result); q.onerror = () => rej(q.error); }); } catch (e) { return []; } }
async function dbPut(rec) { try { const d = await db(); await new Promise((res, rej) => { const t = d.transaction("docs", "readwrite"); t.objectStore("docs").put(rec); t.oncomplete = res; t.onerror = () => rej(t.error); }); } catch (e) {} }
async function dbDel(name) { try { const d = await db(); await new Promise((res, rej) => { const t = d.transaction("docs", "readwrite"); t.objectStore("docs").delete(name); t.oncomplete = res; t.onerror = () => rej(t.error); }); } catch (e) {} }

// ---------- Ollama ----------
async function ollama(path, body) {
  const { url, timeout } = cfg();
  const ctrl = new AbortController(), timer = setTimeout(() => ctrl.abort(), timeout);
  try {
    const r = await fetch(url + path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: ctrl.signal } : { signal: ctrl.signal });
    if (!r.ok) throw new Error(`Ollama said ${r.status}: ${(await r.text()).slice(0, 200)}`);
    return await r.json();
  } catch (e) {
    if (e.name === "AbortError") throw new Error(`Ollama took longer than ${timeout / 1000}s. Raise the timeout in Setup.`);
    if (e instanceof TypeError) throw new Error(`Can't reach Ollama at ${url}. Make sure Ollama is running.`);
    throw e;
  } finally { clearTimeout(timer); }
}
const embed = async texts => (await ollama("/api/embed", { model: cfg().embed, input: texts })).embeddings;
async function chat(messages, think = false) {
  const j = await ollama("/api/chat", { model: cfg().chat, messages, stream: false, think, keep_alive: "30m", options: { num_ctx: NUM_CTX, temperature: 0.2 } });
  return j.message.content.replace(/<think>[\s\S]*?<\/think>/g, "").trim();
}
async function checkOllama() {
  const { url, chat: cm, embed: em } = cfg();
  $("connNote").textContent = "Checking…";
  try {
    const names = ((await ollama("/api/tags")).models || []).map(m => m.name);
    const has = m => names.some(n => n === m || n.startsWith(m + ":"));
    const missing = [cm, em].filter(m => !has(m));
    $("connNote").textContent = missing.length ? `Connected, but missing: ${missing.join(", ")}.\nRun in a terminal: ollama pull ${missing.join(" && ollama pull ")}` : `Connected to Ollama at ${url}.`;
    $("statusDot").classList.remove("off");
    return true;
  } catch (e) {
    $("statusDot").classList.add("off");
    $("connNote").textContent = e.message + "\nIf you opened this page from a website (not as a file), start Ollama with OLLAMA_ORIGINS=* so the page can connect.";
    return false;
  }
}
$("testBtn").onclick = async () => { if (await checkOllama()) indexBundled(); };

// ---------- building the index ----------
function chunkText(t) { t = t.replace(/\s+/g, " ").trim(); const out = []; for (let i = 0; i < t.length; i += CHUNK - OVERLAP) out.push(t.slice(i, i + CHUNK)); return out; }
function chunksFromPages(pages) { const out = []; pages.forEach((txt, i) => chunkText(txt).forEach(text => { if (text.length > 40) out.push({ page: i + 1, text }); })); return out; }
async function embedChunks(chunks, name, base, total) {
  for (let i = 0; i < chunks.length; i += 32) {
    const batch = chunks.slice(i, i + 32);
    const vecs = await embed(batch.map(c => "search_document: " + c.text));
    batch.forEach((c, j) => c.vec = vecs[j]);
  }
}

async function indexBundled() {
  if (indexing) return;
  const todo = BUNDLE.docs.filter(d => !store[d.name] || store[d.name].version !== d.version || !store[d.name].chunks);
  if (!todo.length) { render(); return; }
  indexError = "";
  for (let i = 0; i < todo.length; i++) {
    const d = todo[i];
    indexing = { file: d.title, done: i, total: todo.length };
    render();
    try {
      const chunks = chunksFromPages(d.pages);
      await embedChunks(chunks);
      store[d.name] = { name: d.name, title: d.title, category: d.category, sources: d.sources, size: d.size, pages: d.pages.length, version: d.version, bundled: true, chunks };
      await dbPut(store[d.name]);
    } catch (e) {
      indexError = e.message;
      break;
    }
  }
  indexing = null;
  render();
}

async function addPdf(file) {
  if (!window.pdfjsLib) throw new Error("the PDF reader didn't load (it needs internet the first time)");
  const buf = await file.arrayBuffer();
  const pdf = await pdfjsLib.getDocument({ data: buf.slice(0) }).promise;
  const pages = [];
  for (let p = 1; p <= pdf.numPages; p++) pages.push((await (await pdf.getPage(p)).getTextContent()).items.map(i => i.str).join(" "));
  const chunks = chunksFromPages(pages);
  if (!chunks.length) throw new Error("no text found (is it a scanned PDF?)");
  await embedChunks(chunks);
  const ext = BUNDLE.external.find(e => e.name === file.name);
  const rec = { name: file.name, title: ext ? ext.title : file.name.replace(/\.pdf$/i, ""), category: ext ? ext.category : "Uploaded", sources: ext ? [{ title: ext.title, url: ext.url }] : [],
    size: file.size, pages: pdf.numPages, version: "upload", bundled: false, chunks, blob: new Blob([buf], { type: "application/pdf" }) };
  store[rec.name] = rec;
  if (blobUrls[rec.name]) { URL.revokeObjectURL(blobUrls[rec.name]); delete blobUrls[rec.name]; }
  await dbPut(rec);
  return chunks.length;
}

$("upload").onchange = async e => {
  const list = [...e.target.files]; e.target.value = "";
  if (!list.length) return;
  const lines = [];
  for (const f of list) {
    $("status").textContent = `Reading and indexing ${f.name}…`;
    try { if (!/\.pdf$/i.test(f.name)) throw new Error("not a PDF"); lines.push(`✓ ${f.name}: ${await addPdf(f)} chunks`); }
    catch (err) { lines.push(`✕ ${f.name}: ${err.message}`); }
    render();
  }
  $("status").textContent = lines.join("\n");
};

// ---------- the collection: bundled + external + uploaded ----------
function collection() {
  const out = [];
  for (const d of BUNDLE.docs) {
    const s = store[d.name];
    const status = indexing && indexing.file === d.title ? "indexing" : s && s.version === d.version && s.chunks ? "ready" : "waiting";
    out.push({ name: d.name, title: d.title, category: d.category, size: d.size, pages: d.pages.length, chunks: s && s.chunks ? s.chunks.length : 0, status });
  }
  for (const e of BUNDLE.external) {
    const s = store[e.name];
    out.push(s ? { name: s.name, title: s.title, category: s.category, size: s.size, pages: s.pages, chunks: s.chunks.length, status: "ready" }
               : { name: e.name, title: e.title, category: e.category, url: e.url, status: "missing" });
  }
  for (const s of Object.values(store)) if (!s.bundled && !BUNDLE.external.some(e => e.name === s.name))
    out.push({ name: s.name, title: s.title, category: s.category, size: s.size, pages: s.pages, chunks: s.chunks.length, status: "ready" });
  return out.sort((a, b) => CAT_ORDER.indexOf(a.category) - CAT_ORDER.indexOf(b.category) || a.title.localeCompare(b.title));
}
const titleOf = name => (store[name] || BUNDLE.docs.find(d => d.name === name) || { title: name }).title;

function render() {
  const files = collection();
  const ready = files.filter(f => f.status === "ready");
  const chunks = Object.values(store).reduce((n, s) => n + (s.chunks ? s.chunks.length : 0), 0);
  $("stDocs").textContent = ready.length;
  $("stChunks").textContent = chunks;
  $("modelLine").textContent = `${cfg().chat} · ${ready.length} of ${files.length} documents indexed`;

  const box = $("indexBox"), waiting = files.filter(f => f.status === "waiting").length, missing = files.filter(f => f.status === "missing").length;
  if (indexing) {
    box.hidden = false; $("indexBar").parentElement.hidden = false;
    $("indexText").textContent = `Indexing ${indexing.file} (${indexing.done + 1} of ${indexing.total})…`;
    $("indexBar").style.width = Math.round(indexing.done / indexing.total * 100) + "%";
    $("reindexBtn").hidden = true;
  } else if (indexError || waiting || missing) {
    box.hidden = false; $("indexBar").parentElement.hidden = true;
    $("indexText").textContent = indexError ? indexError : [waiting ? `${waiting} document${waiting > 1 ? "s" : ""} not indexed yet.` : "", missing ? `${missing} research PDF${missing > 1 ? "s are" : " is"} not included in this file. Click one to get it, then upload it with +.` : ""].join(" ");
    $("reindexBtn").hidden = !waiting && !indexError;
    $("reindexBtn").textContent = indexError ? "Try again" : "Index now";
  } else box.hidden = true;

  // categories
  const cats = ["All", ...CAT_ORDER.filter(c => files.some(f => f.category === c))];
  $("cats").innerHTML = cats.map(c => `<button type="button" data-cat="${esc(c)}" aria-pressed="${c === cat}">${esc(c)}<span>${c === "All" ? files.length : files.filter(f => f.category === c).length}</span></button>`).join("");
  $("cats").querySelectorAll("button").forEach(b => b.onclick = () => { cat = b.dataset.cat; render(); });

  // documents
  const term = $("docSearch").value.trim().toLowerCase();
  const list = files.filter(f => (cat === "All" || f.category === cat) && (f.title + " " + f.name).toLowerCase().includes(term));
  $("docCount").textContent = list.length;
  $("listLabel").textContent = cat === "All" ? "All documents" : cat;
  $("docs").innerHTML = list.length ? list.map(f => `
    <div class="doc ${f.status === "missing" ? "missing" : ""}" role="button" tabindex="0" data-name="${esc(f.name)}" aria-pressed="${f.name === openFile}">
      <span class="ficon" aria-hidden="true"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg></span>
      <span style="min-width:0"><b title="${esc(f.title)}">${esc(f.title)}</b>
        <small><span>${f.status === "missing" ? "click to get the PDF" : `${f.pages} pages · ${f.chunks} chunks · ${fmtSize(f.size)}`}</span><span class="st ${f.status}">${STATUS_LABEL[f.status]}</span></small>
        <span class="sub">${esc(f.name)}</span></span>
      ${store[f.name] && !store[f.name].bundled ? `<button class="del" aria-label="Remove ${esc(f.title)}" title="Remove"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>` : ""}
    </div>`).join("") : `<p class="empty">No match.</p>`;
  $("docs").querySelectorAll(".doc").forEach(d => {
    const f = files.find(x => x.name === d.dataset.name);
    const go = () => f.status === "missing" ? window.open(f.url, "_blank", "noopener") : openViewer(f.name, 1);
    d.onclick = e => { if (!e.target.closest(".del")) go(); };
    d.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } };
    const del = d.querySelector(".del");
    if (del) del.onclick = async () => {
      if (!d.dataset.confirm) { d.dataset.confirm = "1"; d.querySelector("small").textContent = "Click × again to remove"; return; }
      delete store[f.name]; await dbDel(f.name);
      if (openFile === f.name) closeViewer();
      render();
    };
  });
}
$("docSearch").addEventListener("input", render);
$("reindexBtn").onclick = async () => { if (await checkOllama()) indexBundled(); };

// ---------- PDF viewer ----------
function pdfUrl(name) {
  if (blobUrls[name]) return blobUrls[name];
  const b = BUNDLE.docs.find(d => d.name === name);
  let blob = store[name] && store[name].blob;
  if (!blob && b) { const bin = atob(b.b64), arr = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i); blob = new Blob([arr], { type: "application/pdf" }); }
  return blob ? (blobUrls[name] = URL.createObjectURL(blob)) : null;
}
function openViewer_(name, page) {
  const base = pdfUrl(name);
  if (!base) return;
  openFile = name;
  const url = base + "#page=" + (page || 1);
  $("viewer").innerHTML = `<div class="vbar"><span>${esc(titleOf(name))} · page ${page || 1}</span><a href="${url}" target="_blank" rel="noopener">Open ↗</a></div><iframe title="PDF preview" src="${url}"></iframe>`;
  document.querySelectorAll(".acc")[1].dataset.open = "true";
  render();
}
function openViewer(name, page) { if (window.setPanel) setPanel("right", true); return openViewer_(name, page); }
function closeViewer() { openFile = null; $("viewer").innerHTML = `<div class="placeholder">Click a document or a reference to open it here.</div>`; }

// ---------- tire calculator (exact math the small model can rely on) ----------
const SIZE_RE = /(\d{3})\s*\/\s*(\d{2})\s*Z?R?\s*-?\s*(\d{2})(?:\s*(\d{2,3})\s*([A-Z])\b)?/gi;
function parseSizes(text) { return [...text.matchAll(SIZE_RE)].map(m => ({ w: +m[1], ar: +m[2], rim: +m[3], li: m[4] ? +m[4] : null, sr: m[5] ? m[5].toUpperCase() : null, raw: `${m[1]}/${m[2]}R${m[3]}` })); }
const diameter = t => t.rim * 25.4 + 2 * t.w * t.ar / 100;
function findCar(text) { const t = text.toLowerCase(); let best = null, len = 0; for (const c of DATA.CARS) for (const k of c.keys) if (t.includes(k) && k.length > len) { best = c; len = k.length; } return best; }
const verdict = d => Math.abs(d) <= 3 ? "good fit (within ±3%)" : Math.abs(d) <= 5 ? "risky (3–5% off)" : "not recommended (more than 5% off)";
const sgn = (n, d = 2) => (n > 0 ? "+" : "") + n.toFixed(d);
function calculator(q) {
  const sizes = parseSizes(q), car = findCar(q), L = DATA.LOAD, S = DATA.SPEED;
  if (!sizes.length) return null;
  const lines = [];
  if (car) {
    const nw = sizes[0], oems = car.sizes.map(s => ({ ...s, t: parseSizes(s.tire)[0] }));
    const oem = oems.find(s => s.t.rim === nw.rim) || oems.sort((a, b) => Math.abs(diameter(a.t) - diameter(nw)) - Math.abs(diameter(b.t) - diameter(nw)))[0];
    const df = diameter(oem.t), dn = diameter(nw), d = (dn - df) / df * 100;
    lines.push(`${car.name} factory tire on the ${oem.label} version: ${oem.tire}${oem.li ? " " + oem.li + oem.sr : ""} on ${oem.rim}, diameter ${df.toFixed(1)} mm.`);
    lines.push(`New tire ${nw.raw}: diameter ${dn.toFixed(1)} mm. Change: ${sgn(d)}%, which is ${verdict(d)}.`);
    lines.push(`When the speedometer shows 100 km/h, the real speed is ${(100 * dn / df).toFixed(1)} km/h. Ride height changes by ${sgn((dn - df) / 2, 1)} mm.`);
    if (nw.rim !== oem.t.rim) lines.push(`It needs a ${nw.rim}-inch wheel with bolt pattern ${car.pcd} and center bore ${car.cb}.`);
    if (oem.li) lines.push(`The new tire's load index must be at least ${oem.li} (${L[oem.li]} kg) and its speed rating at least ${oem.sr} (${S[oem.sr]} km/h).`);
    if (nw.li && oem.li && nw.li < oem.li) lines.push(`Warning: load index ${nw.li} is LOWER than the factory ${oem.li}. Do not use it.`);
  } else if (sizes.length >= 2) {
    const [a, b] = sizes, da = diameter(a), dbb = diameter(b), d = (dbb - da) / da * 100;
    lines.push(`${a.raw} diameter ${da.toFixed(1)} mm; ${b.raw} diameter ${dbb.toFixed(1)} mm. Change: ${sgn(d)}%, which is ${verdict(d)}.`);
    lines.push(`With ${b.raw} instead of ${a.raw}, the real speed at 100 km/h on the speedometer is ${(100 * dbb / da).toFixed(1)} km/h.`);
  } else {
    const t = sizes[0];
    lines.push(`${t.raw}: width ${t.w} mm, sidewall ${(t.w * t.ar / 100).toFixed(1)} mm (${t.ar}% of the width), radial, fits a ${t.rim}-inch wheel, overall diameter ${diameter(t).toFixed(1)} mm.`);
    if (t.li && L[t.li]) lines.push(`Load index ${t.li} = up to ${L[t.li]} kg per tire.`);
    if (t.sr && S[t.sr]) lines.push(`Speed rating ${t.sr} = up to ${S[t.sr]} km/h.`);
  }
  return "Calculated by the app: " + lines.join(" ");
}

// ---------- search + answer ----------
const slug = s => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
async function search(q, k, boostFile) {
  const qv = (await embed(["search_query: " + q]))[0], qn = Math.hypot(...qv);
  const all = Object.values(store).flatMap(s => (s.chunks || []).map(c => ({ ...c, file: s.name })));
  return all.map(c => {
    let dot = 0, n = 0;
    for (let i = 0; i < qv.length; i++) { dot += c.vec[i] * qv[i]; n += c.vec[i] * c.vec[i]; }
    return { c, score: dot / (Math.sqrt(n) * qn + 1e-9) + (c.file === boostFile ? 0.15 : 0) };
  }).sort((a, b) => b.score - a.score).slice(0, k);
}
async function extraQueries(q) {
  try { return (await chat([{ role: "user", content: "Write 2 short search queries (one per line, no numbering) that would help find information to answer this question:\n" + q }])).split("\n").map(l => l.replace(/^[-•\d.\s]+/, "").trim()).filter(Boolean).slice(0, 2); }
  catch (e) { return []; }
}
const SYSTEM = `You are Tire Size AI, a helpful assistant for wheel and tire sizes of cars in Thailand.
Answer in simple English using ONLY the numbered sources below.
After every fact, cite the source number in square brackets, like [1] or [2][3].
If a source says "Calculated by the app", trust its numbers exactly.
If the sources do not contain the answer, say you could not find it in the documents.
Do not invent sources or numbers.`;

async function runAsk(question) {
  const k = +$("topk").value || 6, car = findCar(question);
  const boost = car ? slug(car.name) + "-specs.pdf" : null;
  const queries = [question].concat($("multi").checked ? await extraQueries(question) : []);
  const best = new Map();
  for (const q of queries) for (const h of await search(q, k, boost)) {
    const key = h.c.file + "|" + h.c.page + "|" + h.c.text.slice(0, 40);
    if (!best.has(key) || best.get(key).score < h.score) best.set(key, h);
  }
  const hits = [...best.values()].sort((a, b) => b.score - a.score).slice(0, k);
  const refs = [], context = [];
  const calc = calculator(question);
  if (calc) { refs.push({ n: 1, kind: "calculator", title: "Tire calculator (exact math by the app)", snippet: calc, score: 1 }); context.push(`[1] (Tire calculator)\n${calc}`); }
  for (const h of hits) {
    const n = refs.length + 1;
    refs.push({ n, kind: "document", file: h.c.file, page: h.c.page, title: titleOf(h.c.file), score: h.score, snippet: h.c.text.slice(0, 320) });
    context.push(`[${n}] (from ${h.c.file}, page ${h.c.page})\n${h.c.text}`);
  }
  const answer = await chat([{ role: "system", content: SYSTEM + "\n\nSOURCES:\n\n" + context.join("\n\n") }, { role: "user", content: question }], $("think").checked);
  const cited = new Set([...answer.matchAll(/\[(\d+)\]/g)].map(m => +m[1]));
  refs.forEach(r => r.cited = cited.has(r.n));
  return { answer, sources: refs, queries };
}

// ---------- chat UI ----------
function welcome() {
  $("chat").innerHTML = `<div class="welcome">
    <div class="avatar" aria-hidden="true" style="width:72px;height:72px">${document.querySelector(".chat-head .avatar").innerHTML}</div>
    <h3>Ask me about tire sizes</h3>
    <p>I answer from the document collection and always show the references I used.</p>
    <div class="steps">
      <div><b><i>01</i>Ask</b><span>Type a question, like a tire size for your car.</span></div>
      <div><b><i>02</i>Search</b><span>It finds the closest passages in the documents.</span></div>
      <div><b><i>03</i>Calculate</b><span>Tire sizes get exact math: diameter, % change, real speed.</span></div>
      <div><b><i>04</i>Answer</b><span>qwen3 answers from those sources and lists the references.</span></div>
    </div>
    <div class="suggest">${SUGGEST.map(s => `<button type="button">${esc(s)}</button>`).join("")}</div>
  </div>`;
  $("chat").querySelectorAll(".suggest button").forEach(b => b.onclick = () => ask(b.textContent));
}
function clearChat() { welcome(); renderSources([]); }
function addMsg(role, html) {
  const w = $("chat").querySelector(".welcome");
  if (w) { w.remove(); $("chat").insertAdjacentHTML("beforeend", `<div class="divider">Today</div>`); }
  const d = document.createElement("div"), isUser = role === "user";
  d.className = "msg " + role;
  d.innerHTML = `<div class="who" aria-hidden="true">${isUser ? "Y" : `<svg width="22" height="22" viewBox="0 0 34 34"><circle cx="17" cy="17" r="15" fill="#1f2328"/><circle cx="17" cy="17" r="8.5" fill="#f6efe6"/><circle cx="17" cy="17" r="2.5" fill="#1f2328"/></svg>`}</div>
    <div><div class="name">${isUser ? "You" : "Tire Size AI"}<span class="time">${now()}</span></div><div class="text">${html}</div></div>`;
  $("chat").append(d); $("chat").scrollTop = $("chat").scrollHeight;
  return d;
}
const refLabel = s => s.kind === "calculator" ? s.title : `${s.title} · page ${s.page}`;
const openRef = s => { if (s.kind === "document") openViewer(s.file, s.page); };
function renderSources(sources) {
  $("srcCount").textContent = sources.length;
  $("srcCards").innerHTML = sources.length ? sources.map(s => `
    <button class="src-card" data-n="${s.n}">
      <span class="top"><b>[${s.n}] ${esc(s.kind === "calculator" ? "Tire calculator" : s.title)}</b><span class="tag ${s.cited ? "" : "no"}">${s.cited ? "cited" : "not cited"}</span></span>
      <span style="font-size:12px;color:var(--muted)">${s.kind === "calculator" ? "Exact math done by the app" : `${esc(s.file)} · page ${s.page} · match ${Math.round(Math.min(1, s.score) * 100)}%`}</span>
      <span class="snip">${esc(s.snippet)}</span>
    </button>`).join("") : `<p class="empty">Ask a question and the references the AI used will show here.</p>`;
  $("srcCards").querySelectorAll(".src-card").forEach(c => { const s = sources.find(x => x.n == c.dataset.n); c.onclick = () => openRef(s); });
}
async function ask(question) {
  question = question.trim();
  if (!question || busy) return;
  busy = true; $("sendBtn").disabled = true;
  addMsg("user", esc(question));
  const box = addMsg("bot", `<span class="typing" aria-label="Thinking"><span></span><span></span><span></span></span>`), text = box.querySelector(".text");
  const t0 = Date.now();
  try {
    if (!Object.values(store).some(s => s.chunks && s.chunks.length)) throw new Error(indexing ? "The documents are still being indexed. Try again in a moment." : "No documents are indexed yet. Make sure Ollama is running, then click 'Index now' on the left.");
    const data = await runAsk(question);
    const byN = Object.fromEntries(data.sources.map(s => [s.n, s]));
    text.innerHTML = esc(data.answer).replace(/\[(\d+)\]/g, (m, n) => byN[n] ? `<a class="cite" data-n="${n}" title="${esc(refLabel(byN[n]))}" tabindex="0">${n}</a>` : m);
    text.querySelectorAll(".cite").forEach(a => { const s = byN[a.dataset.n]; a.onclick = () => openRef(s); a.onkeydown = e => { if (e.key === "Enter") openRef(s); }; });
    const refs = document.createElement("div");
    refs.className = "refs";
    refs.innerHTML = `<strong>References</strong>` + data.sources.map(s => { const tag = s.kind === "document" ? "button" : "div"; return `<${tag} class="ref ${s.cited ? "" : "dim"}" data-n="${s.n}"><b>[${s.n}]</b><span>${esc(refLabel(s))}</span><em>${s.cited ? "cited" : "also used"}</em></${tag}>`; }).join("");
    refs.querySelectorAll("button.ref").forEach(b => b.onclick = () => openRef(byN[b.dataset.n]));
    text.after(refs);
    const meta = document.createElement("div");
    meta.className = "meta-line";
    meta.textContent = `${((Date.now() - t0) / 1000).toFixed(1)}s` + (data.queries.length > 1 ? " · searched: " + data.queries.join(" | ") : "");
    refs.after(meta);
    renderSources(data.sources);
  } catch (err) { box.classList.add("err"); text.textContent = err.message; }
  finally { busy = false; $("sendBtn").disabled = false; $("chat").scrollTop = $("chat").scrollHeight; }
}
$("ask").addEventListener("submit", e => { e.preventDefault(); const q = $("q").value; $("q").value = ""; ask(q); });
document.querySelectorAll(".acc-head").forEach(h => h.onclick = () => { const acc = h.parentElement, open = acc.dataset.open !== "true"; acc.dataset.open = open; h.setAttribute("aria-expanded", open); });

// ---------- start ----------
loadCfg();
welcome();
const ready = dbAll().then(async all => {
  all.forEach(r => store[r.name] = r);
  render();
  if (await checkOllama()) await indexBundled(); else render();
});
