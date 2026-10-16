"""
Bot de Automatizacion de Encuestas - SIAAUTT (UTT)
===================================================
Automatiza el llenado de encuestas de calidad en https://uttorreon.mx
usando Playwright en modo headless.

Uso:
    python bot_encuestas.py
"""

import os
import platform
import random
import re
import subprocess
import sys
import time
import unicodedata

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------

load_dotenv()

SIMULAR = "--simular" in sys.argv or os.getenv("SIMULAR", "").lower() in ("1", "true")
DUMP = "--dump" in sys.argv or os.getenv("DUMP", "").lower() in ("1", "true")
# En modo dump o simulacion NUNCA se envia la encuesta
NO_ENVIAR = SIMULAR or DUMP

EMAIL = os.getenv("EMAIL")
PASSWORD = os.getenv("PASSWORD")
BASE_URL = os.getenv("BASE_URL", "https://uttorreon.mx").rstrip("/")
MATRICULA = os.getenv("MATRICULA", "").strip()

URL_ENCUESTAS = (
    f"{BASE_URL}/{MATRICULA}" if MATRICULA else BASE_URL
) + "/AreaAlumno/Encuestas"

# Respuesta para preguntas abiertas (text / textarea). Configurable por .env
RESPUESTA_ABIERTA = os.getenv("TEXTO_ABIERTO", "ninguno")

# Prioridad de respuestas positivas para selects/radios (orden descendente)
RESPUESTAS_POSITIVAS = [
    "excelente",
    "muy bueno",
    "muy buena",
    "bueno",
    "buena",
    "siempre",
    "todos los dias",
    "totalmente de acuerdo",
    "completamente satisfecho",
    "muy satisfecho",
    "satisfecho",
    "muy bien",
    "bien",
    "si",
    "de acuerdo",
]

# Palabras que indican una respuesta negativa (nunca elegir como "variacion")
NEGATIVAS = (
    "nunca", "jamas", "jamás", "nada", "malo", "mala", "mal",
    "deficiente", "insuficiente", "regular", "poco",
    "pesimo", "pésimo", "insatisfecho",
)


def _es_negativa(opt: dict) -> bool:
    """True si la opcion tiene connotacion negativa (No, Nunca, Malo...)."""
    texto = f"{opt.get('value', '')} {opt.get('text', '')}".lower()
    if re.search(r"\bno\b", texto) or re.search(r"\bnunca\b", texto):
        return True
    return any(k in texto for k in NEGATIVAS)


# Encuesta que se esta respondiendo ahora mismo (codigo/nombre del encabezado)
ENCUESTA_ACTUAL = {"codigo": "", "nombre": ""}


def _norm_q(s: str) -> str:
    """Normaliza texto de pregunta/opcion para comparar (sin acentos ni signos)."""
    s = s or ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower().strip().strip("¿?¡!:. ")
    return re.sub(r"\s+", " ", s)


# ---------------------------------------------------------------------------
# RESPUESTAS FIJAS por encuesta y pregunta (configuracion definitiva)
# ---------------------------------------------------------------------------
# Clave: codigo de encuesta -> { texto de la pregunta : opcion a marcar }
# Lo que NO este aqui usa el clasificador general (la opcion mas positiva).
RESPUESTAS_FIJAS = {
    "Beca Académica": {
        "El horario de atención, lo calificas como:": "4.-Bueno",
        "El trato brindado por el personal a cargo lo consideras:": "5.-Muy Bueno",
    },
    "NVO-01": {
        "La información que recibí resolvió mis dudas:": "No Aplica",
        "El personal que participo en el proceso de inscripción me atendió amablemente.": "No Aplica",
        "Me avisaron oportunamente de los periodos y horarios de inscripción": "No Aplica",
        "El proceso de inscripción es ágil, sencillo:": "No Aplica",
        "El horario de atención, lo calificas como:": "4.-Bueno",
        "En cuanto al tiempo para la realización de trámite del área, lo calificas como:": "4.-Bueno",
    },
    "F-GC-011 Rev G": {
        "¿Con qué frecuencia utilizas el servicio de cafetería?": "Ocasionalmente",
        "El tiempo de atención es:": "Bueno",
        "La higiene con que sirven los alimentos es:": "No Aplica",
        "La presentación del platillo lo consiferas que es:": "No Aplica",
        "La limpieza de las instaciones es:": "Bueno",
        "¿Has comprado en el centro de copiado, ubicado en el laboratorio pesado 2?": "Si",
        "La rapidez en la atención es:": "Regular",
        "EL horario de atención del servicio de centro de copiado lo consideras:": "Bueno",
    },
    "F-GC-009 M-ASESORIA": {
        "¿Recibiste asesoría académica?": "No",
    },
    "F-GC-009 BIBL": {
        "El servicio con relación al material bibliográfico disponible, consideras que es:": "4.-Bueno",
        "La inducción recibida para el uso de los servicios de biblioteca fue:": "3.-Regular",
        "La atención brindada por el personal a cargo la consideras que es:": "4.-Bueno",
        "La eficiencia con relación al préstamo de libros es:": "4.-Bueno",
        "El sistema para la localización de libros lo consideras:": "3.-Regular",
        "El centro de cómputo ubicado en Biblioteca, para consultas académicas, lo considero:": "3.-Regular",
    },
    "F-GC-009 TI2": {
        "Seleccione el centro de computo a evaluar": "MAC",
        "El software (office) del equipo de cómputo instalado consideras que es :": "4.-Bueno",
        "La funcionalidad del equipo de cómputo utilizado, de acuerdo a tú experiencia es:": "4.-Bueno",
    },
}

# Version normalizada para busqueda rapida
_FIJAS_NORM = {
    _norm_q(cod): {_norm_q(q): v for q, v in qmap.items()}
    for cod, qmap in RESPUESTAS_FIJAS.items()
}


def _emparejar_opcion(valor: str, opciones: list) -> dict | None:
    """Busca en las opciones la que corresponde a 'valor' (texto/numer'/substring)."""
    objetivo = _norm_q(valor)
    if not objetivo:
        return None
    for o in opciones:
        if _norm_q(o.get("text", "")) == objetivo or _norm_q(o.get("value", "")) == objetivo:
            return o
    # Por numero inicial: "4" -> "4.-Bueno", "3" -> "3.-Regular"
    m = re.match(r"^(\d)", valor.strip())
    if m:
        for o in opciones:
            if _norm_q(o.get("text", "")).startswith(m.group(1)):
                return o
    # Por coincidencia parcial (ej. "no aplica")
    for o in opciones:
        if objetivo and objetivo in _norm_q(o.get("text", "")):
            return o
    return None


def respuesta_fija(texto: str) -> str | None:
    """Devuelve la opcion fija para la pregunta (segun la encuesta actual), o None."""
    cod = _norm_q(ENCUESTA_ACTUAL.get("codigo", ""))
    q = _norm_q(texto)
    mapa = _FIJAS_NORM.get(cod)
    if mapa and q in mapa:
        return mapa[q]
    return None


# ---------------------------------------------------------------------------
# Clasificacion de preguntas (configurable)
# ---------------------------------------------------------------------------
# Preguntas que hablan de internet -> segunda mejor opcion (no la maxima)
INTERNET_KW = [k.strip().lower() for k in
               os.getenv("INTERNET_KW", "internet,wifi,wi-fi").split(",") if k.strip()]

# Verbos que indican pregunta de "uso/filtro" (¿usas...?, ¿recibiste...?)
PALABRAS_USO = [
    "usas", "has usado", "utilizas", "has utilizado", "recibiste", "asistes",
    "acudes", "cuentas con", "has comprado", "compraste", "has solicitado",
    "solicitaste", "haces uso", "has hecho uso", "consumes", "has consumido",
    "participaste", "has participado",
]

# Que contestar en preguntas de uso/filtro con opciones Si/No
FILTRO_SINO = os.getenv("FILTRO_SINO", "si").strip().lower()      # si | no

# Casillas multiples: marcar una o todas
CHECKBOX_MODO = os.getenv("CHECKBOX", "una").strip().lower()      # una | todas

# Selects sin valoracion (¿que centro...?): primera o aleatoria
CATEGORICA_MODO = os.getenv("CATEGORICA", "primera").strip().lower()  # primera | aleatoria

# Delay entre acciones (ms)
DELAY_CORTO = 50
DELAY_MEDIO = 200
DELAY_LARGO = 400

# ---------------------------------------------------------------------------
# Modo humano: tiempos aleatorios para simular a una persona real
# ---------------------------------------------------------------------------
MODO_HUMANO = os.getenv("MODO_HUMANO", "true").strip().lower() not in ("0", "false", "no")
VELOCIDAD = float(os.getenv("VELOCIDAD", "0.6"))  # 1.0 = realista, 0.6 = moderado

# Rangos base (segundos) ANTES de aplicar VELOCIDAD
H_ABRIR_ENCUESTA = (2.0, 4.5)     # al abrir una encuesta (leer intro)
H_LECTURA = (1.2, 2.8)            # leer cada pregunta
H_ANTES_CLICK = (0.4, 1.2)        # pensar antes de marcar/ seleccionar
H_ENTRE_PREGUNTAS = (0.3, 0.9)    # pausa tras responder
H_ENTRE_PASOS = (1.2, 2.8)        # entre secciones/pasos del wizard
H_ENTRE_ENCUESTAS = (4.0, 10.0)   # entre una encuesta y la siguiente
H_ANTES_FINALIZAR = (2.0, 5.0)    # revisar antes de enviar
H_TECLEO = (0.05, 0.14)           # ms por caracter al teclear texto abierto

# Reintentos de login (por rate-limit del servidor)
LOGIN_REINTENTOS = 3
LOGIN_ESPERA = 60  # segundos entre reintentos

# Argumentos de Chromium para entornos headless/servidor
BROWSER_ARGS = [
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-software-rasterizer",
]


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    """Imprime un mensaje con timestamp."""
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def rango_humano(rng: tuple[float, float]) -> float:
    """Devuelve un valor aleatorio del rango (segundos), escalado por VELOCIDAD."""
    if not MODO_HUMANO:
        return 0.0
    lo, hi = rng
    return random.uniform(lo, hi) * VELOCIDAD


def pausa(page, rng: tuple[float, float]) -> None:
    """Espera un tiempo aleatorio humano dentro del rango dado (segundos)."""
    seg = rango_humano(rng)
    if seg > 0:
        page.wait_for_timeout(int(seg * 1000))


def teclear(elemento, texto: str) -> None:
    """Escribe texto en un elemento simulando tecleo humano."""
    if not MODO_HUMANO:
        elemento.fill(texto)
        return
    elemento.click()
    for ch in texto:
        elemento.type(ch, delay=int(random.uniform(*H_TECLEO) * 1000))


def notificar(msg: str) -> None:
    """
    Envia una notificacion visible: campana + notificacion de escritorio
    (segun el sistema operativo) + log.
    """
    log(f"  >>> NOTIFICACION: {msg}")
    try:
        sistema = platform.system()
        if sistema == "Linux":
            subprocess.run(["notify-send", "-u", "normal", "Bot Encuestas SIAAUTT", msg],
                           timeout=5, check=False)
        elif sistema == "Darwin":  # macOS
            subprocess.run(["osascript", "-e",
                            f'display notification "{msg}" with title "Bot Encuestas SIAAUTT"'],
                           timeout=5, check=False)
        elif sistema == "Windows":
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "New-BurntToastNotification -Text 'Bot Encuestas SIAAUTT', '" + msg + "'"],
                           timeout=5, check=False)
    except Exception:
        pass
    try:
        print("\a", end="", flush=True)
    except Exception:
        pass


def _opciones_ordenadas(opciones: list[dict]) -> list[dict]:
    """Devuelve las opciones ordenadas de mas positiva a menos positiva."""
    validas = [o for o in opciones if str(o.get("value", "")).strip()]
    if not validas:
        return []

    ranking: list[dict] = []
    restantes = list(validas)

    # 1. Palabras clave positivas, en orden de prioridad
    for keyword in RESPUESTAS_POSITIVAS:
        for opt in list(restantes):
            txt = (opt["value"] + " " + opt.get("text", "")).lower()
            if keyword in txt:
                ranking.append(opt)
                restantes.remove(opt)

    # 2. Restantes: valores numericos de mayor a menor
    numericos = []
    no_numericos = []
    for opt in restantes:
        try:
            numericos.append((float(opt["value"]), opt))
        except (ValueError, TypeError):
            no_numericos.append(opt)
    numericos.sort(key=lambda x: x[0], reverse=True)
    ranking.extend(o for _, o in numericos)
    ranking.extend(no_numericos)

    return ranking


def _opciones_ordenadas(opciones: list[dict]) -> list[dict]:
    """Devuelve las opciones ordenadas de mas positiva a menos positiva."""
    validas = [o for o in opciones if str(o.get("value", "")).strip()]
    if not validas:
        return []

    ranking: list[dict] = []
    restantes = list(validas)

    # 1. Palabras clave positivas, en orden de prioridad
    for keyword in RESPUESTAS_POSITIVAS:
        for opt in list(restantes):
            txt = (opt["value"] + " " + opt.get("text", "")).lower()
            if keyword in txt:
                ranking.append(opt)
                restantes.remove(opt)

    # 2. Restantes: valores numericos de mayor a menor
    numericos = []
    no_numericos = []
    for opt in restantes:
        try:
            numericos.append((float(opt["value"]), opt))
        except (ValueError, TypeError):
            no_numericos.append(opt)
    numericos.sort(key=lambda x: x[0], reverse=True)
    ranking.extend(o for _, o in numericos)
    ranking.extend(no_numericos)

    return ranking


def _norm(o) -> str:
    return f"{o.get('value', '')} {o.get('text', '')}".strip().lower()


def _es_salteo(o: dict) -> bool:
    """Opciones que podrian saltar secciones; evitar seleccionarlas por seguridad."""
    txt = (o.get("text", "") or "").strip().lower()
    if txt == "no" or txt == "nunca":
        return True
    return False


def _es_uso_filtro(texto: str) -> bool:
    t = (texto or "").lower()
    return any(k in t for k in PALABRAS_USO)


def _es_pregunta_internet(texto: str) -> bool:
    t = (texto or "").lower()
    return any(k in t for k in INTERNET_KW)


def _es_binaria(opciones: list[dict]) -> bool:
    vals = set()
    for o in opciones:
        txt = (o.get("text") or "").strip().lower()
        if txt:
            vals.add(txt)
    if len(vals) < 2:
        return False
    permitidas = {"si", "sí", "no", "no aplica", "no aplica.", "n/a", "no lo se", "no lo sé", "no sé"}
    return vals <= permitidas and (("si" in vals) or ("sí" in vals)) and ("no" in vals)


def _es_valoracion(opciones: list[dict]) -> bool:
    for o in opciones:
        t = _norm(o)
        if any(k in t for k in RESPUESTAS_POSITIVAS):
            return True
        try:
            float(o["value"])
            return True
        except Exception:
            pass
    return False


def _elegir_binaria(opciones: list[dict]):
    si_opt = None; no_opt = None
    for o in opciones:
        txt = (o.get("text") or o.get("value") or "").strip().lower()
        if txt in ("si", "sí") and si_opt is None:
            si_opt = o
        if txt == "no" and no_opt is None:
            no_opt = o
    if FILTRO_SINO == "no" and no_opt:
        return no_opt
    if si_opt:
        return si_opt
    return opciones[0] if opciones else None


def elegir_respuesta(texto: str, opciones: list[dict]) -> dict | None:
    # 0. Respuesta fija configurada para esta encuesta/pregunta (tiene prioridad)
    fija = respuesta_fija(texto)
    if fija:
        opt = _emparejar_opcion(fija, opciones)
        if opt:
            return opt
    ranking = _opciones_ordenadas(opciones)
    if not ranking:
        return None
    # 1. Internet: segunda mejor (no negativa), si existe
    if _es_pregunta_internet(texto) and len(ranking) >= 2:
        if not _es_negativa(ranking[1]):
            return ranking[1]
        return ranking[0]
    # 2. Uso/filtro binaria
    if _es_binaria(ranking) and _es_uso_filtro(texto):
        return _elegir_binaria(ranking)
    # 3. Valoracion
    if _es_valoracion(ranking):
        return ranking[0]
    # 4. Categorica
    if len(ranking) >= 2:
        if CATEGORICA_MODO == "aleatoria":
            return random.choice(ranking)
    return ranking[0]


# Mantener nombre compat para el resto
def elegir_opcion_positiva(opciones: list[dict], variar: bool = False) -> dict | None:
    return elegir_respuesta("", opciones)


# ---------------------------------------------------------------------------
# Funciones del bot
# ---------------------------------------------------------------------------

def _detectar_matricula(page) -> str | None:
    """Detecta la matricula del alumno a partir de los links del dashboard."""
    try:
        for a in page.query_selector_all("a"):
            href = a.get_attribute("href") or ""
            m = re.search(r"uttorreon\.mx/(\d{6,})/AreaAlumno/Encuestas", href)
            if m:
                return m.group(1)
        for a in page.query_selector_all("a"):
            href = a.get_attribute("href") or ""
            m = re.search(r"uttorreon\.mx/(\d{5,})/", href)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def hacer_login(page) -> bool:
    """Inicia sesion en la plataforma, con reintentos y deteccion de matricula."""
    global MATRICULA, URL_ENCUESTAS

    for intento in range(1, LOGIN_REINTENTOS + 1):
        log(f"Navegando a la pagina de login... (intento {intento}/{LOGIN_REINTENTOS})")
        try:
            page.goto(BASE_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(DELAY_MEDIO)

            # Ya hay sesion activa guardada
            if f"/{MATRICULA}/" in page.url:
                log("Ya hay sesion activa, saltando login.")
                return True

            log("Llenando formulario de login...")
            page.wait_for_selector("input#email", timeout=15000)
            page.fill("input#email", EMAIL)
            page.fill("input#password", PASSWORD)
            page.click('button[type="submit"]')
            log("Credenciales enviadas, esperando respuesta...")
            page.wait_for_load_state("networkidle", timeout=15000)
            page.wait_for_timeout(DELAY_LARGO)

            # Login fallido: seguimos en la pantalla de acceso
            if "login" in page.url.lower():
                log(f"Login fallido (intento {intento}). Reintentando en {LOGIN_ESPERA}s...")
                page.wait_for_timeout(LOGIN_ESPERA * 1000)
                continue

            log("Login exitoso!")

            # Detectar matricula real y actualizar la URL de encuestas
            detectada = _detectar_matricula(page)
            if detectada and detectada != MATRICULA:
                log(f"Matricula detectada automaticamente: {detectada}")
                MATRICULA = detectada
                URL_ENCUESTAS = f"{BASE_URL}/{MATRICULA}/AreaAlumno/Encuestas"
            return True

        except PlaywrightTimeout:
            log(f"Timeout en login (intento {intento}). Reintentando en {LOGIN_ESPERA}s...")
            page.wait_for_timeout(LOGIN_ESPERA * 1000)
        except Exception as e:
            log(f"Error en login (intento {intento}): {str(e)[:60]}")
            page.wait_for_timeout(LOGIN_ESPERA * 1000)

    log("ERROR: Login fallido tras varios intentos. Verifica tus credenciales en .env")
    return False


def navegar_a_encuestas(page) -> bool:
    """
    Navega al modulo de encuestas. Intenta dos rutas:
    1. Panel de notificaciones (boton rojo) si existe.
    2. Navegacion directa por URL.
    """
    log("Navegando al modulo de encuestas...")

    # Ruta 1: Buscar el tab rojo de encuestas en el panel de notificaciones
    try:
        tab_encuestas = page.query_selector('a[href="#encuestas"]')
        if tab_encuestas:
            log("Panel de notificaciones detectado, clickeando tab Encuestas...")
            tab_encuestas.click()
            page.wait_for_timeout(DELAY_MEDIO)

            # Buscar el boton "Ir a Encuestas pendientes"
            btn_ir = page.query_selector('a.btn-danger[href*="Encuestas"]')
            if btn_ir:
                btn_ir.click()
                page.wait_for_load_state("networkidle", timeout=15000)
                page.wait_for_timeout(DELAY_LARGO)
                log("Navegacion via panel de notificaciones exitosa.")
                return True
            else:
                log("Panel sin boton de encuestas pendientes, usando URL directa.")
        else:
            log("Sin panel de notificaciones, usando URL directa.")
    except Exception as e:
        log(f"Ruta panel fallo ({str(e)[:50]}), usando URL directa.")

    # Ruta 2: Navegacion directa por URL
    page.goto(URL_ENCUESTAS, wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle", timeout=15000)
    page.wait_for_timeout(DELAY_LARGO)

    log("Pagina de encuestas cargada.")
    return True


def obtener_encuestas_disponibles(page) -> int:
    """
    Cuenta cuantas encuestas con estatus 'Activada' hay en la tabla.
    Retorna el numero total de encuestas activadas.
    """
    page.wait_for_timeout(DELAY_MEDIO)

    # Esperar a que la tabla bootstrap-table cargue
    try:
        page.wait_for_selector("table tbody tr", timeout=10000)
    except PlaywrightTimeout:
        log("No se encontraron encuestas en la tabla.")
        return 0

    filas = page.query_selector_all("table tbody tr")
    activadas = 0
    for fila in filas:
        estatus = fila.query_selector('span.label-warning')
        if estatus and "activada" in estatus.inner_text().lower():
            activadas += 1

    return activadas


def seleccionar_encuesta(page, index: str) -> bool:
    """Selecciona la fila, espera que carguen las preguntas y activa el tab de contestacion."""
    try:
        selector = f'tr[data-index="{index}"]'
        if not page.query_selector(selector):
            log(f"  No se encontro fila para encuesta indice {index}")
            return False

        page.click(selector)
        log(f"  Fila indice {index} seleccionada.")
        page.wait_for_timeout(DELAY_LARGO)

        try:
            page.wait_for_selector(".WPregunta", state="attached", timeout=20000)
            log(f"  Preguntas de encuesta cargadas.")
        except PlaywrightTimeout:
            log(f"  ERROR: Las preguntas de la encuesta no cargaron.")
            return False

        # Activar el tab de contestacion para que las preguntas sean visibles
        tab_contestacion = page.query_selector("#anabContestacio")
        if tab_contestacion:
            tab_contestacion.click()
            page.wait_for_timeout(DELAY_MEDIO)
            log(f"  Tab de contestacion activado.")

        page.evaluate("""() => {
            if (typeof window.encuestac !== 'undefined' && window.encuestac)
                window.__savedEncuestac = window.encuestac;
            else if (typeof $ !== 'undefined')
                $('[class*=\"encuestac\"]').each(function() {
                    var d = $(this).data();
                    for (var k in d) if (d[k] && typeof d[k].Serialize === 'function') {
                        window.__savedEncuestac = d[k];
                        return false;
                    }
                });
        }""")
        return True

    except Exception as e:
        log(f"  ERROR al seleccionar encuesta: {e}")
        return False


DUMP_REGISTROS = []


def _set_encuesta_actual(page) -> None:
    """Lee el codigo/nombre de la encuesta abierta y lo guarda globalmente."""
    try:
        nom = page.inner_text("#NombreEncuesta").strip()
    except Exception:
        nom = ""
    try:
        cod = page.inner_text("#codigo").strip()
    except Exception:
        cod = ""
    ENCUESTA_ACTUAL["nombre"] = re.sub(r"^\s*nombre\s*:\s*", "", nom, flags=re.I)
    ENCUESTA_ACTUAL["codigo"] = re.sub(r"^\s*c[oó]digo\s*:\s*", "", cod, flags=re.I)


def _dump_registrar(tipo: str, texto: str, opciones: list, elegida: dict | None) -> None:
    """Guarda una pregunta con todas sus opciones para el volcado (--dump)."""
    DUMP_REGISTROS.append({
        "encuesta": ENCUESTA_ACTUAL.get("nombre", ""),
        "codigo": ENCUESTA_ACTUAL.get("codigo", ""),
        "tipo": tipo,
        "texto": texto.strip(),
        "opciones": [f"{o.get('text', '')}" for o in opciones],
        "elegida": (elegida or {}).get("text", ""),
    })


def guardar_dump(ruta: str = "dump_encuestas.json") -> None:
    """Escribe el volcado (deduplicado) en un JSON."""
    import json
    vistos = set()
    salida = []
    for r in DUMP_REGISTROS:
        clave = (r["encuesta"], r["texto"], r["tipo"])
        if clave in vistos:
            continue
        vistos.add(clave)
        salida.append(r)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)
    log(f"  Volcado guardado en {ruta} ({len(salida)} preguntas)")


def responder_preguntas_visibles(page) -> int:
    """Detecta y responde preguntas visibles en el step actual."""
    respondidas = 0
    preguntas = page.query_selector_all(".WPregunta")

    for pregunta in preguntas:
        if not pregunta.evaluate("el => el.offsetParent !== null"):
            continue

        elem_texto = pregunta.query_selector(".Pregunta")
        texto_pregunta = elem_texto.inner_text().strip() if elem_texto else "Sin texto"

        # Pausa de "leer" la pregunta (mas larga si hay mas texto)
        pausa(page, H_LECTURA)
        if len(texto_pregunta) > 80:
            pausa(page, (0.3, 0.8))

        # --- SELECT ---
        select = pregunta.query_selector("select.form-control")
        if select and select.is_visible():
            opciones_raw = select.query_selector_all("option")
            opciones = []
            for opt in opciones_raw:
                val = opt.get_attribute("value") or ""
                txt = opt.inner_text().strip()
                opciones.append({"value": val, "text": txt})

            elegida = elegir_respuesta(texto_pregunta, opciones)
            if DUMP:
                _dump_registrar("select", texto_pregunta, opciones, elegida)
            if elegida:
                pausa(page, H_ANTES_CLICK)
                select.select_option(value=elegida["value"])
                log(f"    [SELECT] {texto_pregunta[:50]}... -> {elegida['value']} ({elegida['text'][:40]})")
                respondidas += 1
                pausa(page, H_ENTRE_PREGUNTAS)
            continue

        # --- RADIO BUTTONS ---
        radios = pregunta.query_selector_all('input[type="radio"]')
        if radios and len(radios) > 0:
            opciones = []
            for radio in radios:
                val = radio.get_attribute("value") or ""
                radio_id = radio.get_attribute("id")
                label_txt = val
                if radio_id:
                    label_el = pregunta.query_selector(f'label[for="{radio_id}"]')
                    if label_el:
                        label_txt = label_el.inner_text().strip()
                opciones.append({"value": val, "text": label_txt, "element": radio})

            ranking = _opciones_ordenadas(opciones)
            if not ranking:
                continue
            candidato = elegir_respuesta(texto_pregunta, ranking)
            if candidato and candidato.get("element"):
                elegido = candidato
            else:
                elegido = ranking[0]

            if DUMP:
                _dump_registrar("radio", texto_pregunta, ranking, elegido)
            pausa(page, H_ANTES_CLICK)
            elegido["element"].click()
            log(f"    [RADIO] {texto_pregunta[:50]}... -> {elegido['value']}")
            respondidas += 1
            pausa(page, H_ENTRE_PREGUNTAS)
            continue

        # --- CHECKBOX ---
        checkboxes = pregunta.query_selector_all('input[type="checkbox"]')
        if checkboxes and len(checkboxes) > 0:
            if DUMP:
                opciones_cb = []
                for cb in checkboxes:
                    cb_id = cb.get_attribute("id")
                    txt = cb.get_attribute("value") or ""
                    if cb_id:
                        lbl = pregunta.query_selector(f'label[for="{cb_id}"]')
                        if lbl:
                            txt = lbl.inner_text().strip()
                    opciones_cb.append({"text": txt})
                _dump_registrar("checkbox", texto_pregunta, opciones_cb, None)
            if CHECKBOX_MODO == "todas":
                marcados = 0
                for cb in checkboxes:
                    if not cb.is_checked():
                        pausa(page, H_ANTES_CLICK)
                        cb.check()
                        marcados += 1
                        pausa(page, (0.2, 0.5))
                if marcados:
                    log(f"    [CHECK] {texto_pregunta[:50]}... -> marcadas {marcados} casillas")
                    respondidas += marcados
                pausa(page, H_ENTRE_PREGUNTAS)
                continue
            else:
                for cb in checkboxes:
                    if not cb.is_checked():
                        pausa(page, H_ANTES_CLICK)
                        cb.check()
                        log(f"    [CHECK] {texto_pregunta[:50]}... -> marcada primera")
                        respondidas += 1
                        pausa(page, H_ENTRE_PREGUNTAS)
                        break
                continue

        # --- INPUT TEXT ---
        input_text = pregunta.query_selector('input[type="text"].form-control')
        if input_text:
            if DUMP:
                _dump_registrar("texto", texto_pregunta, [], {"text": RESPUESTA_ABIERTA})
            valor_actual = input_text.input_value()
            if not valor_actual.strip():
                pausa(page, H_ANTES_CLICK)
                teclear(input_text, RESPUESTA_ABIERTA)
                log(f"    [TEXT] {texto_pregunta[:50]}... -> {RESPUESTA_ABIERTA}")
                respondidas += 1
                pausa(page, H_ENTRE_PREGUNTAS)
            continue

        # --- TEXTAREA ---
        textarea = pregunta.query_selector("textarea")
        if textarea:
            if DUMP:
                _dump_registrar("texto", texto_pregunta, [], {"text": RESPUESTA_ABIERTA})
            valor_actual = textarea.input_value()
            if not valor_actual.strip():
                pausa(page, H_ANTES_CLICK)
                teclear(textarea, RESPUESTA_ABIERTA)
                log(f"    [TEXTAREA] {texto_pregunta[:50]}... -> {RESPUESTA_ABIERTA}")
                respondidas += 1
                pausa(page, H_ENTRE_PREGUNTAS)
            continue

    return respondidas


def procesar_secciones_js(page, max_secciones=50) -> dict:
    """
    Procesa hasta max_secciones secciones usando JavaScript directamente.
    Retorna {respondidas, secciones, terminado}.
    """
    codigo = f"""
    () => {{
        return new Promise((resolve) => {{
            let respondidas = 0;
            let secciones = 0;
            let maximo = {max_secciones};
            const RESPUESTA = '{RESPUESTA_ABIERTA}';
            const HUMANO = {str(MODO_HUMANO).lower()};
            const VEL = {VELOCIDAD};
            const R = {{
                lectura: [{H_LECTURA[0]}, {H_LECTURA[1]}],
                antes: [{H_ANTES_CLICK[0]}, {H_ANTES_CLICK[1]}],
                paso: [{H_ENTRE_PASOS[0]}, {H_ENTRE_PASOS[1]}],
            }};
            function espera(rango) {{
                if (!HUMANO) return 80;
                const seg = (rango[0] + Math.random() * (rango[1] - rango[0])) * VEL;
                return Math.max(50, seg * 1000);
            }}

            function esNegativa(r) {{
                let t = (r.value || '').toLowerCase();
                if (r.id) {{
                    const lbl = document.querySelector('label[for="' + r.id + '"]');
                    if (lbl) t += ' ' + lbl.innerText.toLowerCase();
                }}
                if (/\bno\b/.test(t) || /\bnunca\b/.test(t)) return true;
                const neg = ['nada','malo','mala','mal','deficiente','insuficiente',
                             'regular','poco','pesimo','pésimo','insatisfecho'];
                return neg.some(k => t.includes(k));
            }}

            function responderPregunta(p) {{
                if (p.offsetParent === null) return false;

                const radios = p.querySelectorAll('input[type="radio"]');
                if (radios.length > 0) {{
                    const lista = Array.from(radios).filter(r => !r.disabled);
                    if (!lista.length) return false;
                    // Ordenar por valor desc; preferir no marcadas (fuerza 'change')
                    const orden = lista.slice().sort((a, b) => {{
                        const va = parseFloat(a.value), vb = parseFloat(b.value);
                        return (isNaN(vb) ? -1 : vb) - (isNaN(va) ? -1 : va);
                    }});
                    let cand = orden.filter(r => !r.checked);
                    if (!cand.length) return false;
                    const mejorRadio = cand[0];
                    if (mejorRadio) {{
                        mejorRadio.click();
                        mejorRadio.dispatchEvent(new Event('change', {{bubbles: true}}));
                        return true;
                    }}
                    return false;
                }}

                const select = p.querySelector('select.form-control');
                if (select) {{
                    const opts = Array.from(select.options).filter(o => o.value && o.value.trim() && !o.disabled);
                    if (opts.length) {{
                        const positivas = ['excelente','muy bueno','muy buena','bueno','buena','siempre',
                                           'todos los dias','totalmente de acuerdo','satisfecho','si'];
                        let mejor = null;
                        for (const kw of positivas) {{
                            mejor = opts.find(o => o.text.toLowerCase().includes(kw));
                            if (mejor) break;
                        }}
                        if (!mejor) {{
                            let mejorVal = -Infinity;
                            for (const o of opts) {{
                                const v = parseFloat(o.value);
                                if (!isNaN(v) && v > mejorVal) {{ mejorVal = v; mejor = o; }}
                            }}
                        }}
                        if (!mejor) mejor = opts[opts.length - 1];
                        if (select.value !== mejor.value || !select.value) {{
                            select.value = mejor.value;
                            select.dispatchEvent(new Event('change', {{bubbles: true}}));
                            return true;
                        }}
                    }}
                    return false;
                }}

                const inpText = p.querySelector('input[type="text"].form-control');
                if (inpText && !inpText.value.trim()) {{
                    inpText.value = RESPUESTA;
                    inpText.dispatchEvent(new Event('input', {{bubbles: true}}));
                    inpText.dispatchEvent(new Event('change', {{bubbles: true}}));
                    return true;
                }}

                const ta = p.querySelector('textarea');
                if (ta && !ta.value.trim()) {{
                    ta.value = RESPUESTA;
                    ta.dispatchEvent(new Event('input', {{bubbles: true}}));
                    ta.dispatchEvent(new Event('change', {{bubbles: true}}));
                    return true;
                }}

                const checks = p.querySelectorAll('input[type="checkbox"]');
                if (checks.length > 0) {{
                    for (const c of checks) {{
                        if (!c.checked && !c.disabled) {{
                            c.click();
                            c.dispatchEvent(new Event('change', {{bubbles: true}}));
                            return true;
                        }}
                    }}
                }}
                return false;
            }}

            function textoVisible() {{
                for (const px of document.querySelectorAll('.WPregunta')) {{
                    if (px.offsetParent === null) continue;
                    const el = px.querySelector('.Pregunta');
                    if (el) return el.innerText.trim().substring(0, 60);
                }}
                return '';
            }}

            function paso() {{
                // Responder UNA pregunta visible y esperar como un humano
                let contestada = false;
                for (const p of document.querySelectorAll('.WPregunta')) {{
                    if (p.offsetParent === null) continue;
                    const largo = (function () {{
                        const el = p.querySelector('.Pregunta');
                        return el ? el.innerText.trim().length : 0;
                    }})();
                    if (responderPregunta(p)) {{
                        respondidas++;
                        contestada = true;
                        const base = espera(R.lectura) + espera(R.antes);
                        setTimeout(paso, base + (largo > 80 ? espera([300, 800]) : 0));
                        break;
                    }}
                }}
                if (!contestada) avanzar();
            }}

            function avanzar() {{
                const btn = document.querySelector('a.btn-primary.pull-right');
                const btnSig = document.querySelector('a.btn-success.pull-right:not(.disabled)');
                const siguiente = (btnSig && btnSig.innerText.trim().toLowerCase().includes('siguiente')) ? btnSig : null;

                if (siguiente) {{
                    siguiente.click();
                    secciones++;
                    if (secciones >= maximo) {{
                        resolve({{respondidas, secciones, terminado: false, texto: textoVisible()}});
                    }} else {{
                        setTimeout(paso, espera(R.paso));
                    }}
                }} else {{
                    resolve({{respondidas, secciones, terminado: true, texto: ''}});
                }}
            }}

            paso();
        }});
    }}
    """
    return page.evaluate(codigo)


def avanzar_secciones_python(page, max_secciones=60) -> int:
    """
    Fallback en Python: responde todas las preguntas visibles y avanza de paso
    hasta que no haya boton 'Siguiente' o no haya progreso.
    """
    total = 0
    texto_anterior = ""
    for i in range(max_secciones):
        total += responder_preguntas_visibles(page)

        btn_sig = page.query_selector('a.btn-success.pull-right:not(.disabled)')
        if not (btn_sig and btn_sig.is_visible() and
                "siguiente" in (btn_sig.inner_text() or "").strip().lower()):
            break

        btn_sig.click()
        page.wait_for_timeout(DELAY_MEDIO)

        texto_actual = ""
        for p in page.query_selector_all(".WPregunta"):
            try:
                if p.evaluate("el => el.offsetParent !== null"):
                    el = p.query_selector(".Pregunta")
                    if el:
                        texto_actual = el.inner_text().strip()[:60]
                        break
            except Exception:
                continue

        if i >= 1 and texto_actual and texto_actual == texto_anterior:
            log(f"  [LOOP-PY] Texto no cambio: '{texto_actual}'")
            break
        texto_anterior = texto_actual
    return total


def avanzar_secciones_y_responder(page) -> int:
    prueba = page.evaluate("""() => {
        for (const p of document.querySelectorAll('.WPregunta')) {
            if (p.offsetParent !== null && p.querySelector('input[type="radio"]'))
                return true;
        }
        return false;
    }""")
    if prueba:
        total = 0; sec = 0; lote = 0; texto_anterior = ""
        # Contar pasos reales del SmartWizard
        pasos_totales = page.evaluate("""() => {
            const ul = document.querySelector('.swMain .anchor');
            if (!ul) return -1;
            return ul.querySelectorAll('li').length;
        }""")
        log(f"  Pasos SmartWizard: {pasos_totales}")
        while True:
            lote += 1
            log(f"  Lote JS (seccion ~{sec + 1})...")
            try:
                r = procesar_secciones_js(page, max_secciones=80)
            except Exception as e:
                log(f"  Error JS: {e}, cambiando a Python...")
                return total + avanzar_secciones_python(page)
            total += r["respondidas"]; sec += r["secciones"]
            page.wait_for_timeout(100)
            if r["terminado"]:
                break
            # Detectar loop: si el texto de la pregunta no cambia entre lotes
            if lote >= 2 and r.get("texto") and r["texto"] == texto_anterior:
                log(f"  [LOOP] Texto no cambio en lote {lote}: '{r['texto']}'")
                log(f"  Forzando terminacion...")
                break
            texto_anterior = r.get("texto", "")
        log(f"  Total secciones JS: {sec}")
        return total
    else:
        return avanzar_secciones_python(page)


def _manejar_modal(page) -> None:
    """Cierra el modal de confirmacion si aparece."""
    for selector in [
        '.swal2-confirm',
        '.modal.show .btn-primary',
        '.modal.in .btn-primary',
        '.bootbox .btn-primary',
        'button.confirm',
    ]:
        try:
            btn = page.query_selector(selector)
            if btn and btn.is_visible():
                log(f"  Modal detectado, confirmando...")
                btn.click()
                page.wait_for_timeout(DELAY_MEDIO)
                return
        except Exception:
            continue


def _payload_encuesta(page) -> dict:
    """Devuelve el token y la encuesta serializada que se enviarian."""
    return page.evaluate("""() => {
        var plug = window.__savedEncuestac || window.encuestac;
        var tokenEl = document.querySelector('input[name="_token"]');
        var token = tokenEl ? tokenEl.value : '';
        if (plug && typeof plug.Serialize === 'function') {
            var s = plug.Serialize();
            return { token: token, encuesta: (typeof s === 'string') ? s : JSON.stringify(s) };
        }
        var f = document.getElementById('frmencuesta');
        if (!f) return { token: token, encuesta: '' };
        var fd = new FormData(f);
        var p = new URLSearchParams();
        for (const kv of fd) p.append(kv[0], kv[1]);
        return { token: token, encuesta: p.toString() };
    }""")


def finalizar_encuesta(page) -> dict:
    """
    Finaliza la encuesta. Metodo principal: AJAX POST directo con
    action='contestacion' + token CSRF + Encuesta serializada.
    Devuelve {'ok': bool, 'msg': str}.
    """
    # Modo simulacion: NO enviar, solo mostrar lo que se enviaria
    if NO_ENVIAR:
        log("  [SIMULACION] No se envia la encuesta.")
        try:
            pay = _payload_encuesta(page)
            enc = pay.get("encuesta", "") or ""
            log(f"  [SIMULACION] Payload ({len(enc)} caracteres): {enc[:500]}")
        except Exception as e:
            log(f"  [SIMULACION] No se pudo leer el payload: {str(e)[:80]}")
        return {"ok": True, "msg": "SIMULADO (no enviado)"}

    log("  Finalizando encuesta...")

    # --- Metodo principal: AJAX directo ---
    if 'Encuestas' in page.url:
        try:
            r = page.evaluate("""() => {
                var plug = window.__savedEncuestac || window.encuestac;
                var tokenEl = document.querySelector('input[name="_token"]');
                var token = tokenEl ? tokenEl.value : '';
                var data;
                if (plug && typeof plug.Serialize === 'function') {
                    data = { action: 'contestacion', _token: token, Encuesta: plug.Serialize() };
                } else {
                    var f = document.getElementById('frmencuesta');
                    if (!f) return { estatus: false, error: 'sin_formulario' };
                    var fd = new FormData(f);
                    var p = new URLSearchParams();
                    for (const kv of fd) p.append(kv[0], kv[1]);
                    data = { action: 'contestacion', _token: token, Encuesta: p.toString() };
                }
                return new Promise(function(resolve) {
                    $.ajax({ type: 'POST', dataType: 'json', data: data })
                        .done(function(d) { resolve(d); })
                        .fail(function(x, s) { resolve({ estatus: false, error: 'ajax_fail:' + s }); });
                });
            }""")
            if isinstance(r, dict) and r.get('estatus'):
                msg = r.get('message', '')
                log(f"  Servidor confirma: {msg[:100]}")
                _manejar_modal(page)
                return {"ok": True, "msg": msg}
            err = r.get('error') if isinstance(r, dict) else str(r)
            log(f"  AJAX sin exito ({err}), usando fallback...")
        except Exception as e:
            log(f"  ERROR en AJAX: {str(e)[:80]}")

    # --- Fallback: boton Finalizar + metodos alternativos ---
    def _encontrar_boton():
        btn = page.query_selector('a.btn-primary.pull-right')
        if not btn:
            btn = page.query_selector('a:has-text("Finalizar")')
        return btn

    btn_finalizar = _encontrar_boton()
    if btn_finalizar and btn_finalizar.is_visible():
        log("  Presionando boton Finalizar...")
        page.evaluate("""(btn) => {
            if (typeof $ !== 'undefined') $(btn).trigger('click');
        }""", btn_finalizar)
        try:
            btn_finalizar.click()
        except Exception:
            pass
        page.wait_for_timeout(DELAY_LARGO)
    else:
        log("  Finalizar no visible. Intentando metodos alternativos...")
        for num, nombre, codigo in [
            (1, "smartWizard_last", """() => {
                const sw = document.querySelector('.swMain');
                if (!sw) return 'no_swMain';
                $(sw).smartWizard('last'); return 'sw_last';
            }"""),
            (2, "ultimo_anchor", """() => {
                const a = document.querySelectorAll('.swMain .anchor li a');
                if (a.length > 0) { a[a.length-1].click(); return 'ok_'+a.length; }
                return 'no_anchor';
            }"""),
            (3, "jquery_click", """() => {
                let btn = document.querySelector('a.btn-primary.pull-right');
                if (!btn) {
                    for (const a of document.querySelectorAll('a'))
                        if (a.innerText.trim().toLowerCase() === 'finalizar') { btn = a; break; }
                }
                if (!btn) return 'no_encontrado';
                btn.style.cssText = 'display:block!important;visibility:visible!important;opacity:1!important';
                $(btn).trigger('click'); return 'jq_trigger';
            }"""),
        ]:
            try:
                r = page.evaluate(codigo)
                log(f"  M{num} {nombre}: {r}")
            except Exception as ex:
                log(f"  M{num} {nombre}: ERROR: {str(ex)[:60]}")
            page.wait_for_timeout(DELAY_LARGO)

    _manejar_modal(page)
    page.wait_for_timeout(DELAY_MEDIO)

    log("  Encuesta finalizada (via boton/fallback).")
    return {"ok": True, "msg": "finalizada por boton/fallback"}


# ---------------------------------------------------------------------------
# Flujo principal
# ---------------------------------------------------------------------------

def ejecutar_bot():
    """Funcion principal que ejecuta todo el flujo del bot."""
    # Validar credenciales
    if not EMAIL or not PASSWORD:
        log("ERROR: Falta EMAIL o PASSWORD en el archivo .env")
        log("Copia .env.example a .env y llena tus credenciales.")
        sys.exit(1)

    log("=" * 60)
    log("Bot de Encuestas SIAAUTT - Iniciando")
    log(f"Modo humano: {'ON' if MODO_HUMANO else 'OFF'} | Velocidad: {VELOCIDAD}")
    log("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=BROWSER_ARGS)
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        try:
            # --- PASO 1: LOGIN ---
            if not hacer_login(page):
                log("Abortando: no se pudo iniciar sesion.")
                browser.close()
                sys.exit(1)

            # --- PASO 2: NAVEGAR A ENCUESTAS ---
            if not navegar_a_encuestas(page):
                log("Abortando: no se pudo navegar a encuestas.")
                browser.close()
                sys.exit(1)

            if DUMP:
                log("*** MODO DUMP: se listaran todas las preguntas y opciones (no se envia) ***")
            elif SIMULAR:
                log("*** MODO SIMULACION: no se enviara ninguna encuesta ***")

            # --- PASO 3: DETECTAR ENCUESTAS ---
            total_encuestas = obtener_encuestas_disponibles(page)
            log(f"Encuestas disponibles (Activadas): {total_encuestas}")

            if total_encuestas == 0:
                log("No hay encuestas activadas por completar. Finalizando.")
                notificar("No hay encuestas activadas pendientes.")
                browser.close()
                return

            # En simulacion, fijar los indices de las activadas (no cambian al no enviar)
            indices_sim = []
            if NO_ENVIAR:
                for fila in page.query_selector_all("table tbody tr"):
                    est = fila.query_selector("span.label-warning")
                    if est and "activada" in est.inner_text().lower():
                        indices_sim.append(fila.get_attribute("data-index"))

            # --- PASO 4: COMPLETAR CADA ENCUESTA ---
            encuestas_completadas = 0

            for i in range(total_encuestas):
                log("-" * 50)
                log(f"Encuesta {i + 1} de {total_encuestas}")
                log("-" * 50)

                try:
                    # Recargar la pagina de encuestas para refrescar la tabla
                    # (necesario porque al completar una encuesta el DOM cambia)
                    if i > 0:
                        page.goto(URL_ENCUESTAS, wait_until="domcontentloaded")
                        page.wait_for_load_state("networkidle", timeout=15000)
                        page.wait_for_timeout(DELAY_LARGO)

                    # Obtener las filas actualizadas - siempre seleccionar la primera
                    # encuesta activada disponible (indice 0 de las activadas)
                    if NO_ENVIAR and i < len(indices_sim):
                        indice_activada = indices_sim[i]
                    else:
                        filas = page.query_selector_all("table tbody tr")
                        indice_activada = None
                        for fila in filas:
                            estatus = fila.query_selector("span.label-warning")
                            if estatus and "activada" in estatus.inner_text().lower():
                                indice_activada = fila.get_attribute("data-index")
                                break

                    if indice_activada is None:
                        log("  No se encontraron mas encuestas activadas.")
                        break

                    # Seleccionar la encuesta
                    if not seleccionar_encuesta(page, indice_activada):
                        log(f"  Saltando encuesta {i + 1}: no se pudo abrir.")
                        continue

                    # Pausa de "leer la introduccion" de la encuesta
                    pausa(page, H_ABRIR_ENCUESTA)

                    _set_encuesta_actual(page)
                    log(f"  Encuesta: {ENCUESTA_ACTUAL['nombre']} ({ENCUESTA_ACTUAL['codigo']})")

                    # Responder todas las secciones
                    total_resp = avanzar_secciones_y_responder(page)
                    log(f"  Total preguntas respondidas: {total_resp}")

                    # Pausa de "revisar" antes de enviar
                    pausa(page, H_ANTES_FINALIZAR)

                    # Finalizar
                    res = finalizar_encuesta(page)
                    if res.get("ok"):
                        encuestas_completadas += 1
                        notificar(f"Encuesta {i + 1} COMPLETADA. {res.get('msg', '')}")
                    else:
                        log(f"  Advertencia: posible error al finalizar encuesta {i + 1}")

                    # Esperar antes de la siguiente (simula leer el "gracias")
                    pausa(page, H_ENTRE_ENCUESTAS)
                except Exception as e:
                    log(f"  ERROR en encuesta {i + 1}: {e}")
                    log("  Intentando continuar con la siguiente encuesta...")
                    try:
                        browser.close()
                    except Exception:
                        pass
                    try:
                        browser = p.chromium.launch(
                            headless=True,
                            args=BROWSER_ARGS,
                        )
                        context = browser.new_context(
                            viewport={"width": 1280, "height": 720},
                            user_agent=(
                                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                            ),
                        )
                        page = context.new_page()
                        if not hacer_login(page) or not navegar_a_encuestas(page):
                            log("  No se pudo re-establecer sesion. Abortando.")
                            break
                    except Exception as e2:
                        log(f"  Error fatal al reconectar: {e2}")
                        break

            # --- RESUMEN ---
            log("=" * 60)
            log(f"Proceso completado!")
            log(f"Encuestas completadas: {encuestas_completadas} de {total_encuestas}")
            log("=" * 60)
            if DUMP:
                guardar_dump()
            notificar(f"Proceso terminado: {encuestas_completadas}/{total_encuestas} encuestas completadas.")

        except Exception as e:
            log(f"ERROR inesperado: {e}")
            raise
        finally:
            browser.close()
            log("Navegador cerrado.")


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ejecutar_bot()
