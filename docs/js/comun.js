/* Estado compartido entre pestañas (panel, pantalla del aula, check-in, portal).
   Versión estática para GitHub Pages: todo corre en el navegador con localStorage. */
const CONFIG = {
  profesor: { nombre: "Prof. Demo", email: "profesor@universidad.edu.ar" },
  aulas: { "A-201": "Aula 201 · Edificio Central", "LAB-3": "Laboratorio 3" },
  cursos: {
    "PROG2-A": { id: "PROG2-A", materia: "Programación II", division: "A", aula: "A-201" },
    "BD-B": { id: "BD-B", materia: "Bases de Datos", division: "B", aula: "LAB-3" },
  },
  reglas: { minimoVirtual: 60, alertaCamara: 30, rotacionQR: 20 },
};

const NOMBRES = ["Ana","Carlos","Lucía","Mateo","Valentina","Santiago","Camila","Benjamín","Martina","Thiago","Sofía",
  "Joaquín","Julieta","Tomás","Florencia","Agustín","Milagros","Facundo","Rocío","Nicolás","Abril","Lautaro","Micaela",
  "Franco","Delfina","Ignacio","Paula","Bruno"];
const APELLIDOS = ["Gómez","López","Fernández","Rodríguez","Pérez","Martínez","García","Sosa","Romero","Díaz","Álvarez",
  "Torres","Ruiz","Ramírez","Benítez","Acosta","Medina","Herrera","Suárez","Aguirre","Giménez","Molina","Castro","Ortiz",
  "Silva","Luna","Rojas","Vera"];

function rng(semilla) {           // mulberry32: aleatorio reproducible
  let a = 0; for (const ch of String(semilla)) a = (a * 31 + ch.charCodeAt(0)) | 0;
  return () => { a |= 0; a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
const elegir = (r, arr) => arr[Math.floor(r() * arr.length)];
const sinTildes = s => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

function padron(cursoId) {
  const r = rng(cursoId), usados = new Set(), lista = [];
  while (lista.length < 30) {
    const n = elegir(r, NOMBRES), a = elegir(r, APELLIDOS);
    if (usados.has(n + a)) continue; usados.add(n + a);
    lista.push({ matricula: String(40000 + Math.floor(r() * 10000)), nombre: `${a}, ${n}`,
      email: `${sinTildes(n)}.${sinTildes(a)}@universidad.edu.ar` });
  }
  return lista.sort((x, y) => x.nombre.localeCompare(y.nombre, "es"));
}

/* ── estado ─────────────────────────────────────────────── */
const CLAVE = "autopresente_v1";
function leer() {
  try { return JSON.parse(localStorage.getItem(CLAVE)) || nuevo(); } catch { return nuevo(); }
}
function nuevo() { return { sig: 1, clases: [], eventos: [], portal: {} }; }
function guardar(e) { try { localStorage.setItem(CLAVE, JSON.stringify(e)); } catch {} }
function mutar(fn) { const e = leer(); const r = fn(e); guardar(e); return r; }
function log(e, mensaje, nivel = "info", claseId = null) {
  e.eventos.unshift({ ts: horaTxt(new Date(), true), nivel, claseId, mensaje });
  e.eventos = e.eventos.slice(0, 60);
}

/* ── utilidades ─────────────────────────────────────────── */
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const dos = n => String(n).padStart(2, "0");
const horaTxt = (d, seg) => `${dos(d.getHours())}:${dos(d.getMinutes())}${seg ? ":" + dos(d.getSeconds()) : ""}`;
const fechaTxt = d => `${dos(d.getDate())}/${dos(d.getMonth() + 1)}/${d.getFullYear()}`;
const nombrePila = nom => { const [ape, n] = nom.split(", "); return `${n} ${ape}`; };

/* Token del QR: cambia cada N segundos; se acepta la ventana actual y la anterior. */
function tokenQR(claseId, desfase = 0) {
  const v = Math.floor(Date.now() / 1000 / CONFIG.reglas.rotacionQR) - desfase;
  let h = 2166136261; for (const ch of `${claseId}:${v}:cervantes`) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  return (h >>> 0).toString(36);
}
// Acepta la ventana anterior, la actual y la siguiente (por si el reloj del celular difiere unos segundos).
const tokenValido = (claseId, t) => [-1, 0, 1, 2].some(d => t === tokenQR(claseId, d));

/* ── Sincronización celular → pantalla del aula ─────────────────────
   GitHub Pages no tiene servidor, así que el check-in hecho desde el
   celular viaja por un canal privado de ntfy.sh (servicio público de
   mensajes). El nombre del canal es aleatorio y sólo lo conoce el QR.
   Sólo se envían datos ficticios del padrón de demo. */
const NTFY = "https://ntfy.sh";
function canal() {
  let c = localStorage.getItem("autopresente_canal");
  if (!c) {
    c = "autopresente-" + Array.from(crypto.getRandomValues(new Uint8Array(10)), b => b.toString(16).padStart(2, "0")).join("");
    localStorage.setItem("autopresente_canal", c);
  }
  return c;
}

function registrarCheckin(claseId, matricula, origen) {
  return mutar(e => {
    const c = e.clases.find(x => x.id === claseId);
    if (!c || c.estado !== "en_curso") return "cerrada";
    if (c.checkins[matricula]) return "repetido";
    c.checkins[matricula] = { hora: horaTxt(new Date()), metodo: "wifi", ts: Date.now() };
    const a = padron(c.cursoId).find(x => x.matricula === matricula);
    log(e, `Check-in QR${origen ? " " + origen : ""}: ${a ? nombrePila(a.nombre) : matricula}`, "info", claseId);
    return "ok";
  });
}

let fuenteCheckins = null;
function escucharCheckins(alRecibir) {
  if (fuenteCheckins || !window.EventSource) return;
  fuenteCheckins = new EventSource(`${NTFY}/${canal()}/sse`);
  fuenteCheckins.onmessage = ev => {
    try {
      const m = JSON.parse(JSON.parse(ev.data).message);
      if (m.tipo === "checkin") { registrarCheckin(+m.claseId, String(m.matricula), "desde celular"); alRecibir && alRecibir(); }
    } catch {}
  };
}

async function enviarCheckinRemoto(topic, claseId, matricula) {
  const r = await fetch(`${NTFY}/${encodeURIComponent(topic)}`, {
    method: "POST", body: JSON.stringify({ tipo: "checkin", claseId, matricula }) });
  if (!r.ok) throw new Error("HTTP " + r.status);
}

function cabecera(activo) {
  const enlaces = [["index.html", "Panel"], ["aula.html?aula=A-201", "Pantalla A-201"], ["aula.html?aula=LAB-3", "Pantalla LAB-3"],
    ["cervantes.html", "Portal Cervantes"]];
  return `<header class="barra"><div class="in">
    <a class="marca" href="index.html"><img class="logo" src="img/icono-cervantes.png" alt="">Cervantes<span class="producto">AutoPresente</span></a>
    <span class="esp"></span>
    <nav>${enlaces.map(([h, t]) => `<a href="${h}"${t === activo ? ' style="color:#fff"' : ""}${h.startsWith("aula") || h.startsWith("cerv") ? ' target="_blank"' : ""}>${t}</a>`).join("")}</nav>
  </div></header>`;
}
