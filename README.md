# Evaluador de llamadas de Lina

Servicio que evalúa las llamadas de **Lina**, el agente de cobranza por voz de Banco Andino, contra una rúbrica de 10 criterios basada en sus reglas R1–R10. Para cada llamada devuelve el resultado de cada criterio (cumple / no cumple / no aplica) con la cita textual que lo justifica, un puntaje y la severidad de las fallas. Para un archivo completo agrega un reporte con la tasa de cumplimiento por criterio y las fallas más frecuentes.

- Servicio: https://vozy-fde-evaluator.onrender.com (plan gratis de Render: si estuvo inactivo, la primera carga tarda cerca de un minuto)
- Documentación de la API (Swagger): https://vozy-fde-evaluator.onrender.com/docs
- Resultado de las 20 llamadas: [`results.json`](results.json)
- Hallazgos para el cliente: [`HALLAZGOS.md`](HALLAZGOS.md)
- Video: _pendiente_

## Cómo usarlo

**Desde la página** (`/`): "Evaluar el archivo de la prueba" evalúa el archivo de este repo; "Subir otro JSON" evalúa cualquier archivo con el mismo formato. La tabla se llena llamada por llamada; clic en una celda muestra la cita y la explicación. Al terminar se puede descargar `results.json`.

**Desde la API:**

| Método | Ruta | Qué hace |
|---|---|---|
| POST | `/evaluate` | Evalúa una conversación. Cuerpo: `{"especificacion_agente": {...}, "conversacion": {...}}` |
| POST | `/evaluate/batch` | Evalúa el archivo completo (mismo formato que `conversaciones_prueba_fde.json`) y devuelve el reporte |
| POST | `/evaluate/batch/file` | Igual, subiendo el `.json` como archivo |
| POST | `/evaluate/sample` | Evalúa el archivo de la prueba que viene en el repo |
| POST | `/report` | Arma el reporte a partir de resultados de `/evaluate` |
| GET | `/rubric` | La rúbrica |
| GET | `/health` | Estado y modelo configurado |

```bash
curl -X POST https://vozy-fde-evaluator.onrender.com/evaluate/batch/file -F "file=@data/conversaciones_prueba_fde.json"
```

## Correrlo local

Requiere Python 3.9+ y una clave gratuita de Gemini ([aistudio.google.com/apikey](https://aistudio.google.com/apikey)).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # y poner GEMINI_API_KEY
.venv/bin/uvicorn app.main:app --reload
# http://localhost:8000  ·  http://localhost:8000/docs

.venv/bin/python -m pytest              # pruebas (no llaman a Gemini)
.venv/bin/python scripts/compare.py     # evalúa el archivo y lo compara con la calificación manual
```

## Arquitectura

```
JSON ──> Pydantic (valida la entrada)
          │
          ├─> checks.py: Facts  ── hechos por código: quién contesta, si confirmó nombre,
          │                        dígitos dados, monto y fecha dichos, fecha del compromiso,
          │                        palabras de amenaza
          │
          ├─> llm.py: Gemini ───── 1 llamada por conversación, temperatura 0, salida forzada
          │                        a un JSON Schema y validada con Pydantic. Recibe la
          │                        especificación, los datos del cliente, los hechos y la
          │                        transcripción numerada por turnos
          │
          └─> evaluator.py ─────── combina: R1, R2, R4 por código; R5 código + LLM;
                                   R3, R6–R10 por LLM con controles de código.
                                   Verifica que cada cita exista en la transcripción,
                                   calcula puntaje y severidad, arma el reporte
```

| Archivo | Qué tiene |
|---|---|
| `app/models.py` | Modelos de entrada y salida (Pydantic). Definen el formato y el Swagger |
| `app/rubric.py` | Los 10 criterios: regla, cuándo aplican, severidad, método |
| `app/checks.py` | Extracción por código: montos en palabras, dígitos, fechas, citas |
| `app/llm.py` | Cliente de Gemini por REST: esquema, reintentos, modelo de respaldo, caché |
| `app/evaluator.py` | Prompt, decisión por criterio, puntaje, severidad y reporte |
| `app/main.py` | API (FastAPI) |
| `app/static/index.html` | Página con la tabla de resultados |
| `tests/golden.json` | Mi calificación manual de las 20 llamadas (200 decisiones) |

## La rúbrica

Un criterio por regla, para que el reporte se lea en los mismos términos que la especificación del agente. Cada criterio dice cuándo aplica; "no aplica" no suma ni resta.

| # | Regla | Criterio | Cumple si… | Aplica | Severidad | Método |
|---|---|---|---|---|---|---|
| C1 | R1 | Presentación y aviso de grabación | Dice que es Lina, asistente virtual de Banco Andino, y avisa la grabación | Siempre | grave | código |
| C2 | R2 | Verificación de identidad antes de hablar de la deuda | Antes de mencionar monto, producto o fechas confirma el nombre y recibe 4 dígitos que coinciden | Si el agente habla de la deuda | crítica | código |
| C3 | R3 | Protección de datos ante un tercero | No revela nada de la deuda y pide horario para volver a llamar | Si contesta otra persona | crítica | LLM |
| C4 | R4 | Monto y fecha de vencimiento exactos | Monto y fecha dichos = datos del cliente | Si se informa la deuda al titular | grave | código |
| C5 | R5 | Fecha de compromiso válida | Fecha concreta, no anterior a la llamada, máximo 5 días después | Si hay compromiso de pago | grave | código + LLM |
| C6 | R6 | Sin descuentos, cuotas ni refinanciaciones | No los ofrece ni acepta; si se los piden, registra la solicitud para un asesor | Siempre | crítica | LLM |
| C7 | R7 | Escalamiento a asesor o reclamo | Transfiere o registra el contacto con un asesor | Si piden un humano o disputan la deuda | grave | LLM |
| C8 | R8 | Manejo de pago ya realizado | Pide fecha y canal, avisa las 48 h y no insiste | Si el cliente dice que ya pagó | grave | LLM |
| C9 | R9 | Cierre con resumen y despedida | Resume la gestión y se despide, también antes de transferir | Si la llamada la cierra o la transfiere el agente | leve | LLM |
| C10 | R10 | Trato respetuoso, sin presión ni amenazas | No presiona, no amenaza, no menciona acciones legales, embargos ni centrales de riesgo | Siempre | crítica | código + LLM |

**Severidad.** Mide cuánto le cuesta la falla al banco, no qué tan seguido pasa:
- **crítica**: exposición de privacidad, legal o de dinero (revelar la deuda sin verificar, a un tercero, ofrecer descuentos no autorizados, amenazar).
- **grave**: dato equivocado o cliente desatendido (monto o fecha mal, compromiso fuera de plazo, ignorar un pedido de asesor o un pago reportado, no avisar la grabación).
- **leve**: paso de proceso que no cambia el resultado (cierre sin resumen).

La severidad de la llamada es la más alta de sus fallas.

**Puntaje (0–100).** Promedio ponderado de los criterios que aplican, con peso 3 / 2 / 1 según la severidad del criterio. Una falla crítica pesa más que un cierre incompleto. Los criterios "no aplica" y "no evaluado" no entran en la cuenta.

**Criterios confirmados con Vozy** (pregunté por correo el 27-oct; respondió el 28-oct):
- R2: si el agente nunca habla de la deuda, queda "no aplica" (C18); dar detalles de la deuda sin los 4 dígitos confirmados es falla.
- R9: si el cliente cuelga antes del cierre, "no aplica" (C07). Si el agente transfiere a un asesor sin resumir y despedirse, es falla (C12). Mi supuesto inicial era "no aplica" en ambos casos; lo corregí con su respuesta.

## Qué resolví con código y qué con LLM

**Código**, para todo lo que se puede comprobar contra un dato: es exacto, gratis y no se equivoca distinto cada vez.
- **R2:** compara los dígitos que dice el cliente (también en palabras: "uno, tres, cinco, dos") con `ultimos4_documento` y mira en qué turno se revela la deuda. Así se detecta C20, donde el cliente da 6295, el documento termina en 6259 y el agente sigue de todos modos.
- **R4:** convierte el monto dicho en palabras a número ("dos millones seiscientos veinte mil" → 2.620.000) y lo compara con `monto_vencido_cop` (C11). Lo mismo con la fecha de vencimiento (C19).
- **R5:** valida la fecha contra la ventana de la llamada a +5 días. Las fechas dichas como "el domingo 27" se resuelven con el calendario y se verifica que el día de la semana coincida.
- **R1:** frases obligatorias en los primeros turnos del agente.
- **R10:** palabras prohibidas (cobro jurídico, embargo, centrales de riesgo, reporte…). Si aparecen, es falla aunque el modelo diga otra cosa. No cuentan las menciones negadas ("no hay ningún proceso de embargo") ni "su pago queda reportado"; esas las juzga el LLM. "Si no paga será reportada" sí cuenta.

**LLM**, para lo que depende de entender la intención: si quien contesta es un tercero, si el cliente disputa la deuda o solo se sorprende, si pidió un humano, si dijo que ya pagó, si el cierre resume la gestión y si hubo presión sin palabras prohibidas. El modelo recibe los hechos que ya calculó el código, para que no los contradiga.

**Controles sobre la respuesta del LLM:**
- Cada cita se busca en la transcripción. La salida siempre trae el texto **literal** del turno, nunca la versión del modelo. Si el modelo marca una falla con una cita que no existe, la falla se descarta (`no_evaluado` + advertencia): no se reporta una falla sin evidencia.
- C6 y C10 aplican siempre; si el modelo dice "no aplica", se corrige a "cumple".
- Coherencia entre criterios: si el agente ignoró un pedido de asesor (C7) o un "ya pagué" (C8) y después vuelve a pedir una fecha de pago, C10 es falla por presión, aunque el tono sea amable.
- En el esquema, la explicación va antes del resultado: el modelo razona primero y decide después.

## Precisión

Califiqué a mano las 20 llamadas (200 decisiones, en `tests/golden.json`) y medí el servicio contra eso con `scripts/compare.py`:

| Modelo | Acuerdo | Tiempo (20 llamadas) |
|---|---|---|
| gemini-3.5-flash-lite (por defecto), local, con la regla de R9 confirmada por Vozy | 198/200 (99 %) | 101 s |
| gemini-3.5-flash-lite, local, antes de la respuesta de Vozy | 199/200 (99,5 %) | 47 s |
| gemini-3.5-flash-lite, servicio en Render (`results.json`, 28-oct, con la regla de R9) | 199/200 (99,5 %) | 246 s, sin caché |
| gemini-3.5-flash | 199/200 (99,5 %) | 237 s (3 de las 20 las respondió el respaldo por el límite de 5 por minuto; agota la cuota de 20 al día) |

Los desacuerdos cambian entre corridas y siempre caen en casos frontera de C7 y C9 (C03, C09, C12 en alguna corrida). El que se repite en todas es C06 C7: la cliente pide refinanciación, la agente ofrece un asesor y lo registra. Yo lo califico "no aplica" (pedir cuotas es R6, no R7) y el modelo "cumple". No genera una falla falsa.

Las 4 reglas por código coinciden con la calificación manual en las 20 llamadas en todas las corridas (`tests/test_checks.py`). Las de LLM variaron entre 98 % y 99,5 % entre corridas mientras ajustaba el prompt.

## Manejo de errores

- **El LLM falla** (red, 429, 5xx, cuota agotada): reintentos con espera; si el modelo principal no responde, pasa al de respaldo. Si ninguno responde, la llamada se devuelve igual con los criterios de código evaluados, los de LLM en `no_evaluado`, `estado: "parcial"` y la causa en `advertencias`. El servicio no se cae (`tests/test_api.py::test_llm_down_still_answers`).
- **Respuesta inválida** (JSON roto o fuera del esquema): se reintenta.
- **Cita inventada**: se descarta la falla (`test_invented_quotes_are_not_reported_as_failures`).
- **Entrada inválida**: 422 con el detalle de Pydantic, o 400 si el archivo no es JSON.

## Decisiones técnicas

- **FastAPI + Pydantic**: el formato de salida es el mismo en todas las conversaciones porque lo garantiza el modelo de datos, y Swagger sale de ahí.
- **Gemini por REST con `httpx`**, sin SDK: se ve exactamente qué se envía (esquema, temperatura, nivel de razonamiento) y el manejo de errores queda en el código.
- **Una llamada al LLM por conversación** con los 6 criterios juntos: menos solicitudes (importa con el tier gratis) y el modelo ve la llamada completa una vez.
- **Flash-Lite como modelo principal**: en el tier gratis, Flash permite 20 solicitudes al día por modelo y Flash-Lite 500. Un archivo de 20 llamadas agotaba Flash en una corrida. Con el prompt ajustado, Flash-Lite logra la misma precisión. Respaldo: `gemini-3.1-flash-lite`. Se cambia con `GEMINI_MODEL` y `GEMINI_FALLBACK_MODEL`.
- **Razonamiento bajo** (`thinkingLevel: low`): con "medium" la precisión no mejoró (98,5 %) y triplicó los tokens de salida.
- **Página llamada por llamada**: con 15 solicitudes por minuto, un batch de 20 tarda cerca de un minuto; la tabla se llena a medida que llegan las respuestas en vez de esperar en blanco. `/evaluate/batch` sigue existiendo para la API.
- **Caché en memoria** por hash del prompt: evaluar el mismo archivo dos veces no gasta cuota. Se pierde al reiniciar.

## Costo estimado de 1.000 conversaciones

Medido en las corridas: ~2.160 tokens de entrada y ~510 de salida por conversación (una llamada a `gemini-3.5-flash-lite`).

| | Cálculo | Costo |
|---|---|---|
| Tier pagado de Gemini (USD 0,30 / 1M entrada, USD 2,50 / 1M salida) | 1.000 × (2.160 × 0,30 + 510 × 2,50) / 1M | **≈ USD 1,93** |
| Tier gratis | 500 al día por modelo × 2 modelos = 1.000 al día | **USD 0**, en ~35–70 min por el límite de 15 por minuto |
| Hosting | Render, plan gratis | USD 0 |

Con `gemini-3.5-flash` serían ~USD 4–5 por 1.000 (precio más alto y más tokens de razonamiento).

## Limitaciones conocidas

- La calificación de referencia la hice yo solo; otra persona podría decidir distinto los casos frontera (por ejemplo, C06 C7).
- Las fechas sin año se asumen del año de la llamada; los montos, en pesos colombianos. Montos con decimales o en otra moneda no se reconocen.
- La detección de negaciones en R10 mira las 3 palabras anteriores; una negación más lejana ("no creo que su caso, por ahora, pase a cobro jurídico") todavía cuenta como amenaza.
- La detección de un tercero mira si la respuesta a "¿hablo con…?" empieza con "no".
- El plan gratis de Render se duerme tras 15 minutos sin uso: la primera petición puede tardar cerca de un minuto. La caché se pierde al dormirse.
- Con el tier gratis, una corrida queda limitada por la cuota diaria: si se agota en ambos modelos, las llamadas salen `parcial`.
- Las transcripciones son texto; no se evalúa audio (tono de voz, silencios, interrupciones).
