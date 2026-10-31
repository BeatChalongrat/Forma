# The assistant's code runner (ADR-048). Build context: repository root.
# It holds Python, the data libraries and one small program. No forma package, no secrets.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# Fonts, then what opens and renders documents (LibreOffice, Poppler, Java for tabula), draws diagrams, reads scans
# and unpacks archives, then the shell tools a run can call.
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-core fontconfig \
        libreoffice-core libreoffice-writer libreoffice-calc libreoffice-impress default-jre-headless \
        poppler-utils libpango-1.0-0 libpangoft2-1.0-0 graphviz \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-tha \
        7zip unrar-free libarchive-tools unzip bc ripgrep fd-find sqlite3 \
    && ln -s /usr/bin/fdfind /usr/local/bin/fd \
    && rm -rf /var/lib/apt/lists/*
COPY deploy/sandbox/fonts.conf /etc/fonts/local.conf
COPY deploy/sandbox/requirements.txt /srv/requirements.txt
RUN pip install -r /srv/requirements.txt
COPY deploy/sandbox/matplotlibrc /opt/matplotlib/matplotlibrc
COPY backend/src/forma/code_sandbox/runner.py /srv/runner.py
ENV MATPLOTLIBRC=/opt/matplotlib/matplotlibrc MPL_CACHE=/opt/matplotlib/cache
# The font list is slow to build and the file system is read-only at run time: build it now.
RUN MPLCONFIGDIR=/opt/matplotlib/cache python -c "import matplotlib.font_manager as f; f.fontManager" \
    && chmod -R a+rX /opt/matplotlib
# LibreOffice looks up the name of the uid a run uses. The home directory is set for each run.
RUN groupadd -g 20000 run && useradd -u 20000 -g 20000 -M -d /nonexistent -s /usr/sbin/nologin run
ENV SANDBOX_PACKAGES="numpy pandas polars pyarrow scipy scikit-learn statsmodels sympy mpmath networkx matplotlib seaborn graphviz openpyxl python-calamine fastexcel xlrd xlsxwriter odfpy python-docx python-pptx pypdf pdfplumber pypdfium2 pdf2image tabula-py reportlab img2pdf weasyprint striprtf extract-msg pillow pytesseract beautifulsoup4 lxml pyyaml rapidfuzz pythainlp tabulate tqdm python-dateutil pytz joblib"
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD ["python", "-c", "import socket; s = socket.socket(socket.AF_UNIX); s.connect('/run/sandbox/runner.sock')"]
CMD ["python", "-I", "/srv/runner.py"]
