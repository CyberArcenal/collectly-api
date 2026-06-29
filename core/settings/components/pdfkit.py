import os
import pdfkit

import os
import pdfkit
import shutil

# Hanapin ang wkhtmltopdf path mula sa ENV o fallback sa common Linux/Windows paths
DEFAULT_PATHS = [
    "/usr/local/bin/wkhtmltopdf",  # common sa Docker Alpine/Debian
    "/usr/bin/wkhtmltopdf",        # common sa Ubuntu/Debian
    r"C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe",  # Windows
]

# Priority: ENV var > unang existing default path
WKHTMLTOPDF_PATH = os.environ.get("WKHTMLTOPDF_PATH") or next(
    (p for p in DEFAULT_PATHS if os.path.isfile(p) and os.access(p, os.X_OK)),
    shutil.which("wkhtmltopdf")  # fallback: hanapin sa PATH
)

if not WKHTMLTOPDF_PATH:
    raise RuntimeError("❌ wkhtmltopdf binary not found. Please install it or set WKHTMLTOPDF_PATH env var.")

PDFKIT_CONFIG = pdfkit.configuration(wkhtmltopdf=WKHTMLTOPDF_PATH)

PDFKIT_OPTIONS = {
    "page-size": "Letter",
    "margin-top": "0.5in",
    "margin-right": "0.5in",
    "margin-bottom": "0.5in",
    "margin-left": "0.5in",
    "encoding": "UTF-8",
    "no-outline": None,
}

def is_wkhtmltopdf_available(path=WKHTMLTOPDF_PATH):
    """Check if wkhtmltopdf exists and is executable."""
    return os.path.isfile(path) and os.access(path, os.X_OK)

if not is_wkhtmltopdf_available():
    raise RuntimeError(f"❌ wkhtmltopdf binary not executable at: {WKHTMLTOPDF_PATH}")