"""Arma el paquete de la app de Teams (manifest.json + íconos) en dist/AutoPresente-teams.zip.

    .venv\\Scripts\\python.exe scripts\\empaquetar_teams.py

Toma BASE_URL, MS_CLIENT_ID y TEAMS_APP_ID del .env. BASE_URL tiene que ser
https (Teams no carga pestañas por http): en producción, el dominio del
servidor; para probar, un túnel (ver README).
"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from app import config  # noqa: E402

VERSION_MANIFIESTO = "1.17"


def manifiesto() -> dict:
    base = config.BASE_URL
    host = base.split("://", 1)[-1].split("/")[0]
    m = {
        "$schema": f"https://developer.microsoft.com/json-schemas/teams/v{VERSION_MANIFIESTO}/MicrosoftTeams.schema.json",
        "manifestVersion": VERSION_MANIFIESTO,
        "version": "1.1.0",
        "id": config.TEAMS_APP_ID,
        "developer": {
            "name": "Institución Cervantes · AutoPresente",
            "websiteUrl": f"{base}/",
            "privacyUrl": f"{base}/privacidad",
            "termsOfUseUrl": f"{base}/terminos",
        },
        "name": {"short": "AutoPresente", "full": "AutoPresente · Asistencia automática"},
        "description": {
            "short": "Asistencia automática: QR en el aula, Teams y carga en Autogestión.",
            "full": ("Toma la asistencia sin intervención del docente. Los alumnos en el aula escanean un QR que "
                     "cambia cada 20 segundos; los que cursan por Teams quedan registrados por estar en la reunión, "
                     "con el porcentaje de tiempo con cámara encendida. Al terminar la clase, un robot carga la "
                     "asistencia en Autogestión Cervantes y el docente recibe el resumen en Teams y por correo."),
        },
        "icons": {"color": "color.png", "outline": "outline.png"},
        "accentColor": "#2A3355",
        "staticTabs": [{
            "entityId": "panel", "name": "Panel", "scopes": ["personal"],
            "contentUrl": f"{base}/teams/panel", "websiteUrl": f"{base}/",
        }],
        "configurableTabs": [{
            "configurationUrl": f"{base}/teams/config",
            "canUpdateConfiguration": True,
            "scopes": ["team", "groupChat"],
            "context": ["channelTab", "privateChatTab", "meetingChatTab", "meetingDetailsTab",
                        "meetingSidePanel", "meetingStage"],
        }],
        "permissions": ["identity"],
        "validDomains": [host],
    }
    if config.MS_CLIENT_ID:
        m["webApplicationInfo"] = {"id": config.MS_CLIENT_ID, "resource": f"api://{host}/{config.MS_CLIENT_ID}"}
    return m


def icono_color() -> bytes:
    """192×192: el ícono de la institución sobre fondo azul marino."""
    fondo = Image.new("RGBA", (192, 192), (42, 51, 85, 255))
    logo = Image.open(RAIZ / "static" / "icono-cervantes.png").convert("RGBA").resize((140, 140), Image.LANCZOS)
    circulo = Image.new("RGBA", (152, 152), (0, 0, 0, 0))
    ImageDraw.Draw(circulo).ellipse((0, 0, 151, 151), fill=(255, 255, 255, 255))
    fondo.alpha_composite(circulo, (20, 20))
    fondo.alpha_composite(logo, (26, 26))
    buf = io.BytesIO()
    fondo.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def icono_contorno() -> bytes:
    """32×32: sólo blanco sobre transparente, como pide Teams. Un QR estilizado con un tilde."""
    escala = 8
    img = Image.new("RGBA", (32 * escala, 32 * escala), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    b = (255, 255, 255, 255)
    for x, y in ((3, 3), (17, 3), (3, 17)):  # tres esquinas del QR
        d.rectangle([x * escala, y * escala, (x + 11) * escala, (y + 11) * escala], outline=b, width=2 * escala)
        d.rectangle([(x + 4) * escala, (y + 4) * escala, (x + 7) * escala, (y + 7) * escala], fill=b)
    d.line([(18 * escala, 23 * escala), (22 * escala, 27 * escala), (29 * escala, 18 * escala)], fill=b, width=3 * escala)
    buf = io.BytesIO()
    img.resize((32, 32), Image.LANCZOS).save(buf, "PNG")
    return buf.getvalue()


def main() -> int:
    if not config.BASE_URL.startswith("https://"):
        print(f"BASE_URL es {config.BASE_URL}: Teams sólo carga pestañas por https. Configurá un dominio o túnel https.")
        return 1
    if not config.MS_CLIENT_ID:
        print("Aviso: sin MS_CLIENT_ID el paquete no incluye inicio de sesión único (las pestañas pedirán ingresar).")
    destino = RAIZ / "dist"
    destino.mkdir(exist_ok=True)
    zip_path = destino / "AutoPresente-teams.zip"
    m = manifiesto()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(m, ensure_ascii=False, indent=2))
        z.writestr("color.png", icono_color())
        z.writestr("outline.png", icono_contorno())
    (destino / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Paquete listo: {zip_path}")
    print("Subilo en Teams → Aplicaciones → Administrar aplicaciones → Cargar una aplicación,")
    print("o pedile a sistemas que lo publique desde el Centro de administración de Teams.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
