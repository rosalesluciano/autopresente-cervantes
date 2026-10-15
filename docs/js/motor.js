/* Motor del pipeline (corre en el panel): en_curso → Teams → Cervantes → correo → completada. */
const PASOS = ["en_curso", "esperando_teams", "cargando_cervantes", "enviando_reporte", "completada"];

function simularClase(cursoId, minutos, escaneos) {
  return mutar(e => {
    const ini = new Date(), fin = new Date(ini.getTime() + minutos * 60000);
    const id = e.sig++;
    const alumnos = padron(cursoId), r = rng(id + cursoId + ini.getTime());
    const checkins = {};
    alumnos.map(a => [r(), a]).sort((x, y) => x[0] - y[0]).slice(0, escaneos)
      .forEach(([, a]) => checkins[a.matricula] = { hora: horaTxt(ini), metodo: "wifi" });
    e.clases.unshift({ id, cursoId, inicio: ini.getTime(), fin: fin.getTime(), estado: "en_curso",
      detalle: "QR activo en el aula", checkins, registros: null, resumen: null, finTs: null });
    const c = CONFIG.cursos[cursoId];
    log(e, `Clase programada ${c.materia} ${horaTxt(ini)}-${horaTxt(fin)}`, "info", id);
    log(e, "QR activo en el aula", "info", id);
    return id;
  });
}

function terminarYa(id) {
  mutar(e => { const c = e.clases.find(x => x.id === id); if (c && c.estado === "en_curso") c.fin = Date.now(); });
}

function estado(e, c, nuevo, detalle) {
  c.estado = nuevo; c.detalle = detalle; c.desde = Date.now(); log(e, detalle, "info", c.id);
}

/* Reporte de asistencia de Teams simulado para quienes no escanearon el QR. */
function teamsSimulado(c) {
  const r = rng("teams" + c.id), dur = (c.fin - c.inicio) / 1000, salida = [];
  for (const a of padron(c.cursoId)) {
    if (c.checkins[a.matricula] || r() < 0.35) continue;
    const seg = dur * (r() < 0.15 ? 0.1 + r() * 0.3 : 0.75 + r() * 0.25);
    const cam = elegir(r, [80 + r() * 20, 40 + r() * 40, r() * 25]);
    salida.push({ email: a.email, segundos: seg, pct_camara: Math.round(cam) });
  }
  return salida;
}

function consolidar(c, virtuales) {
  const durMin = (c.fin - c.inicio) / 60000, porEmail = Object.fromEntries(virtuales.map(v => [v.email, v]));
  c.registros = padron(c.cursoId).map(a => {
    const ck = c.checkins[a.matricula], v = porEmail[a.email];
    const f = { ...a, estado: "Ausente", modalidad: "-", minutos: null, pct_tiempo: null, pct_camara: null };
    if (ck) Object.assign(f, { estado: "Presente", modalidad: "Presencial" });
    else if (v) {
      const min = v.segundos / 60, pct = Math.min(100, 100 * min / durMin);
      Object.assign(f, { modalidad: "Virtual", minutos: Math.round(min * 10) / 10, pct_tiempo: Math.round(pct),
        pct_camara: v.pct_camara, estado: pct >= CONFIG.reglas.minimoVirtual ? "Presente" : "Ausente" });
    }
    return f;
  });
  const pres = c.registros.filter(f => f.estado === "Presente");
  c.resumen = { total: c.registros.length, presenciales: pres.filter(f => f.modalidad === "Presencial").length,
    virtuales: pres.filter(f => f.modalidad === "Virtual").length, ausentes: c.registros.length - pres.length };
  return c.resumen;
}

let robotEnCurso = null;
function tickMotor(alAbrirRobot) {
  mutar(e => {
    const ahora = Date.now();
    for (const c of e.clases) {
      if (c.estado === "en_curso" && ahora >= c.fin)
        estado(e, c, "esperando_teams", "Clase finalizada, descargando reporte de asistencia de Teams");
      else if (c.estado === "esperando_teams" && ahora - c.desde > 2500) {
        const r = consolidar(c, teamsSimulado(c));
        log(e, `Consolidado: ${r.presenciales} presenciales, ${r.virtuales} virtuales, ${r.ausentes} ausentes`, "info", c.id);
        estado(e, c, "cargando_cervantes", "Robot iniciando sesión en Autogestión Cervantes");
      } else if (c.estado === "cargando_cervantes" && robotEnCurso !== c.id && !robotEnCurso) {
        robotEnCurso = c.id;
        const curso = CONFIG.cursos[c.cursoId];
        localStorage.setItem("autopresente_robot", JSON.stringify({
          claseId: c.id, materia: curso.materia, division: curso.division, fecha: fechaTxt(new Date(c.inicio)),
          mes: new Date(c.inicio).getMonth(),
          filas: c.registros.map(f => ({ matricula: f.matricula, nombre: f.nombre, presente: f.estado === "Presente" })),
        }));
        alAbrirRobot(c.id);
      } else if (c.estado === "enviando_reporte" && ahora - c.desde > 1500) {
        c.correo = true;
        estado(e, c, "completada", `Reporte enviado a ${CONFIG.profesor.email}`);
      }
    }
  });
}

function robotTermino(claseId, ok, detalle) {
  robotEnCurso = null;
  mutar(e => {
    const c = e.clases.find(x => x.id === claseId); if (!c) return;
    if (ok) estado(e, c, "enviando_reporte", detalle);
    else { c.desde = Date.now(); log(e, `Error en Cervantes, reintento automático: ${detalle}`, "warn", c.id); }
  });
}

function htmlCorreo(c) {
  const curso = CONFIG.cursos[c.cursoId], r = c.resumen, AZ = "#2A3355", alerta = CONFIG.reglas.alertaCamara;
  const virt = c.registros.filter(f => f.modalidad === "Virtual").sort((a, b) => b.pct_camara - a.pct_camara);
  const aus = c.registros.filter(f => f.estado === "Ausente").map(f => esc(f.nombre)).join(" · ") || "Ninguno";
  const kpi = (n, t) => `<td style="padding:14px;text-align:center;border:1px solid #e6e8ef;border-radius:8px">
    <div style="font-size:26px;font-weight:700;color:${AZ}">${n}</div><div style="font-size:12px;color:#5b6280;text-transform:uppercase;letter-spacing:.04em">${t}</div></td>`;
  const filas = virt.map(f => {
    const baja = f.pct_camara < alerta;
    return `<tr><td style="padding:8px 10px;border-bottom:1px solid #e6e8ef">${esc(f.nombre)}<div style="color:#8a90a6;font-size:12px">${f.pct_tiempo}% de la clase</div></td>
      <td style="padding:8px 10px;border-bottom:1px solid #e6e8ef;text-align:right;font-weight:600;color:${baja ? "#b42318" : AZ}">${f.pct_camara}%
      ${baja ? `<span style="background:#fdecea;color:#b42318;border-radius:4px;padding:1px 6px;font-size:11px;margin-left:6px">Alerta de atención</span>` : ""}</td></tr>`;
  }).join("") || `<tr><td style="padding:8px 10px;color:#8a90a6">Sin asistentes virtuales.</td></tr>`;
  return `<div style="font-family:Segoe UI,Arial,sans-serif;background:#f4f5f9;padding:20px">
  <div style="max-width:620px;margin:auto;background:#fff;border-radius:12px;overflow:hidden;border:1px solid #e6e8ef">
    <div style="background:${AZ};color:#fff;padding:20px 24px;display:flex;gap:14px;align-items:center">
      <img src="img/icono-cervantes.png" alt="" style="width:44px;height:44px;border-radius:50%;background:#fff;padding:2px">
      <div><div style="font-size:12px;opacity:.75;letter-spacing:.08em;text-transform:uppercase">Para: ${esc(CONFIG.profesor.email)}</div>
      <div style="font-size:19px;font-weight:600;margin-top:2px">Asistencia registrada exitosamente</div>
      <div style="opacity:.85">${esc(curso.materia)} · División ${curso.division} · ${fechaTxt(new Date(c.inicio))}</div></div>
    </div>
    <div style="padding:20px 24px">
      <table style="width:100%;border-collapse:separate;border-spacing:8px 0"><tr>${kpi(r.presenciales, "Presenciales")}${kpi(r.virtuales, "Virtuales")}${kpi(r.ausentes, "Ausentes")}</tr></table>
      <h3 style="color:${AZ};font-size:15px;margin:22px 0 6px">Participación virtual · cámara encendida</h3>
      <table style="width:100%;border-collapse:collapse;font-size:14px">${filas}</table>
      <h3 style="color:${AZ};font-size:15px;margin:22px 0 6px">Ausentes</h3>
      <p style="font-size:14px;color:#3a4160;margin:0">${aus}</p>
      <p style="font-size:12px;color:#8a90a6;margin-top:22px;border-top:1px solid #e6e8ef;padding-top:12px">Cargado automáticamente en Autogestión Cervantes.
      Virtual = presente si estuvo ≥ ${CONFIG.reglas.minimoVirtual}% de la clase. No necesitás hacer nada.</p>
    </div></div></div>`;
}
