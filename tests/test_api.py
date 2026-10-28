import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.llm import LLMError, LLMOutput

ROOT = Path(__file__).resolve().parent.parent
RAW = json.loads((ROOT / "data" / "conversaciones_prueba_fde.json").read_text())


class Down:
    model = "fake"
    key = "x"
    tokens_in = tokens_out = 0

    async def evaluate(self, system, prompt):
        raise LLMError("HTTP 503")


class Liar(Down):
    async def evaluate(self, system, prompt):
        return LLMOutput(
            resultado_llamada="compromiso_de_pago",
            compromiso={"existe": False},
            criterios=[{"criterio": c, "resultado": "no_cumple", "cita": "esto nunca se dijo en la llamada",
                        "explicacion": "inventado"} for c in ("C3", "C6", "C7", "C8", "C9", "C10")],
        )


client = TestClient(main.app)


def test_llm_down_still_answers(monkeypatch):
    monkeypatch.setattr(main.evaluator, "llm", Down())
    r = client.post("/evaluate/batch", json=RAW)
    assert r.status_code == 200
    body = r.json()
    assert len(body["conversaciones"]) == 20
    c20 = body["conversaciones"][19]
    assert c20["estado"] == "parcial"
    assert any("LLM no disponible" in w for w in c20["advertencias"])
    assert next(c for c in c20["criterios"] if c["criterio"] == "C2")["resultado"] == "no_cumple"


def test_invented_quotes_are_not_reported_as_failures(monkeypatch):
    monkeypatch.setattr(main.evaluator, "llm", Liar())
    spec = RAW["especificacion_agente"]
    r = client.post("/evaluate", json={"especificacion_agente": spec, "conversacion": RAW["conversaciones"][0]})
    assert r.status_code == 200
    for c in r.json()["criterios"]:
        if c["resultado"] == "no_cumple":
            assert c["cita"]
    assert next(c for c in r.json()["criterios"] if c["criterio"] == "C6")["resultado"] == "no_evaluado"


def test_same_shape_for_every_conversation(monkeypatch):
    monkeypatch.setattr(main.evaluator, "llm", Down())
    body = client.post("/evaluate/batch", json=RAW).json()
    shapes = {tuple(c.keys()) for c in body["conversaciones"]}
    assert len(shapes) == 1
    assert all([k["criterio"] for k in c["criterios"]] == ["C%d" % i for i in range(1, 11)] for c in body["conversaciones"])


def test_bad_input_is_a_clear_422():
    r = client.post("/evaluate", json={"conversacion": {"id": "X"}})
    assert r.status_code == 422
    assert "error" in r.json()


def test_upload_rejects_non_json():
    r = client.post("/evaluate/batch/file", files={"file": ("x.json", b"no soy json", "application/json")})
    assert r.status_code == 400


def test_report_from_single_results(monkeypatch):
    monkeypatch.setattr(main.evaluator, "llm", Down())
    spec = client.get("/sample").json()["especificacion_agente"]
    results = [client.post("/evaluate", json={"especificacion_agente": spec, "conversacion": c}).json()
               for c in RAW["conversaciones"][:3]]
    rep = client.post("/report", json=results).json()
    assert rep["total_conversaciones"] == 3
    assert [c["criterio"] for c in rep["por_criterio"]] == ["C%d" % i for i in range(1, 11)]


class Lenient(Down):
    """Answers like a lenient model on C07: sees the ignored request but calls the tone fine."""

    async def evaluate(self, system, prompt):
        ok = {"resultado": "cumple", "cita": None, "explicacion": "ok"}
        return LLMOutput(
            resultado_llamada="llamada_interrumpida",
            compromiso={"existe": False},
            criterios=[
                dict(ok, criterio="C3", resultado="no_aplica"),
                dict(ok, criterio="C6", resultado="no_aplica"),
                {"criterio": "C7", "resultado": "no_cumple", "explicacion": "ignora el pedido de asesor",
                 "cita": "Entiendo, Hernán. Solo necesito que me indique una fecha de pago."},
                dict(ok, criterio="C8", resultado="no_aplica"),
                dict(ok, criterio="C9", resultado="no_aplica"),
                dict(ok, criterio="C10", cita="Entiendo, Hernán."),
            ],
        )


def test_pushing_for_a_date_after_ignoring_a_request_is_pressure(monkeypatch):
    monkeypatch.setattr(main.evaluator, "llm", Lenient())
    c07 = next(c for c in RAW["conversaciones"] if c["id"] == "C07")
    body = client.post("/evaluate", json={"especificacion_agente": RAW["especificacion_agente"], "conversacion": c07}).json()
    by = {c["criterio"]: c for c in body["criterios"]}
    assert by["C10"]["resultado"] == "no_cumple"
    assert "fecha" in by["C10"]["cita"]
    # C6 always applies: "no_aplica" from the model becomes "cumple".
    assert by["C6"]["resultado"] == "cumple"
