# Bot de Encuestas SIAAUTT (UTT)

Contesta automáticamente las encuestas de satisfacción/calidad del portal
`https://uttorreon.mx` usando **Playwright** en modo headless (sin abrir ventana).

El bot simula el comportamiento de una persona real (tiempos de lectura,
selección y escritura aleatorios) para que el llenado no se vea automatizado, y
usa un conjunto de **respuestas fijas ya definidas** para las preguntas
importantes.

---

## ¿Qué hace?

1. Inicia sesión en el portal del alumno (reintenta si el servidor aplica
   rate-limit).
2. Entra al módulo de **Encuestas**.
3. Detecta las encuestas en estado **Activada** (pendientes).
4. Recorre cada encuesta sección por sección respondiendo todas las preguntas.
5. Envía la encuesta y confirma con el folio que devuelve el servidor.
6. Repite con cada encuesta pendiente y avisa al terminar.

Para "usar cualquier cuenta" **solo se cambian dos datos** en el archivo `.env`
(`EMAIL` y `PASSWORD`); la matrícula se detecta sola.

---

## Compatibilidad

**Sí** funciona en Windows, macOS y Linux. Solo requiere Python 3.10 o superior.

| Sistema | Compatible | Nota |
|---------|------------|------|
| Linux   | ✅ | Aviso vía `notify-send` (opcional) |
| macOS   | ✅ | Aviso vía `osascript` (incluido) |
| Windows | ✅ | Aviso vía PowerShell (opcional) |

La campana de la terminal suena en todos los sistemas aunque no haya
notificación de escritorio.

---

## Requisitos

- Python 3.10+ (probado con 3.12)
- Conexión a internet

---

## Instalación (una sola vez)

**Linux / macOS:**
```bash
python3 -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

**Windows (PowerShell):**
```powershell
py -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

---

## Configuración (`.env`)

Copia la plantilla y edita tus credenciales:

```bash
cp .env.example .env          # Windows: copy .env.example .env
```

Todas las variables disponibles:

| Variable | Obligatoria | Por defecto | Qué hace |
|----------|-------------|-------------|----------|
| `EMAIL` | Sí | — | Correo **o matrícula** con que se entra al portal (acepta ambos). |
| `PASSWORD` | Sí | — | Contraseña de la cuenta. |
| `BASE_URL` | No | `https://uttorreon.mx` | URL base del portal. |
| `MATRICULA` | No | *(autodetectada)* | Matrícula del alumno. Si no se pone, el bot la lee sola al iniciar sesión. |
| `MODO_HUMANO` | No | `true` | `true` = tiempos aleatorios de persona real; `false` = lo más rápido posible. |
| `VELOCIDAD` | No | `0.6` | Multiplicador del modo humano: `0.3` rápido (~1 min) · `0.6` moderado (~2-4 min) · `1.0` realista (~5-8 min). |
| `FILTRO_SINO` | No | `si` | Respuesta general a preguntas de uso con Sí/No **no cubiertas** por las respuestas fijas: `si` o `no`. |
| `CHECKBOX` | No | `una` | Casillas de selección múltiple: `una` marca una sola, `todas` las marca todas. |
| `CATEGORICA` | No | `primera` | Selects sin valoración ("¿Qué centro…?"): `primera` o `aleatoria`. |
| `TEXTO_ABIERTO` | No | `ninguno` | Texto que se escribe en los comentarios abiertos. |
| `INTERNET_KW` | No | `internet,wifi,wi-fi` | Palabras que hacen que una pregunta se responda con la **segunda mejor** opción (nunca negativa). |

> Nota: para preguntas concretas el bot usa las **respuestas fijas** de la
> siguiente sección, que tienen **prioridad** sobre `FILTRO_SINO`, `CATEGORICA`,
> etc. Esas variables solo aplican a preguntas que no están en la lista fija.

---

## Respuestas automáticas

### Regla general (para preguntas no fijadas)

- **Valoración** (Excelente/Muy bueno, escalas 5→1, frecuencia Siempre→Nunca):
  siempre la opción **más positiva**.
- **Internet** (`INTERNET_KW`): **segunda mejor** opción (nunca negativa).
- **Casillas múltiples**: según `CHECKBOX`.
- **Selects sin valoración**: según `CATEGORICA`.
- **Comentarios abiertos**: se escribe `TEXTO_ABIERTO` (por defecto `ninguno`).

### Respuestas fijas (configuración definitiva)

Estas respuestas están en el código (`RESPUESTAS_FIJAS`, dentro de
`bot_encuestas.py`) y se aplican **a cualquier cuenta**. Tienen prioridad sobre
la regla general:

| Encuesta | Pregunta | Respuesta |
|----------|----------|-----------|
| **Beca Académica** | El horario de atención, lo calificas como: | `4.-Bueno` |
| **Beca Académica** | El trato brindado por el personal a cargo lo consideras: | `5.-Muy Bueno` |
| **NVO-01** | La información que recibí resolvió mis dudas: | `No Aplica` |
| **NVO-01** | El personal que participó en el proceso de inscripción me atendió amablemente. | `No Aplica` |
| **NVO-01** | Me avisaron oportunamente de los periodos y horarios de inscripción | `No Aplica` |
| **NVO-01** | El proceso de inscripción es ágil, sencillo: | `No Aplica` |
| **NVO-01** | El horario de atención, lo calificas como: | `4.-Bueno` |
| **NVO-01** | En cuanto al tiempo para la realización de trámite del área, lo calificas como: | `4.-Bueno` |
| **F-GC-011 Rev G** | ¿Con qué frecuencia utilizas el servicio de cafetería? | `Ocasionalmente` |
| **F-GC-011 Rev G** | El tiempo de atención es: | `Bueno` |
| **F-GC-011 Rev G** | La higiene con que sirven los alimentos es: | `No Aplica` |
| **F-GC-011 Rev G** | La presentación del platillo lo consideras que es: | `No Aplica` |
| **F-GC-011 Rev G** | La limpieza de las instalaciones es: | `Bueno` |
| **F-GC-011 Rev G** | ¿Has comprado en el centro de copiado, ubicado en el laboratorio pesado 2? | `Si` |
| **F-GC-011 Rev G** | La rapidez en la atención es: | `Regular` |
| **F-GC-011 Rev G** | El horario de atención del servicio de centro de copiado lo consideras: | `Bueno` |
| **F-GC-009 M-ASESORIA** | ¿Recibiste asesoría académica? | `No` |
| **F-GC-009 BIBL** | El servicio con relación al material bibliográfico disponible, consideras que es: | `4.-Bueno` |
| **F-GC-009 BIBL** | La inducción recibida para el uso de los servicios de biblioteca fue: | `3.-Regular` |
| **F-GC-009 BIBL** | La atención brindada por el personal a cargo la consideras que es: | `4.-Bueno` |
| **F-GC-009 BIBL** | La eficiencia con relación al préstamo de libros es: | `4.-Bueno` |
| **F-GC-009 BIBL** | El sistema para la localización de libros lo consideras: | `3.-Regular` |
| **F-GC-009 BIBL** | El centro de cómputo ubicado en Biblioteca, para consultas académicas, lo considero: | `3.-Regular` |
| **F-GC-009 TI2** | Seleccione el centro de computo a evaluar | `MAC` |
| **F-GC-009 TI2** | El software (office) del equipo de cómputo instalado consideras que es : | `4.-Bueno` |
| **F-GC-009 TI2** | La funcionalidad del equipo de cómputo utilizado, de acuerdo a tú experiencia es: | `4.-Bueno` |

> La interpretación de los números en escalas de texto es: **4 = Bueno**,
> **3 = Regular** (igual que en las escalas `5.-Muy Bueno / 4.-Bueno /
> 3.-Regular`).

Para cambiar una respuesta fija, edita el diccionario `RESPUESTAS_FIJAS` al
inicio de `bot_encuestas.py`; para agregar una pregunta nueva, añade una línea
`"texto de la pregunta": "opción a marcar"` dentro de la encuesta correspondiente
(el texto se compara ignorando mayúsculas, acentos y signos de puntuación).

---

## Uso

Con el entorno virtual activado:

```bash
python bot_encuestas.py
```

### Modos de ejecución

| Comando | Qué hace | ¿Envía? |
|---------|----------|---------|
| `python bot_encuestas.py` | Flujo real completo | **Sí** |
| `python bot_encuestas.py --simular` | Recorre todo e imprime pregunta/respuesta/payload, pero no envía | No |
| `python bot_encuestas.py --dump` | Vuelca todas las preguntas y opciones a `dump_encuestas.json` | No |

También se pueden activar por variable de entorno: `SIMULAR=true` o `DUMP=true`.

**Modo simulación** (recomendado para probar antes de enviar): como no completa
ninguna encuesta, puedes repetirlo tantas veces como quieras sin gastar los
intentos.

### Verificar cuántas encuestas quedan

```bash
python verificar.py
```

Muestra el total de encuestas **pendientes** (Activadas). Si dice
`No se encontraron registros`, no queda ninguna. También guarda una captura
`verificacion.png` como comprobante.

---

## Usar otra cuenta

Solo cambia en `.env`:

```dotenv
EMAIL=otra_matricula_o_correo
PASSWORD=otra_contraseña
```

La matrícula se ajusta sola al iniciar sesión. (Opcional: puedes fijarla con
`MATRICULA=...`.)

---

## Avisos al terminar

- Todo se imprime en la consola con la hora.
- Al completar cada encuesta y al final suena la campana de la terminal.
- Además muestra una notificación de escritorio si está disponible:
  - **Linux**: `sudo apt install libnotify-bin`
  - **macOS**: incluido
  - **Windows**: opcional (usa BurntToast de PowerShell)

---

## Estructura del proyecto

```
bot_encuestas.py   Script principal (contesta y finaliza encuestas)
verificar.py       Consulta cuántas encuestas quedan pendientes
requirements.txt   Dependencias de Python
.env.example       Plantilla de configuración
.env               Tus credenciales (no se sube a git)
dump_encuestas.json Salida del modo --dump (preguntas y opciones)
debug/             Scripts y capturas usados durante el desarrollo
```

---

## Problemas frecuentes

- **El login falla varias veces:** suele ser rate-limit del servidor. Espera
  ~2 minutos y vuelve a ejecutar (el bot ya reintenta 3 veces solo).
- **Quiero ver el navegador** (para depurar): cambia `headless=True` por
  `headless=False` en `bot_encuestas.py`.
- **No queda ninguna encuesta:** el portal solo muestra encuestas cuando las
  publican (las de evaluación docente suelen salir cada ~4 meses).
- **Playwright no encuentra Chromium:** ejecuta `playwright install chromium`.

---

## Nota

Las encuestas son personales. Este bot existe para automatizar el llenado con
**tus propias respuestas** de la forma más cómoda posible.
