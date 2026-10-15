/* Estado compartido entre pestañas (panel, pantalla del aula, check-in, portal).
   Versión estática para GitHub Pages: todo corre en el navegador con localStorage. */
const CONFIG = {
  profesor: { nombre: "Prof. Demo", email: "profesor@cervantes.edu.ar" },
  dominio: "cervantes.edu.ar",
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
      email: `${sinTildes(n)}.${sinTildes(a)}@${CONFIG.dominio}` });
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
// "Apellido, Nombre" (padrón) → "Nombre Apellido"; un nombre escrito por el alumno queda como está.
const nombrePila = nom => { if (!nom.includes(", ")) return nom; const [ape, n] = nom.split(", "); return `${n} ${ape}`; };
const primerNombre = nom => nom.includes(", ") ? nom.split(", ")[1].split(" ")[0] : nom.split(" ")[0];
const emailValido = e => new RegExp(`^[^@\\s]+@${CONFIG.dominio.replace(/\./g, "\\.")}$`, "i").test(e.trim());
// Alumnos de la clase: en una prueba real, sólo quienes escanearon; en la simulación, el padrón ficticio.
const rosterDe = c => c.roster || padron(c.cursoId);

/* Token del QR: cambia cada N segundos. El QR lleva la ventana (w) en que se
   generó; quien valida es la pantalla del aula con SU reloj, así no importa
   si el reloj del celular está adelantado o atrasado. */
const ventanaActual = () => Math.floor(Date.now() / 1000 / CONFIG.reglas.rotacionQR);
function firmaQR(claseId, w) {
  let h = 2166136261; for (const ch of `${claseId}:${w}:autopresente`) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  return (h >>> 0).toString(36);
}
// Válido si la firma coincide y el QR se mostró hace menos de ~2 minutos (tiempo para escanear y elegir la cuenta).
function tokenValido(claseId, w, t) {
  const edad = ventanaActual() - w;
  if (t !== firmaQR(claseId, w)) return "invalido";
  if (edad > 6) return "vencido";
  return edad < -1 ? "invalido" : "ok";
}

/* ── Sincronización celular → pantalla del aula ─────────────────────
   GitHub Pages no tiene servidor, así que el check-in hecho desde el
   celular viaja por un canal privado de ntfy.sh (servicio público de
   mensajes). El nombre del canal es aleatorio y sólo lo conoce el QR.
   La pantalla del aula valida y responde por el mismo canal.
   Viajan nombre, matrícula y mail del alumno: es un canal público, sirve para la prueba, no para producción. */
const NTFY = "https://ntfy.sh";
function canal() {
  let c = localStorage.getItem("autopresente_canal");
  if (!c) {
    c = "autopresente-" + Array.from(crypto.getRandomValues(new Uint8Array(10)), b => b.toString(16).padStart(2, "0")).join("");
    localStorage.setItem("autopresente_canal", c);
  }
  return c;
}

/* alumno = {matricula, nombre, email}. Lo valida la pantalla del aula (o el panel). */
function registrarCheckin(claseId, alumno, w, t, origen) {
  const v = tokenValido(claseId, w, t);
  if (v !== "ok") return v;
  const email = String(alumno.email || "").trim().toLowerCase();
  const matricula = String(alumno.matricula || "").trim(), nombre = String(alumno.nombre || "").trim().replace(/\s+/g, " ");
  if (!emailValido(email)) return "dominio";
  if (!/^\d{3,10}$/.test(matricula) || nombre.length < 3) return "datos";
  return mutar(e => {
    const c = e.clases.find(x => x.id === claseId);
    if (!c || c.estado !== "en_curso") return "cerrada";
    const roster = rosterDe(c);
    let a = roster.find(x => x.email === email);
    if (!a) {
      if (!c.roster) return "no_padron";                       // simulación: sólo el padrón ficticio
      if (roster.some(x => x.matricula === matricula)) return "matricula_en_uso";
      a = { matricula, nombre, email };
      c.roster.push(a);
    }
    if (c.checkins[a.matricula]) return "repetido";
    c.checkins[a.matricula] = { hora: horaTxt(new Date()), metodo: "qr", ts: Date.now() };
    log(e, `Check-in QR${origen ? " " + origen : ""}: ${nombrePila(a.nombre)} (${a.email})`, "info", claseId);
    return "ok";
  });
}

const publicar = (topic, msg) => fetch(`${NTFY}/${encodeURIComponent(topic)}`, { method: "POST", body: JSON.stringify(msg) });

let fuenteCheckins = null;
function escucharCheckins(alRecibir) {
  if (fuenteCheckins || !window.EventSource) return;
  fuenteCheckins = new EventSource(`${NTFY}/${canal()}/sse`);
  fuenteCheckins.onmessage = ev => {
    try {
      const m = JSON.parse(JSON.parse(ev.data).message);
      if (m.tipo !== "checkin") return;
      const r = registrarCheckin(+m.claseId, m.alumno || {}, +m.w, String(m.t), "desde celular");
      publicar(canal(), { tipo: "ack", id: m.id, resultado: r }).catch(() => {});
      alRecibir && alRecibir();
    } catch {}
  };
}

/* Desde el celular: envía el check-in y espera la respuesta de la pantalla del aula. */
function enviarCheckinRemoto(topic, claseId, alumno, w, t) {
  return new Promise((resolver, rechazar) => {
    const id = Math.random().toString(36).slice(2);
    const es = new EventSource(`${NTFY}/${encodeURIComponent(topic)}/sse`);
    const fin = (fn, v) => { clearTimeout(reloj); es.close(); fn(v); };
    const reloj = setTimeout(() => fin(rechazar, new Error("sin_respuesta")), 12000);
    es.onmessage = ev => {
      try { const m = JSON.parse(JSON.parse(ev.data).message); if (m.tipo === "ack" && m.id === id) fin(resolver, m.resultado); } catch {}
    };
    es.addEventListener("open", () => {
      publicar(topic, { tipo: "checkin", id, claseId, alumno, w, t }).catch(err => fin(rechazar, err));
    }, { once: true });
    es.onerror = () => { if (es.readyState === EventSource.CLOSED) fin(rechazar, new Error("sin_conexion")); };
  });
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
