from datetime import date
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class Turn(BaseModel):
    hablante: str
    texto: str


class ClientData(BaseModel):
    nombre: str
    ultimos4_documento: str
    producto: str
    monto_vencido_cop: int
    fecha_vencimiento: date


class Conversation(BaseModel):
    id: str
    fecha_llamada: date
    datos_cliente: ClientData
    transcripcion: List[Turn] = Field(min_length=1)


class AgentSpec(BaseModel):
    nombre_agente: str
    empresa: str
    canal: Optional[str] = None
    objetivo: Optional[str] = None
    reglas: List[str]


class Dataset(BaseModel):
    descripcion: Optional[str] = None
    especificacion_agente: AgentSpec
    conversaciones: List[Conversation] = Field(min_length=1)


class EvaluateRequest(BaseModel):
    especificacion_agente: AgentSpec
    conversacion: Conversation


Result = Literal["cumple", "no_cumple", "no_aplica", "no_evaluado"]
Severity = Literal["ninguna", "leve", "grave", "critica"]


class CriterionResult(BaseModel):
    criterio: str
    regla: str
    nombre: str
    resultado: Result
    cita: Optional[str] = Field(None, description="Texto literal de la transcripción. Obligatoria si no cumple.")
    turno: Optional[int] = Field(None, description="Índice (desde 0) del turno citado.")
    explicacion: str
    severidad: Severity
    metodo: Literal["codigo", "llm", "codigo+llm"]


class ConversationResult(BaseModel):
    id: str
    estado: Literal["completo", "parcial"]
    resultado_llamada: str
    puntaje: float = Field(ge=0, le=100)
    severidad: Severity
    fallas: List[str]
    criterios: List[CriterionResult]
    advertencias: List[str] = []


class CriterionStats(BaseModel):
    criterio: str
    nombre: str
    cumple: int
    no_cumple: int
    no_aplica: int
    no_evaluado: int
    tasa_cumplimiento: Optional[float] = Field(None, description="cumple / (cumple + no_cumple). Null si nunca aplicó.")


class Report(BaseModel):
    total_conversaciones: int
    conversaciones_con_fallas: int
    puntaje_promedio: float
    severidad: Dict[str, int]
    por_criterio: List[CriterionStats]
    fallas_mas_frecuentes: List[CriterionStats]


class BatchResult(BaseModel):
    modelo: str
    generado: str
    reporte: Report
    conversaciones: List[ConversationResult]
