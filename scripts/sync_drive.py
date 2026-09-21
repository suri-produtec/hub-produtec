#!/usr/bin/env python3
"""
Sincroniza uma pasta do Google Drive para dentro deste repositório (pasta docs/).

- Arquivos "nativos" do Google (Docs, Sheets, Slides) são exportados para um
  formato legível (PDF, XLSX, PPTX respectivamente).
- Outros arquivos (PDF, imagens, DOCX que já foram enviados como upload, etc.)
  são baixados como estão.
- Subpastas do Drive viram subpastas dentro de docs/, preservando a
  organização que vocês já usam no Drive.
- Um manifesto (docs/.sync-manifest.json) guarda o `modifiedTime` de cada
  arquivo já sincronizado, para não baixar de novo o que não mudou.
- Arquivos que somem do Drive, ou que são movidos/renomeados lá (ex: tirados
  de uma pasta e colocados em outra), têm a cópia antiga apagada de docs/ no
  fim da sincronização — nada fica "fantasma" depois de uma reorganização no
  Drive (ver cleanup_stale_files()). Os assets manuais (logo, ícones de
  categoria em docs/assets/) nunca são tocados por essa limpeza.
- Uma planilha do Google chamada "Calendário", dentro de uma pasta de
  categoria (Documentos/Requisitos/Deploy), vira um calendário de eventos
  no hub em vez de um documento comum (ver docs/.calendar-data.json).
- Uma pasta chamada "Novidades", direto na raiz da pasta sincronizada (no
  mesmo nível de Documentos/Requisitos/Deploy, mas sem virar uma categoria
  do menu), vira o feed estilo jornal da página inicial: a planilha
  "Novidades" dentro dela é o conteúdo das notícias, e qualquer outro
  arquivo (imagens de capa) é baixado para docs/assets/novidades/ (ver
  docs/.news-data.json e sync_news_folder()).

Variáveis de ambiente esperadas (definidas como Secrets do GitHub Actions):
  GDRIVE_CREDENTIALS   -> conteúdo JSON da chave da service account
  GDRIVE_FOLDER_ID     -> ID da pasta raiz no Google Drive a sincronizar

Uso local (opcional, para testar fora do GitHub Actions):
  export GDRIVE_CREDENTIALS="$(cat service-account.json)"
  export GDRIVE_FOLDER_ID="1AbCdEfGhIjKlMnOpQrStUvWxYz"
  python scripts/sync_drive.py
"""

import csv
import io
import json
import os
import re
import sys
import unicodedata
from datetime import datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Tipos nativos do Google e para qual formato exportar cada um.
GOOGLE_EXPORT_MAP = {
    "application/vnd.google-apps.document": (
        "application/pdf",
        ".pdf",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ),
}

FOLDER_MIME = "application/vnd.google-apps.folder"
SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"

DOCS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
MANIFEST_PATH = os.path.join(DOCS_DIR, ".sync-manifest.json")
CALENDAR_PATH = os.path.join(DOCS_DIR, ".calendar-data.json")
NEWS_PATH = os.path.join(DOCS_DIR, ".news-data.json")
NEWS_ASSETS_DIR = os.path.join(DOCS_DIR, "assets", "novidades")

# Nome (sem acento, sem diferenciar maiúsculas/minúsculas) que uma planilha do
# Drive precisa ter, dentro de uma pasta de categoria (ex: Deploy), para virar
# um calendário de eventos no hub em vez de um documento comum.
CALENDAR_SHEET_NAMES = {"calendario"}

# Nome (sem acento, sem diferenciar maiúsculas/minúsculas) que a pasta com o
# feed da página inicial precisa ter, direto na raiz da pasta sincronizada.
NEWS_FOLDER_NAMES = {"novidades"}

# Nome que a planilha com o conteúdo das notícias precisa ter, dentro da
# pasta "Novidades".
NEWS_SHEET_NAMES = {"novidades"}

DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"]

# Arquivos na raiz de docs/ que nunca são apagados pela limpeza (são gerados
# por generate_index.py ou pelo próprio sync, não vêm do Drive).
PROTECTED_FILES = {".sync-manifest.json", ".calendar-data.json", ".news-data.json", "index.html"}

# Prefixo de caminho (relativo a docs/) que nunca é tocado pela limpeza: são
# os assets visuais colocados manualmente (logo, ícones de categoria), não
# sincronizados do Drive. docs/assets/novidades/ é a exceção — vem do Drive.
PROTECTED_ASSET_PREFIX = "assets" + os.sep


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def safe_name(name: str) -> str:
    """Deixa o nome do arquivo/pasta seguro para o sistema de arquivos."""
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r'[\\/:*?"<>|]', "-", name).strip()
    return name or "sem-nome"


def is_calendar_sheet(name: str) -> bool:
    base, _ = os.path.splitext(name)
    normalized = strip_accents(base).strip().lower()
    return normalized in CALENDAR_SHEET_NAMES


def is_news_folder(name: str) -> bool:
    normalized = strip_accents(name).strip().lower()
    return normalized in NEWS_FOLDER_NAMES


def is_news_sheet(name: str) -> bool:
    base, _ = os.path.splitext(name)
    normalized = strip_accents(base).strip().lower()
    return normalized in NEWS_SHEET_NAMES


def parse_calendar_date(raw: str):
    raw = (raw or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_calendar_csv(csv_text: str):
    """Lê o CSV exportado de uma planilha 'Calendário' e devolve uma lista de
    eventos: {"date": "AAAA-MM-DD", "title": ..., "desc": ..., "link": ...}.

    Colunas esperadas (em qualquer ordem, com ou sem acento): Data, Título,
    Descrição, Link (Link é opcional).
    """
    rows = list(csv.reader(io.StringIO(csv_text)))
    if not rows:
        return []

    header = [strip_accents(h).strip().lower() for h in rows[0]]
    col_index = {}
    for idx, h in enumerate(header):
        if h in ("data", "date"):
            col_index["date"] = idx
        elif h in ("titulo", "title"):
            col_index["title"] = idx
        elif h in ("descricao", "desc", "description"):
            col_index["desc"] = idx
        elif h in ("link", "url"):
            col_index["link"] = idx

    def get(row, key):
        idx = col_index.get(key)
        if idx is None or idx >= len(row):
            return ""
        return row[idx].strip()

    entries = []
    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue
        date_str = parse_calendar_date(get(row, "date"))
        title = get(row, "title")
        if not date_str or not title:
            continue
        entry = {"date": date_str, "title": title, "desc": get(row, "desc")}
        link = get(row, "link")
        if link:
            entry["link"] = link
        entries.append(entry)
    return entries


def parse_news_csv(csv_text: str):
    """Lê o CSV exportado da planilha 'Novidades' e devolve a lista de
    notícias do feed da página inicial: {"date", "tag", "title", "desc",
    "image", "items": [...], "link"}.

    Colunas esperadas (em qualquer ordem, com ou sem acento): Data,
    Categoria, Título, Descrição, Imagem, Itens, Link (Imagem, Itens e Link
    são opcionais).

    - Imagem: nome de um arquivo de imagem solto na mesma pasta "Novidades"
      (ele é baixado para docs/assets/novidades/), ou uma URL completa.
    - Itens: opcional, vira uma caixa com lista dentro do card. Cada linha
      da célula (Alt+Enter na planilha para quebrar linha) vira um item; uma
      linha no formato "Título — resto do texto" (ou "Título: resto") fica
      com o título em negrito no hub.
    """
    rows = list(csv.reader(io.StringIO(csv_text)))
    if not rows:
        return []

    header = [strip_accents(h).strip().lower() for h in rows[0]]
    col_index = {}
    for idx, h in enumerate(header):
        if h in ("data", "date"):
            col_index["date"] = idx
        elif h in ("categoria", "tag", "categoria/tag"):
            col_index["tag"] = idx
        elif h in ("titulo", "title"):
            col_index["title"] = idx
        elif h in ("descricao", "desc", "description"):
            col_index["desc"] = idx
        elif h in ("imagem", "image", "capa"):
            col_index["image"] = idx
        elif h in ("itens", "items", "lista"):
            col_index["items"] = idx
        elif h in ("link", "url"):
            col_index["link"] = idx

    def get(row, key):
        idx = col_index.get(key)
        if idx is None or idx >= len(row):
            return ""
        return row[idx].strip()

    entries = []
    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue
        date_str = parse_calendar_date(get(row, "date"))
        title = get(row, "title")
        if not date_str or not title:
            continue

        image = get(row, "image")
        if image and "://" not in image:
            image = f"assets/novidades/{safe_name(image)}"

        items_raw = get(row, "items")
        items = [line.strip() for line in items_raw.splitlines() if line.strip()]

        entry = {
            "date": date_str,
            "tag": get(row, "tag"),
            "title": title,
            "desc": get(row, "desc"),
            "image": image,
            "items": items,
        }
        link = get(row, "link")
        if link:
            entry["link"] = link
        entries.append(entry)

    entries.sort(key=lambda e: e["date"], reverse=True)
    return entries


def get_drive_service():
    creds_raw = os.environ.get("GDRIVE_CREDENTIALS")
    if not creds_raw:
        sys.exit("ERRO: variável de ambiente GDRIVE_CREDENTIALS não definida.")
    info = json.loads(creds_raw)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def load_manifest():
    if os.path.exists(MANIFEST_PATH):
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_manifest(manifest):
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)


def list_children(service, folder_id):
    files = []
    page_token = None
    query = f"'{folder_id}' in parents and trashed = false"
    while True:
        resp = service.files().list(
            q=query,
            spaces="drive",
            fields="nextPageToken, files(id, name, mimeType, modifiedTime)",
            pageToken=page_token,
        ).execute()
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


def download_file(service, file_id, mime_type, dest_path):
    if mime_type in GOOGLE_EXPORT_MAP:
        export_mime, ext = GOOGLE_EXPORT_MAP[mime_type]
        if not dest_path.endswith(ext):
            dest_path += ext
        request = service.files().export_media(fileId=file_id, mimeType=export_mime)
    else:
        request = service.files().get_media(fileId=file_id)

    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(buffer.getvalue())
    return dest_path


def is_protected_path(rel_path: str) -> bool:
    """Arquivos que a limpeza nunca deve apagar: os gerados/registro na raiz
    de docs/, e os assets visuais manuais em docs/assets/ (exceto
    docs/assets/novidades/, que vem do Drive e segue a limpeza normal)."""
    if rel_path in PROTECTED_FILES:
        return True
    if rel_path.startswith(PROTECTED_ASSET_PREFIX):
        rest = rel_path[len(PROTECTED_ASSET_PREFIX):]
        if not rest.startswith("novidades" + os.sep):
            return True
    return False


def cleanup_stale_files(expected_paths, manifest, stats):
    """Apaga de docs/ qualquer arquivo vindo do Drive que não foi visto nesta
    sincronização — porque foi removido do Drive, ou porque foi movido para
    outro lugar lá (o que muda o caminho esperado, deixando uma cópia velha
    órfã). Sem isso, itens reorganizados no Drive ficam duplicados para
    sempre (a cópia nova E a antiga, geralmente em "Outros")."""
    removed = []
    for root, dirs, files in os.walk(DOCS_DIR, topdown=False):
        for fname in files:
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, DOCS_DIR)
            if is_protected_path(rel):
                continue
            if rel not in expected_paths:
                os.remove(full)
                removed.append(rel)
        # remove diretórios que ficaram vazios (exceto a própria docs/ e assets/)
        if root != DOCS_DIR and not os.listdir(root):
            os.rmdir(root)

    if removed:
        stats["removed"] = len(removed)
        for rel in removed:
            print(f"  removido (não existe mais no Drive nesse caminho): {rel}")

    # limpa do manifesto qualquer entrada cujo arquivo não sobreviveu à limpeza
    for file_id in list(manifest.keys()):
        if manifest[file_id].get("path") not in expected_paths:
            del manifest[file_id]


def sync_calendar_sheet(service, file_id, name, category, calendar_data, stats):
    """Exporta uma planilha 'Calendário' como CSV e guarda os eventos lidos
    dela em calendar_data[category], em vez de baixá-la como documento."""
    if not category:
        return
    try:
        request = service.files().export_media(fileId=file_id, mimeType="text/csv")
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        csv_text = buffer.getvalue().decode("utf-8-sig")
        entries = parse_calendar_csv(csv_text)
        calendar_data[category] = entries
        stats["calendars"] = stats.get("calendars", 0) + 1
        print(f"  calendário lido: {category}/{name} ({len(entries)} eventos)")
    except Exception as exc:  # nunca deixa o calendário quebrar a sincronização
        print(f"  aviso: não consegui ler a planilha de calendário '{name}' em {category}: {exc}")


def sync_news_folder(service, folder_id, manifest, stats, news_data, expected_paths):
    """Lê a pasta 'Novidades' inteira: a planilha 'Novidades' dentro dela
    vira o feed da página inicial (acumulado em news_data), e qualquer outro
    arquivo (as imagens de capa) é baixado para docs/assets/novidades/ em vez
    de aparecer como documento comum."""
    for item in list_children(service, folder_id):
        file_id = item["id"]
        name = safe_name(item["name"])
        mime_type = item["mimeType"]
        modified_time = item.get("modifiedTime", "")

        if mime_type == SPREADSHEET_MIME and is_news_sheet(item["name"]):
            try:
                request = service.files().export_media(fileId=file_id, mimeType="text/csv")
                buffer = io.BytesIO()
                downloader = MediaIoBaseDownload(buffer, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                csv_text = buffer.getvalue().decode("utf-8-sig")
                entries = parse_news_csv(csv_text)
                news_data.extend(entries)
                stats["news"] = stats.get("news", 0) + 1
                print(f"  novidades lidas: {name} ({len(entries)} notícias)")
            except Exception as exc:  # nunca deixa o feed quebrar a sincronização
                print(f"  aviso: não consegui ler a planilha de novidades '{name}': {exc}")
            continue

        if mime_type == FOLDER_MIME:
            # não esperamos subpastas dentro de "Novidades"; ignora.
            print(f"  aviso: ignorando subpasta inesperada dentro de Novidades: {name}")
            continue

        # qualquer outro arquivo solto na pasta "Novidades" é tratado como
        # imagem de capa (ou outro asset) referenciado pela coluna Imagem.
        cached = manifest.get(file_id)
        dest_path = os.path.join(NEWS_ASSETS_DIR, name)
        if cached and cached.get("modifiedTime") == modified_time and os.path.exists(dest_path):
            stats["unchanged"] += 1
            expected_paths.add(os.path.relpath(dest_path, DOCS_DIR))
            continue

        actual_path = download_file(service, file_id, mime_type, dest_path)
        rel_path = os.path.relpath(actual_path, DOCS_DIR)
        manifest[file_id] = {
            "name": name,
            "path": rel_path,
            "modifiedTime": modified_time,
        }
        expected_paths.add(rel_path)
        stats["updated"] += 1
        print(f"  sincronizado (novidades): {rel_path}")


def sync_folder(service, folder_id, local_dir, manifest, stats, calendar_data, news_data, expected_paths, category=None):
    os.makedirs(local_dir, exist_ok=True)

    for item in list_children(service, folder_id):
        file_id = item["id"]
        name = safe_name(item["name"])
        mime_type = item["mimeType"]
        modified_time = item.get("modifiedTime", "")

        if mime_type == FOLDER_MIME:
            if local_dir == DOCS_DIR and is_news_folder(item["name"]):
                # A pasta "Novidades" na raiz não é uma categoria comum: ela
                # alimenta o feed da página inicial (ver sync_news_folder).
                sync_news_folder(service, file_id, manifest, stats, news_data, expected_paths)
                continue

            # Uma pasta logo dentro da raiz sincronizada é uma categoria
            # (Documentos/Requisitos/Deploy); dentro dela, a categoria se
            # mantém a mesma nas subpastas mais profundas.
            child_category = name if local_dir == DOCS_DIR else category
            sync_folder(
                service,
                file_id,
                os.path.join(local_dir, name),
                manifest,
                stats,
                calendar_data,
                news_data,
                expected_paths,
                category=child_category,
            )
            continue

        if mime_type == SPREADSHEET_MIME and is_calendar_sheet(item["name"]):
            sync_calendar_sheet(service, file_id, name, category, calendar_data, stats)
            continue

        cached = manifest.get(file_id)
        dest_path = os.path.join(local_dir, name)
        if mime_type in GOOGLE_EXPORT_MAP:
            ext = GOOGLE_EXPORT_MAP[mime_type][1]
            if not dest_path.endswith(ext):
                dest_path += ext

        if cached and cached.get("modifiedTime") == modified_time and os.path.exists(dest_path):
            stats["unchanged"] += 1
            expected_paths.add(os.path.relpath(dest_path, DOCS_DIR))
            continue

        actual_path = download_file(service, file_id, mime_type, os.path.join(local_dir, name))
        rel_path = os.path.relpath(actual_path, DOCS_DIR)
        manifest[file_id] = {
            "name": name,
            "path": rel_path,
            "modifiedTime": modified_time,
        }
        expected_paths.add(rel_path)
        stats["updated"] += 1
        print(f"  sincronizado: {rel_path}")


def main():
    root_folder_id = os.environ.get("GDRIVE_FOLDER_ID")
    if not root_folder_id:
        sys.exit("ERRO: variável de ambiente GDRIVE_FOLDER_ID não definida.")

    service = get_drive_service()
    manifest = load_manifest()
    stats = {"updated": 0, "unchanged": 0}
    calendar_data = {}
    news_data = []
    expected_paths = set()

    print("Sincronizando Google Drive -> docs/ ...")
    sync_folder(service, root_folder_id, DOCS_DIR, manifest, stats, calendar_data, news_data, expected_paths)

    cleanup_stale_files(expected_paths, manifest, stats)
    save_manifest(manifest)

    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(CALENDAR_PATH, "w", encoding="utf-8") as f:
        json.dump(calendar_data, f, ensure_ascii=False, indent=2, sort_keys=True)

    news_data.sort(key=lambda e: e["date"], reverse=True)
    with open(NEWS_PATH, "w", encoding="utf-8") as f:
        json.dump(news_data, f, ensure_ascii=False, indent=2)

    print(f"Concluído. Atualizados: {stats['updated']}, sem mudança: {stats['unchanged']}, removidos: {stats.get('removed', 0)}.")
    if calendar_data:
        resumo = ", ".join(f"{cat} ({len(evts)})" for cat, evts in calendar_data.items())
        print(f"Calendários encontrados: {resumo}")
    print(f"Notícias na página inicial: {len(news_data)}.")


if __name__ == "__main__":
    main()
