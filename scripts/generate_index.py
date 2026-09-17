#!/usr/bin/env python3
"""
Gera docs/index.html: uma página simples que lista, em árvore, tudo que foi
sincronizado do Google Drive. É essa página que o GitHub Pages serve como
o "site" do hub.

Roda depois de sync_drive.py (veja .github/workflows/sync-drive.yml).
"""

import html
import os

DOCS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
EXCLUDE = {".sync-manifest.json", "index.html"}

PAGE_TEMPLATE = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hub Produtec</title>
<style>
  :root {{
    --bg: #ffffff;
    --text: #1a1a1a;
    --muted: #6b7280;
    --border: #e5e7eb;
    --accent: #b45309;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #14110f;
      --text: #f3f1ee;
      --muted: #a39c93;
      --border: #2e2925;
      --accent: #e8b169;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    max-width: 760px;
    margin: 0 auto;
    padding: 40px 20px 80px;
    line-height: 1.5;
  }}
  h1 {{ font-size: 1.75rem; margin-bottom: 4px; }}
  p.subtitle {{ color: var(--muted); margin-top: 0; }}
  ul {{ list-style: none; padding-left: 1.25rem; }}
  ul.root {{ padding-left: 0; }}
  li {{ margin: 6px 0; }}
  .folder {{ font-weight: 600; }}
  a {{
    color: var(--text);
    text-decoration: none;
    border-bottom: 1px solid var(--border);
  }}
  a:hover {{ border-color: var(--accent); color: var(--accent); }}
  .empty {{ color: var(--muted); font-style: italic; }}
  footer {{ margin-top: 48px; color: var(--muted); font-size: 0.85rem; }}
</style>
</head>
<body>
  <h1>Hub Produtec</h1>
  <p class="subtitle">Documentação sincronizada automaticamente do Google Drive.</p>
  {body}
  <footer>Atualizado automaticamente pelo GitHub Actions a cada sincronização.</footer>
</body>
</html>
"""


def build_tree(path):
    entries = []
    if not os.path.isdir(path):
        return entries
    for name in sorted(os.listdir(path), key=str.lower):
        if name in EXCLUDE or name.startswith("."):
            continue
        full = os.path.join(path, name)
        if os.path.isdir(full):
            entries.append((name, build_tree(full), True))
        else:
            entries.append((name, None, False))
    return entries


def render(entries, rel="", root=False):
    if not entries:
        return '<p class="empty">Nenhum documento sincronizado ainda.</p>'
    css_class = ' class="root"' if root else ""
    parts = [f"<ul{css_class}>"]
    for name, children, is_dir in entries:
        esc_name = html.escape(name)
        if is_dir:
            child_rel = f"{rel}{name}/"
            parts.append(
                f"<li><span class=\"folder\">\U0001F4C1 {esc_name}</span>"
                f"{render(children, child_rel)}</li>"
            )
        else:
            href = f"{rel}{name}"
            parts.append(f'<li><a href="{html.escape(href)}">\U0001F4C4 {esc_name}</a></li>')
    parts.append("</ul>")
    return "".join(parts)


def main():
    tree = build_tree(DOCS_DIR)
    body = render(tree, root=True)
    page = PAGE_TEMPLATE.format(body=body)
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    print("docs/index.html gerado.")


if __name__ == "__main__":
    main()
