import json
import logging
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import ValidationError

from .evaluator import Evaluator, report
from .models import BatchResult, ConversationResult, Dataset, EvaluateRequest, Report
from .rubric import RUBRIC

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("api")

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "conversaciones_prueba_fde.json"

app = FastAPI(
    title="Lina Call Evaluator",
    description="Evalúa llamadas del agente de cobranza Lina (Banco Andino) contra una rúbrica basada en sus reglas R1–R10.",
    version="1.0.0",
)
load_dotenv()
evaluator = Evaluator()


@app.exception_handler(RequestValidationError)
async def bad_input(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"error": "El JSON no tiene el formato esperado", "detalle": exc.errors()})


@app.exception_handler(Exception)
async def unexpected(request: Request, exc: Exception):
    log.exception("unhandled error")
    return JSONResponse(status_code=500, content={"error": "Error interno: %s" % type(exc).__name__})


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home():
    return (ROOT / "app" / "static" / "index.html").read_text()


@app.get("/health")
async def health():
    return {"ok": True, "modelo": evaluator.llm.model, "llm_configurado": bool(evaluator.llm.key)}


@app.get("/rubric")
async def rubric():
    return [c.__dict__ for c in RUBRIC]


@app.post("/evaluate", response_model=ConversationResult)
async def evaluate(req: EvaluateRequest):
    """Evalúa una conversación. Recibe la especificación del agente y la conversación."""
    return await evaluator.evaluate(req.especificacion_agente, req.conversacion)


@app.post("/evaluate/batch", response_model=BatchResult)
async def evaluate_batch(data: Dataset):
    """Evalúa el archivo completo (mismo formato que conversaciones_prueba_fde.json) y devuelve el reporte agregado."""
    return await run_batch(data)


@app.post("/evaluate/batch/file", response_model=BatchResult)
async def evaluate_file(file: UploadFile = File(...)):
    """Igual que /evaluate/batch, pero subiendo el archivo .json."""
    raw = await file.read()
    try:
        data = Dataset(**json.loads(raw))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="El archivo no es un JSON válido")
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=json.loads(e.json()))
    return await run_batch(data)


@app.post("/evaluate/sample", response_model=BatchResult)
async def evaluate_sample():
    """Evalúa el archivo de la prueba que viene en el repositorio, sin subir nada."""
    return await run_batch(Dataset(**json.loads(SAMPLE.read_text())))


@app.get("/sample", response_model=Dataset)
async def sample():
    """Devuelve el archivo de la prueba (lo usa la página para evaluar llamada por llamada)."""
    return Dataset(**json.loads(SAMPLE.read_text()))


@app.post("/report", response_model=Report)
async def make_report(results: List[ConversationResult]):
    """Arma el reporte agregado a partir de resultados de /evaluate ya calculados."""
    return report(results)


async def run_batch(data: Dataset) -> BatchResult:
    before = (evaluator.llm.tokens_in, evaluator.llm.tokens_out)
    out = await evaluator.evaluate_all(data.especificacion_agente, data.conversaciones)
    log.info("batch of %d: %d tokens in, %d tokens out", len(data.conversaciones),
             evaluator.llm.tokens_in - before[0], evaluator.llm.tokens_out - before[1])
    return out
