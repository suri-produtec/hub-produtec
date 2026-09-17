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

Variáveis de ambiente esperadas (definidas como Secrets do GitHub Actions):
  GDRIVE_CREDENTIALS   -> conteúdo JSON da chave da service account
  GDRIVE_FOLDER_ID     -> ID da pasta raiz no Google Drive a sincronizar

Uso local (opcional, para testar fora do GitHub Actions):
  export GDRIVE_CREDENTIALS="$(cat service-account.json)"
  export GDRIVE_FOLDER_ID="1AbCdEfGhIjKlMnOpQrStUvWxYz"
  python scripts/sync_drive.py
"""

import io
import json
import os
import re
import sys
import unicodedata

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

DOCS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
MANIFEST_PATH = os.path.join(DOCS_DIR, ".sync-manifest.json")


def safe_name(name: str) -> str:
    """Deixa o nome do arquivo/pasta seguro para o sistema de arquivos."""
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r'[\\/:*?"<>|]', "-", name).strip()
    return name or "sem-nome"


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


def sync_folder(service, folder_id, local_dir, manifest, stats):
    os.makedirs(local_dir, exist_ok=True)
    seen_ids = set()

    for item in list_children(service, folder_id):
        file_id = item["id"]
        name = safe_name(item["name"])
        mime_type = item["mimeType"]
        modified_time = item.get("modifiedTime", "")
        seen_ids.add(file_id)

        if mime_type == FOLDER_MIME:
            sync_folder(service, file_id, os.path.join(local_dir, name), manifest, stats)
            continue

        cached = manifest.get(file_id)
        dest_path = os.path.join(local_dir, name)
        if mime_type in GOOGLE_EXPORT_MAP:
            ext = GOOGLE_EXPORT_MAP[mime_type][1]
            if not dest_path.endswith(ext):
                dest_path += ext

        if cached and cached.get("modifiedTime") == modified_time and os.path.exists(dest_path):
            stats["unchanged"] += 1
            continue

        actual_path = download_file(service, file_id, mime_type, os.path.join(local_dir, name))
        manifest[file_id] = {
            "name": name,
            "path": os.path.relpath(actual_path, DOCS_DIR),
            "modifiedTime": modified_time,
        }
        stats["updated"] += 1
        print(f"  sincronizado: {os.path.relpath(actual_path, DOCS_DIR)}")

    return seen_ids


def main():
    root_folder_id = os.environ.get("GDRIVE_FOLDER_ID")
    if not root_folder_id:
        sys.exit("ERRO: variável de ambiente GDRIVE_FOLDER_ID não definida.")

    service = get_drive_service()
    manifest = load_manifest()
    stats = {"updated": 0, "unchanged": 0}

    print("Sincronizando Google Drive -> docs/ ...")
    sync_folder(service, root_folder_id, DOCS_DIR, manifest, stats)
    save_manifest(manifest)

    print(f"Concluído. Atualizados: {stats['updated']}, sem mudança: {stats['unchanged']}.")


if __name__ == "__main__":
    main()
