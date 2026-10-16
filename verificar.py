#!/usr/bin/env python3
"""Verifica el estado actual de las encuestas pendientes en SIAAUTT."""
import os
import re
import sys

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()
EMAIL = os.getenv("EMAIL")
PASSWORD = os.getenv("PASSWORD")
BASE_URL = os.getenv("BASE_URL", "https://uttorreon.mx")
MATRICULA = os.getenv("MATRICULA", "")

BROWSER_ARGS = [
    "--disable-dev-shm-usage", "--no-sandbox",
    "--disable-gpu", "--disable-software-rasterizer",
]


def log(msg):
    print(msg, flush=True)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=BROWSER_ARGS)
    page = browser.new_page()

    page.goto(BASE_URL, wait_until="domcontentloaded")
    page.wait_for_selector("input#email", timeout=15000)
    page.fill("input#email", EMAIL)
    page.fill("input#password", PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle", timeout=15000)
    page.wait_for_timeout(1500)
    log(f"Login OK -> {page.url}")

    # Detectar matricula automaticamente
    for a in page.query_selector_all("a"):
        href = a.get_attribute("href") or ""
        m = re.search(r"uttorreon\.mx/(\d{6,})/AreaAlumno/Encuestas", href)
        if m:
            MATRICULA = m.group(1)
            break
    log(f"Matricula: {MATRICULA}")

    url = f"{BASE_URL}/{MATRICULA}/AreaAlumno/Encuestas"
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle", timeout=15000)
    page.wait_for_timeout(1500)

    try:
        page.wait_for_selector("table tbody tr", timeout=8000)
    except Exception:
        log("NO HAY FILAS EN TABLA")
        log(f"URL: {page.url}")
        browser.close()
        sys.exit(0)

    filas = page.query_selector_all("table tbody tr")
    log(f"Total filas: {len(filas)}")
    activadas = 0
    for f in filas:
        texto = f.inner_text().replace("\n", " | ").strip()
        est = f.query_selector("span.label-warning")
        badge = est.inner_text().strip() if est else ""
        indice = f.get_attribute("data-index") or "-"
        if "activada" in badge.lower():
            activadas += 1
        if texto:
            log(f"  [{indice}] badge='{badge}' :: {texto[:110]}")

    log(f"\nTotal ACTIVADAS (pendientes): {activadas}")
    page.screenshot(path="verificacion.png", full_page=True)
    log("Screenshot: verificacion.png")
    browser.close()
