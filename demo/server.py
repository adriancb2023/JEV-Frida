"""Demo web (reclutador + certificados) que usa KEV de verdad.

- Todo lo que es decision (apto si/no, % cumplimiento, brechas) sale de
  POST {KEV_URL}/v1/systemone (contrato en kev/serve.py y kev/api.py).
  KEV no genera texto: solo noul / choice / score.
- Sin Redis ni workers: las llamadas a KEV pasan por un unico asyncio.Lock
  (cola 1 en 1 dentro del proceso) y la "BBDD" es demo/db.json con escritura
  atomica (tmp + os.replace) bajo threading.Lock.
- Demo 2 (documentos): el veredicto ORIGINAL SI/NO lo da el hash SHA-256
  calculado en el navegador (el fichero nunca sube). KEV solo aporta un
  score de riesgo orientativo sobre metadatos, etiquetado como tal.

Run local (con KEV en localhost:9936):  KEV_URL=http://localhost:9936 python -m demo.server
Run compose (servicio demo):             docker compose up --build  -> demo en :8010, KEV en :9936
"""
import asyncio
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", BASE)
DB_PATH = os.path.join(DATA_DIR, "db.json")
STATIC = os.path.join(BASE, "static")
KEV_URL = os.environ.get("KEV_URL", "http://kev-api:9936").rstrip("/")
PORT = int(os.environ.get("PORT", "8010"))

kev_lock = asyncio.Lock()   # cola 1-en-1 hacia KEV, sin Redis
db_lock = threading.Lock()

app = FastAPI(title="kev-demo")


# ---------- BBDD JSON ----------

def _db_read():
    if not os.path.exists(DB_PATH):
        return {"offers": [], "candidates": [], "certs": []}
    with open(DB_PATH, encoding="utf-8") as f:
        return json.load(f)


def _db_write(db):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = DB_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, DB_PATH)


def _db_update(fn):
    with db_lock:
        db = _db_read()
        out = fn(db)
        _db_write(db)
        return out


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- KEV ----------

def _post_systemone(payload, timeout=180):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        KEV_URL + "/v1/systemone", data=data,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise HTTPException(502, f"KEV respondio {e.code}: {e.read().decode()[:300]}")
    except Exception as e:  # KEV caido o inalcanzable
        raise HTTPException(502, f"No se pudo contactar con KEV en {KEV_URL}: {e}")


async def kev_ask(payload):
    """Una pregunta a KEV, encolada: solo una llamada a la vez."""
    async with kev_lock:
        return await asyncio.to_thread(_post_systemone, payload)


@app.get("/api/health")
async def health():
    try:
        req = urllib.request.Request(KEV_URL + "/v1/models")
        with urllib.request.urlopen(req, timeout=10) as r:
            models = json.loads(r.read().decode()).get("models", [])
        return {"kev": "ok", "model": models[0]["name"] if models else "?"}
    except Exception as e:
        return {"kev": "down", "detail": str(e)[:200], "kev_url": KEV_URL}


# ---------- Demo 1: reclutador ----------

class OfferIn(BaseModel):
    titulo: str
    descripcion: str = ""
    requisitos: list[str] = Field(default_factory=list, max_length=10)


class CvIn(BaseModel):
    nombre: str
    texto: str = Field(min_length=1, max_length=20000)


@app.post("/api/offers")
def create_offer(o: OfferIn):
    oid = uuid.uuid4().hex[:12]

    def go(db):
        db["offers"].append({"id": oid, "titulo": o.titulo,
                             "descripcion": o.descripcion,
                             "requisitos": [r for r in o.requisitos if r.strip()][:10],
                             "creada": _now()})
        return {"id": oid}
    return _db_update(go)


@app.get("/api/offers")
def list_offers():
    return _db_read()["offers"]


@app.post("/api/offers/{oid}/candidates")
def add_candidate(oid: str, cv: CvIn):
    cid = uuid.uuid4().hex[:12]

    def go(db):
        if not any(o["id"] == oid for o in db["offers"]):
            raise HTTPException(404, "oferta no existe")
        db["candidates"].append({"id": cid, "offer_id": oid, "nombre": cv.nombre,
                                 "texto": cv.texto, "resultado": None, "evaluado": None})
        return {"id": cid}
    return _db_update(go)


@app.get("/api/offers/{oid}/candidates")
def list_candidates(oid: str):
    db = _db_read()
    return [c for c in db["candidates"] if c["offer_id"] == oid]


@app.delete("/api/offers/{oid}/candidates/{cid}")
def delete_candidate(oid: str, cid: str):
    def go(db):
        before = len(db["candidates"])
        db["candidates"] = [c for c in db["candidates"]
                            if not (c["id"] == cid and c["offer_id"] == oid)]
        if len(db["candidates"]) == before:
            raise HTTPException(404, "candidato no existe")
        return {"ok": True}
    return _db_update(go)


@app.post("/api/offers/{oid}/evaluate")
async def evaluate_offer(oid: str):
    """Evalua TODOS los CVs de la oferta, de 1 en 1 (cola bajo kev_lock).
    Por candidato, UNA request KEV con: apto (noul si/no), cumplimiento
    (score 0-4 -> % sobre la oferta) y un noul por requisito (brechas)."""
    db = _db_read()
    ofertas = [o for o in db["offers"] if o["id"] == oid]
    if not ofertas:
        raise HTTPException(404, "oferta no existe")
    oferta = ofertas[0]
    cands = [c for c in db["candidates"] if c["offer_id"] == oid]
    if not cands:
        raise HTTPException(422, "sube al menos un CV primero")

    results = []
    for c in cands:  # secuencial: encolado 1-en-1, sin Redis
        questions = {
            "apto": {
                "type": "noul",
                "instructions": "¿Este candidato es APTO para la oferta? Solo si cumple los requisitos esenciales.",
                "criteria": {"true": "Si, es apto para el puesto",
                             "false": "No, no es apto para el puesto"},
            },
            "cumplimiento": {
                "type": "score",
                "instructions": "¿En que grado cumple el CV la oferta? La oferta completa es el 100%.",
                "criteria": ["no cumple nada", "cumple poco", "cumple a medias",
                             "cumple casi todo", "cumple el 100% o mas"],
            },
        }
        for i, r in enumerate(oferta["requisitos"]):
            questions[f"req_{i}"] = {
                "type": "noul",
                "instructions": f"¿El CV cumple este requisito de la oferta? {r}",
                "criteria": {"true": f"Si cumple: {r}", "false": f"No cumple: {r}"},
            }
        resp = await kev_ask({"state": {"oferta": oferta, "cv": c["texto"]},
                              "model": "kev-latest", "questions": questions})
        a = resp["answers"]
        score = a["cumplimiento"]["score"]  # 0..4
        brechas = [oferta["requisitos"][i] for i in range(len(oferta["requisitos"]))
                   if a[f"req_{i}"]["noul"] < 0.5]
        res = {"apto_p": round(a["apto"]["noul"], 4),
               "apto": a["apto"]["noul"] >= 0.5,
               "score": score,
               "porcentaje": round(score / 4 * 100, 1),
               "brechas": brechas,
               "detalle_requisitos": {oferta["requisitos"][i]: round(a[f"req_{i}"]["noul"], 4)
                                      for i in range(len(oferta["requisitos"]))},
               "latency_ms": resp.get("latency_ms")}

        def go(db, c=c, res=res):
            for row in db["candidates"]:
                if row["id"] == c["id"]:
                    row["resultado"] = res
                    row["evaluado"] = _now()
        _db_update(go)
        results.append({"id": c["id"], "nombre": c["nombre"], **res})

    results.sort(key=lambda r: (-r["porcentaje"], -r["apto_p"]))  # ranking
    return {"oferta": oferta["titulo"], "ranking": results}


# ---------- Demo 2: certificados (hash = veredicto, KEV = riesgo) ----------

class CertIn(BaseModel):
    sha256: str = Field(min_length=64, max_length=64)
    filename: str
    size: int = 0


@app.post("/api/certs")
async def register_cert(c: CertIn):
    """Registra SOLO la huella (el documento nunca sale del navegador).
    KEV aporta un score de riesgo orientativo sobre metadatos, nada mas."""
    sha = c.sha256.lower()
    if any(x not in "0123456789abcdef" for x in sha):
        raise HTTPException(422, "sha256 no valido")
    cid = uuid.uuid4().hex[:12]
    riesgo = None
    try:  # orientativo; si KEV esta caido se registra igual con riesgo=null
        resp = await kev_ask({
            "state": {"filename": c.filename, "size_bytes": c.size, "sha256": sha},
            "model": "kev-latest",
            "questions": {"riesgo": {
                "type": "score",
                "instructions": "Segun los metadatos, ¿que riesgo hay de que este registro sea anomalo o fraudulento?",
                "criteria": ["ningun riesgo", "riesgo bajo", "riesgo medio", "riesgo alto"]}}})
        riesgo = round(resp["answers"]["riesgo"]["score"] / 3, 4)
    except HTTPException:
        riesgo = None

    def go(db):
        db["certs"].append({"id": cid, "sha256": sha, "filename": c.filename,
                            "size": c.size, "riesgo_kev": riesgo, "creado": _now()})
        return {"id": cid, "riesgo_kev": riesgo}
    return _db_update(go)


@app.get("/api/certs/{cid}")
def get_cert(cid: str):
    for c in _db_read()["certs"]:
        if c["id"] == cid:
            return c
    raise HTTPException(404, "certificado no existe")


@app.post("/api/certs/{cid}/verify")
def verify_cert(cid: str, body: CertIn):
    """Veredicto ORIGINAL SI/NO: comparacion exacta de hashes. Determinista."""
    cert = None
    for c in _db_read()["certs"]:
        if c["id"] == cid:
            cert = c
    if cert is None:
        raise HTTPException(404, "certificado no existe")
    same = cert["sha256"] == body.sha256.lower()
    return {"id": cid, "identico": same,
            "veredicto": "ORIGINAL: identico al registrado" if same
            else "ALTERADO: no coincide con el registrado",
            "registrado": {"filename": cert["filename"], "creado": cert["creado"]}}


# ----- PDFs de prueba + certificado sellado (PDF minimo, stdlib) -----

def _esc(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _build_pdf(title_lines, body_lines, matrix_bits=None):
    """Genera un PDF 1.4 de una pagina con texto Helvetica y una matriz
    visual 16x16 derivada del hash (huella visual, NO es un QR escaneable;
    la URL de verificacion va como texto). Solo stdlib."""
    ops = ["BT /F1 13 Tf 50 790 Td 14 TL"]
    for ln in title_lines:
        ops.append(f"({_esc(ln)}) Tj T*")
    ops.append("ET BT /F1 10 Tf 50 740 Td 13 TL")
    y = 740
    for ln in body_lines:
        for chunk in [ln[i:i + 100] for i in range(0, len(ln), 100)] or [""]:
            if y < 60:
                break
            ops.append(f"({_esc(chunk)}) Tj T*")
            y -= 13
    ops.append("ET")
    if matrix_bits:
        x0, y0, m = 50, y - 140, 8
        ops.append("0.1 0.1 0.1 rg")
        for r in range(16):
            for col in range(16):
                if matrix_bits[r * 16 + col] == "1":
                    ops.append(f"{x0 + col * m} {y0 + (15 - r) * m} {m} {m} re f")
        ops.append("BT /F1 8 Tf 50 %d Td (Huella visual derivada del SHA-256: decorativa, no escaneable.) Tj ET" % (y0 - 16))
    content = "\n".join(ops).encode("latin-1", errors="replace")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"]
    out = [b"%PDF-1.4\n"]
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(b"".join(out)))
        out.append(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = len(b"".join(out))
    out.append(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.append(b"%010d 00000 n \n" % off)
    out.append(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF"
               % (len(objs) + 1, xref))
    return b"".join(out)


TEST_LINES = ["DOCUMENTO DE PRUEBA para la demo de autenticidad.",
              "Si cambias un solo caracter de este PDF, su SHA-256 cambia",
              "por completo y la verificacion dara ALTERADO."]


@app.get("/api/demo-pdf/original")
def demo_pdf_original():
    return Response(_build_pdf(["Certificado de prueba"], TEST_LINES),
                    media_type="application/pdf")


@app.get("/api/demo-pdf/tampered")
def demo_pdf_tampered():
    """El mismo PDF con UN caracter cambiado (PRUEBA -> PRUEBB)."""
    pdf = _build_pdf(["Certificado de prueba"], TEST_LINES)
    return Response(pdf.replace(b"PRUEBA", b"PRUEBB", 1), media_type="application/pdf")


@app.get("/api/certs/{cid}/certificado.pdf")
def cert_pdf(cid: str):
    cert = None
    for c in _db_read()["certs"]:
        if c["id"] == cid:
            cert = c
    if cert is None:
        raise HTTPException(404, "certificado no existe")
    bits = bin(int(cert["sha256"], 16))[2:].zfill(256)
    pdf = _build_pdf(
        [f"Sello digital de autenticidad  ID {cert['id']}"],
        [f"Documento: {cert['filename']}  ({cert['size']} bytes)",
         f"SHA-256: {cert['sha256']}",
         f"Registrado: {cert['creado']}",
         f"Verificar en: /verifica.html?id={cert['id']}",
         "El hash se calculo en tu navegador; el documento nunca salio de tu ordenador."],
        matrix_bits=bits)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename=sello-{cid}.pdf"})


# ---------- Estatico ----------
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def main():
    import uvicorn
    uvicorn.run(app, host=os.environ.get("DEMO_HOST", "0.0.0.0"), port=PORT)


if __name__ == "__main__":
    main()
