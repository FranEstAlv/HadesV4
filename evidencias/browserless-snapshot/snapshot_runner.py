import argparse
import base64
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv


# ============================================================
# PATHS
# ============================================================

ROOT = Path(__file__).resolve().parent

QUERY_FILE = ROOT / "queries" / "snapshot.graphql"
OUTPUT_ROOT = ROOT / "output"


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()

BROWSERLESS_TOKEN = os.getenv("BROWSERLESS_TOKEN")

BROWSERLESS_URL = os.getenv(
    "BROWSERLESS_URL",
    "https://production-sfo.browserless.io",
)

PROXY_COUNTRY = os.getenv(
    "BROWSERLESS_PROXY_COUNTRY",
    "mx",
)

# NOTA: BROWSERLESS_TOKEN puede contener múltiples claves separadas por coma.
# Si se alcanza el límite de concurrencia (HTTP 429), el script intentará
# usar la siguiente clave disponible en la lista.


# ============================================================
# VALIDATION
# ============================================================

def validate_environment():

    if not BROWSERLESS_TOKEN:
        raise RuntimeError(
            "Falta BROWSERLESS_TOKEN en .env"
        )

    if not QUERY_FILE.exists():
        raise FileNotFoundError(
            f"No existe {QUERY_FILE}"
        )


def validate_url(url):

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            "La URL debe comenzar con http:// o https://"
        )

    if not parsed.netloc:
        raise ValueError(
            "URL inválida"
        )


# ============================================================
# BROWSERLESS
# ============================================================

def browserless_endpoint():

    return (
        f"{BROWSERLESS_URL.rstrip('/')}"
        "/stealth/bql"
    )


def browserless_params():

    return {
        # Browserless native residential proxy
        "proxy": "residential",

        # Mexico
        "proxyCountry": PROXY_COUNTRY,

        # Maintain proxy identity during this session
        "proxySticky": "true",

        # Match locale/timezone/etc. to proxy
        "proxyLocaleMatch": "true",

        # Browserless human-like interaction
        "humanlike": "true",

        # Reduce advertising/tracker noise
        "blockAds": "true",

        # Remove common cookie/consent overlays
        "blockConsentModals": "true",

        # Session limit
        "timeout": "120000",
    }


# ============================================================
# QUERY
# ============================================================

def load_query():

    return QUERY_FILE.read_text(
        encoding="utf-8"
    )


# ============================================================
# SNAPSHOT DIRECTORY
# ============================================================

def create_snapshot_directory():

    OUTPUT_ROOT.mkdir(
        parents=True,
        exist_ok=True
    )

    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    directory = (
        OUTPUT_ROOT /
        f"snapshot_{stamp}"
    )

    directory.mkdir()

    return directory


# ============================================================
# BROWSERLESS REQUEST
# ============================================================

def request_snapshot(url):

    query = load_query()

    payload = {
        "query": query,
        "variables": {
            "url": url
        },
        "operationName": "Snapshot",
    }

    tokens = [
        t.strip()
        for t in BROWSERLESS_TOKEN.split(",")
        if t.strip()
    ]

    last_error = None

    for token in tokens:

        params = browserless_params()
        params["token"] = token

        response = requests.post(
            browserless_endpoint(),
            params=params,
            headers={
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=125,
        )

        if response.status_code == 429:
            last_error = response
            continue

        response.raise_for_status()

        return response.json()

    if last_error is not None:
        print(
            "WARNING: Browserless concurrency limit reached (429). "
            "Se han agotado los tokens disponibles. "
            "Espera unos minutos o actualiza BROWSERLESS_TOKEN en .env "
            "con claves adicionales.",
            file=sys.stderr,
        )
        last_error.raise_for_status()

    raise RuntimeError(
        "No se pudo completar la solicitud a Browserless. "
        "Verifica tu BROWSERLESS_TOKEN en .env"
    )


# ============================================================
# HELPERS
# ============================================================

def attribute_value(attribute):

    if not attribute:
        return None

    return attribute.get("value")


def clean_element(element):

    cleaned = {}

    for key, value in element.items():

        if isinstance(value, dict):

            if (
                "name" in value
                and "value" in value
            ):
                cleaned[key] = value.get(
                    "value"
                )

            else:
                cleaned[key] = value

        else:
            cleaned[key] = value

    return cleaned


def clean_elements(elements):

    if not elements:
        return []

    return [
        clean_element(element)
        for element in elements
    ]


# ============================================================
# SAVE SCREENSHOT
# ============================================================

def save_screenshot(data, directory):

    screenshot = data.get(
        "screenshot"
    )

    if not screenshot:
        return None

    encoded = screenshot.get(
        "base64"
    )

    if not encoded:
        return None

    try:

        image = base64.b64decode(
            encoded
        )

    except Exception as exc:

        print(
            f"No se pudo decodificar screenshot: {exc}"
        )

        return None

    path = (
        directory /
        "screenshot.png"
    )

    path.write_bytes(image)

    return path


# ============================================================
# SAVE HTML
# ============================================================

def save_html(data, directory):

    page = data.get(
        "pageHTML"
    )

    if not page:
        return None

    html = page.get(
        "html"
    )

    if not html:
        return None

    path = (
        directory /
        "page.html"
    )

    path.write_text(
        html,
        encoding="utf-8"
    )

    return path


# ============================================================
# ELEMENT INVENTORY
# ============================================================

def build_element_inventory(data):

    return {

        "headings":
            clean_elements(
                data.get("headings")
            ),

        "forms":
            clean_elements(
                data.get("forms")
            ),

        "inputs":
            clean_elements(
                data.get("inputs")
            ),

        "buttons":
            clean_elements(
                data.get("buttons")
            ),

        "selects":
            clean_elements(
                data.get("selects")
            ),

        "links":
            clean_elements(
                data.get("links")
            ),

        "images":
            clean_elements(
                data.get("images")
            ),

        "data_asin":
            clean_elements(
                data.get("dataAsin")
            ),
    }


# ============================================================
# METADATA
# ============================================================

def build_metadata(
    requested_url,
    result,
    data
):

    navigation = (
        data.get("navigation")
        or {}
    )

    title = (
        data.get("title")
        or {}
    )

    return {
        "requested_url":
            requested_url,

        "final_url":
            navigation.get("url"),

        "http_status":
            navigation.get("status"),

        "title":
            title.get("text"),

        "navigation_time":
            navigation.get("time"),

        "proxy_country":
            PROXY_COUNTRY,

        "stealth":
            True,

        "humanlike":
            True,

        "bql_errors":
            result.get(
                "errors",
                []
            ),
    }


# ============================================================
# SAVE JSON
# ============================================================

def save_json(
    path,
    content
):

    path.write_text(
        json.dumps(
            content,
            indent=2,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )


# ============================================================
# SUMMARY
# ============================================================

def print_summary(
    metadata,
    inventory,
    directory
):

    print()
    print("=" * 65)
    print(" SNAPSHOT COMPLETED")
    print("=" * 65)

    print()
    print(
        f"Requested: {metadata['requested_url']}"
    )

    print(
        f"Final URL: {metadata['final_url']}"
    )

    print(
        f"Status:    {metadata['http_status']}"
    )

    print(
        f"Title:     {metadata['title']}"
    )

    print()
    print("Elements:")

    for key, values in inventory.items():

        print(
            f"  {key:<12} {len(values)}"
        )

    errors = metadata.get(
        "bql_errors",
        []
    )

    if errors:

        print()
        print(
            f"BQL errors/warnings: {len(errors)}"
        )

        for error in errors:

            path = error.get(
                "path",
                []
            )

            message = error.get(
                "message",
                "Unknown"
            )

            print(
                f"  {path}: {message}"
            )

    print()
    print(
        f"Output: {directory}"
    )

    print()


# ============================================================
# MAIN SNAPSHOT
# ============================================================

def run(url):

    validate_environment()
    validate_url(url)

    print()
    print("=" * 65)
    print(" BROWSERLESS SNAPSHOT RUNNER")
    print("=" * 65)

    print()
    print(f"URL:      {url}")
    print(f"Proxy:    residential/{PROXY_COUNTRY}")
    print("Stealth:  enabled")
    print("Human:    enabled")
    print()

    print("Opening page...")

    result = request_snapshot(
        url
    )

    directory = (
        create_snapshot_directory()
    )

    # ----------------------------------------
    # Save raw Browserless result
    # ----------------------------------------

    raw_path = (
        directory /
        "result.json"
    )

    save_json(
        raw_path,
        result
    )

    data = (
        result.get("data")
        or {}
    )

    # ----------------------------------------
    # HTML
    # ----------------------------------------

    save_html(
        data,
        directory
    )

    # ----------------------------------------
    # Screenshot
    # ----------------------------------------

    save_screenshot(
        data,
        directory
    )

    # ----------------------------------------
    # Elements
    # ----------------------------------------

    inventory = (
        build_element_inventory(
            data
        )
    )

    save_json(
        directory /
        "elements.json",
        inventory
    )

    # ----------------------------------------
    # Metadata
    # ----------------------------------------

    metadata = (
        build_metadata(
            url,
            result,
            data
        )
    )

    save_json(
        directory /
        "metadata.json",
        metadata
    )

    # ----------------------------------------
    # Summary
    # ----------------------------------------

    print_summary(
        metadata,
        inventory,
        directory
    )

    return directory


# ============================================================
# CLI
# ============================================================

def parse_arguments():

    parser = argparse.ArgumentParser(
        description=(
            "Inspect a web page using "
            "Browserless BrowserQL."
        )
    )

    parser.add_argument(
        "url",
        help=(
            "URL to inspect. "
            "Example: "
            "https://www.amazon.com.mx/"
        ),
    )

    return parser.parse_args()


def main():

    args = parse_arguments()

    try:

        run(args.url)

    except requests.HTTPError as exc:

        print()
        print(
            f"Browserless HTTP error: {exc}"
        )

        if exc.response is not None:

            print()
            print(
                exc.response.text[:5000]
            )

        sys.exit(1)

    except requests.Timeout:

        print()
        print(
            "Browserless request timed out."
        )

        sys.exit(1)

    except KeyboardInterrupt:

        print()
        print(
            "Interrupted."
        )

        sys.exit(130)

    except Exception as exc:

        print()
        print(
            f"{type(exc).__name__}: {exc}"
        )

        sys.exit(1)


if __name__ == "__main__":
    main()
