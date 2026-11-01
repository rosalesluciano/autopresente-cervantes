/* Integración con Microsoft Teams para las pestañas de AutoPresente.
   - Inicializa el SDK de Teams y copia el tema (claro, oscuro, contraste).
   - Inicia sesión sola con la cuenta de Teams (SSO) y abre la sesión del servidor.
   AP.listo es una promesa con {teams, ctx, sesion}; las páginas esperan a que se
   resuelva antes de pedir datos. Fuera de Teams resuelve igual, con teams=false. */
window.AP = window.AP || {};

AP.aplicarTema = function (tema) {
  document.documentElement.dataset.theme = (tema === "dark" || tema === "contrast") ? "dark" : "light";
};

AP.listo = (async function () {
  if (!window.microsoftTeams) return { teams: false };
  const espera = (ms) => new Promise((_, no) => setTimeout(() => no(new Error("Teams no respondió")), ms));
  try {
    await Promise.race([microsoftTeams.app.initialize(), espera(4000)]);
  } catch (e) {
    return { teams: false };
  }
  document.documentElement.classList.add("en-teams");
  const ctx = await microsoftTeams.app.getContext();
  AP.aplicarTema(ctx.app.theme);
  microsoftTeams.app.registerOnThemeChangeHandler(AP.aplicarTema);

  let sesion;
  try {
    const token = AP.SSO ? await microsoftTeams.authentication.getAuthToken() : "";
    const r = await fetch("/teams/sso", {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    const datos = await r.json().catch(() => ({}));
    sesion = r.ok ? datos : { error: datos.detail || ("HTTP " + r.status) };
  } catch (e) {
    sesion = { error: (e && e.message) || String(e) };
  }
  try { microsoftTeams.app.notifySuccess(); } catch (e) { /* versiones viejas del cliente */ }
  return { teams: true, ctx, sesion };
})();

/* Muestra un aviso arriba de la página cuando el SSO falla, con el motivo. */
AP.avisoSesion = function (sesion) {
  if (!sesion || !sesion.error) return;
  const div = document.createElement("div");
  div.className = "tarjeta";
  div.style.cssText = "margin:16px;border-color:var(--err);color:var(--err)";
  div.textContent = "No se pudo iniciar sesión con tu cuenta de Teams: " + sesion.error +
    ". Si sos docente, pedile a sistemas que revise la configuración de inicio de sesión único (README).";
  document.body.prepend(div);
};
