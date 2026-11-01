"""Inicio de sesión único de Teams y paquete de la app."""
import io
import json
import time
import zipfile
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from PIL import Image

from app import auth, config

TENANT, CLIENTE, HOST = "11111111-2222-3333-4444-555555555555", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "asistencia.cervantes.edu.ar"


@pytest.fixture
def sso(monkeypatch):
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(config, "MS_TENANT_ID", TENANT)
    monkeypatch.setattr(config, "MS_CLIENT_ID", CLIENTE)
    monkeypatch.setattr(config, "BASE_URL", f"https://{HOST}")
    publica = SimpleNamespace(get_signing_key_from_jwt=lambda _t: SimpleNamespace(key=clave.public_key()))
    monkeypatch.setattr(auth, "_jwks", lambda: publica)

    def firmar(**cambios):
        ahora = int(time.time())
        claims = {"aud": f"api://{HOST}/{CLIENTE}", "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
                  "iat": ahora, "nbf": ahora, "exp": ahora + 600, "tid": TENANT,
                  "preferred_username": "profesor@cervantes.edu.ar", "name": "Prof. Demo", **cambios}
        return jwt.encode(claims, clave, algorithm="RS256")
    return firmar


def test_token_de_teams_valido(sso):
    claims = auth.validar_token_teams(sso())
    assert claims["preferred_username"] == "profesor@cervantes.edu.ar"
    assert auth.validar_token_teams(sso(iss=f"https://sts.windows.net/{TENANT}/"))  # tokens v1 también


@pytest.mark.parametrize("cambio", [
    {"aud": "api://otro.com/xyz"},
    {"iss": "https://login.microsoftonline.com/otro-tenant/v2.0"},
    {"exp": int(time.time()) - 10},
])
def test_token_de_teams_rechazado(sso, cambio):
    with pytest.raises((jwt.PyJWTError, HTTPException)):
        auth.validar_token_teams(sso(**cambio))


def test_paquete_de_la_app_de_teams(monkeypatch, tmp_path):
    import scripts.empaquetar_teams as emp

    monkeypatch.setattr(config, "BASE_URL", f"https://{HOST}")
    monkeypatch.setattr(config, "MS_CLIENT_ID", CLIENTE)
    assert emp.main() == 0
    with zipfile.ZipFile(emp.RAIZ / "dist" / "AutoPresente-teams.zip") as z:
        assert sorted(z.namelist()) == ["color.png", "manifest.json", "outline.png"]
        m = json.loads(z.read("manifest.json"))
        color = Image.open(io.BytesIO(z.read("color.png")))
        contorno = Image.open(io.BytesIO(z.read("outline.png"))).convert("RGBA")
    assert m["validDomains"] == [HOST]
    assert m["webApplicationInfo"] == {"id": CLIENTE, "resource": f"api://{HOST}/{CLIENTE}"}
    assert m["staticTabs"][0]["contentUrl"] == f"https://{HOST}/teams/panel"
    assert "meetingSidePanel" in m["configurableTabs"][0]["context"]
    assert len(m["description"]["short"]) <= 80 and len(m["name"]["short"]) <= 30
    assert color.size == (192, 192) and contorno.size == (32, 32)
    # El ícono de contorno sólo puede tener blanco y transparencia.
    pixeles = contorno.get_flattened_data() if hasattr(contorno, "get_flattened_data") else contorno.getdata()
    assert all(px[:3] == (255, 255, 255) for px in pixeles if px[3] > 200)
