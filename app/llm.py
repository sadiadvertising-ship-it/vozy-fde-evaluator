import asyncio
import hashlib
import json
import logging
import os
import re
from typing import List, Literal, Optional

import httpx
from pydantic import BaseModel, ValidationError

log = logging.getLogger("llm")

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class LLMError(Exception):
    pass


class LLMCriterion(BaseModel):
    criterio: Literal["C3", "C6", "C7", "C8", "C9", "C10"]
    resultado: Literal["cumple", "no_cumple", "no_aplica"]
    cita: Optional[str] = None
    explicacion: str


class Commitment(BaseModel):
    existe: bool
    fecha: Optional[str] = None
    cita: Optional[str] = None


class LLMOutput(BaseModel):
    resultado_llamada: str
    compromiso: Commitment
    criterios: List[LLMCriterion]
    modelo: Optional[str] = None


SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "resultado_llamada": {
            "type": "STRING",
            "enum": ["compromiso_de_pago", "pago_ya_realizado", "escalado_a_asesor",
                     "tercero_contesta", "sin_acuerdo", "llamada_interrumpida"],
        },
        "compromiso": {
            "type": "OBJECT",
            "properties": {
                "existe": {"type": "BOOLEAN"},
                "fecha": {"type": "STRING", "nullable": True, "description": "YYYY-MM-DD o null"},
                "cita": {"type": "STRING", "nullable": True},
            },
            "required": ["existe"],
        },
        "criterios": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "criterio": {"type": "STRING", "enum": ["C3", "C6", "C7", "C8", "C9", "C10"]},
                    "resultado": {"type": "STRING", "enum": ["cumple", "no_cumple", "no_aplica"]},
                    "cita": {"type": "STRING", "nullable": True},
                    "explicacion": {"type": "STRING"},
                },
                "required": ["criterio", "resultado", "explicacion"],
                # Reason first, decide last: the verdict has to follow from the explanation.
                "propertyOrdering": ["criterio", "explicacion", "cita", "resultado"],
            },
        },
    },
    "required": ["resultado_llamada", "compromiso", "criterios"],
}


class Gemini:
    def __init__(self):
        self.key = os.getenv("GEMINI_API_KEY", "")
        # Flash-Lite: the free tier gives it 500 requests/day against 20/day for Flash.
        self.model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
        # Used only when the main model keeps failing (429/503, quota, outage).
        self.fallback = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.1-flash-lite")
        self.thinking = os.getenv("GEMINI_THINKING_LEVEL", "low")
        self.timeout = float(os.getenv("LLM_TIMEOUT", "60"))
        self.retries = int(os.getenv("LLM_RETRIES", "3"))
        self.limit = int(os.getenv("LLM_CONCURRENCY", "3"))
        self.sems = {}
        self.cache = {}
        self.tokens_in = 0
        self.tokens_out = 0

    async def evaluate(self, system: str, prompt: str) -> LLMOutput:
        if not self.key:
            raise LLMError("GEMINI_API_KEY no está configurada")

        h = hashlib.sha256((self.model + system + prompt).encode()).hexdigest()
        if h in self.cache:
            return self.cache[h]

        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseSchema": SCHEMA,
            },
        }
        if self.thinking:
            body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": self.thinking}

        # One semaphore per event loop: on Python 3.9 a semaphore is bound to the loop that created it.
        loop = asyncio.get_running_loop()
        sem = self.sems.setdefault(id(loop), asyncio.Semaphore(self.limit))

        errors = []
        async with sem:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                for model in [m for m in (self.model, self.fallback) if m]:
                    try:
                        out = await self.call(http, model, body)
                    except LLMError as e:
                        errors.append("%s: %s" % (model, e))
                        log.warning("model %s failed: %s", model, e)
                        continue
                    self.cache[h] = out
                    return out

        raise LLMError("; ".join(errors))

    async def call(self, http: httpx.AsyncClient, model: str, body: dict) -> LLMOutput:
        last = "sin respuesta"
        for attempt in range(self.retries):
            try:
                r = await http.post(URL.format(model=model), json=body, headers={"x-goog-api-key": self.key})
            except httpx.HTTPError as e:
                last = "error de red: %s" % type(e).__name__
                await asyncio.sleep(2 ** attempt)
                continue

            if r.status_code == 429 and "PerDay" in r.text:
                # Waiting does not help with a daily quota; go to the next model now.
                raise LLMError("cuota diaria agotada")
            if r.status_code in (429, 500, 502, 503, 504):
                last = "HTTP %d" % r.status_code
                await asyncio.sleep(retry_delay(r.text, attempt))
                continue
            if r.status_code != 200:
                raise LLMError("HTTP %d: %s" % (r.status_code, r.text[:200]))

            data = r.json()
            usage = data.get("usageMetadata", {})
            self.tokens_in += usage.get("promptTokenCount", 0)
            self.tokens_out += usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
            try:
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                out = LLMOutput(**json.loads(text))
                out.modelo = model
                return out
            except (KeyError, IndexError, json.JSONDecodeError, ValidationError) as e:
                # A malformed answer is retried like a transient error.
                last = "respuesta inválida: %s" % type(e).__name__
                log.warning("invalid output from %s (attempt %d): %s", model, attempt + 1, e)

        raise LLMError(last)


def retry_delay(text: str, attempt: int) -> float:
    m = re.search(r'"retryDelay":\s*"(\d+)s"', text)
    if m:
        return min(float(m.group(1)) + 1, 60)
    return 2 ** (attempt + 1)
