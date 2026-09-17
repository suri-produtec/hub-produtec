# Hub Produtec

Hub de documentação técnica/de produto da Produtec. Os documentos originais
vivem no Google Drive; este repositório sincroniza automaticamente uma cópia
deles em `docs/`, para servir como fonte de verdade versionada e navegável no
GitHub (histórico, busca, links diretos, etc.).

## Como funciona

```
Google Drive (pasta compartilhada)  --(GitHub Action, a cada 30 min)-->  docs/ neste repositório
```

- Um workflow do GitHub Actions (`.github/workflows/sync-drive.yml`) roda
  periodicamente e também pode ser disparado manualmente na aba **Actions**.
- Ele usa uma *service account* do Google para ler os arquivos de uma pasta
  do Drive e replica a estrutura de pastas dentro de `docs/`.
- Google Docs, Sheets e Slides são exportados como PDF, XLSX e PPTX
  respectivamente (formatos que o GitHub já pré-visualiza ou que abrem em
  qualquer computador). Outros arquivos são copiados como estão.
- Só o que mudou é baixado de novo — um manifesto interno
  (`docs/.sync-manifest.json`) guarda a data de modificação de cada arquivo.

## Configuração (única vez)

Veja o passo a passo completo em [`SETUP.md`](./SETUP.md). Resumo:

1. Criar a pasta no Google Drive e compartilhar com uma *service account* do
   Google Cloud.
2. Guardar as credenciais dessa service account e o ID da pasta como
   **Secrets** deste repositório no GitHub.
3. Rodar o workflow manualmente uma vez (aba **Actions**) para conferir que
   está tudo certo.

## Estrutura

```
.
├── docs/                     # espelho dos arquivos do Google Drive (gerado automaticamente)
├── scripts/
│   ├── sync_drive.py         # script que faz a sincronização
│   └── requirements.txt
├── .github/workflows/
│   └── sync-drive.yml        # workflow agendado do GitHub Actions
├── README.md
└── SETUP.md                  # passo a passo de configuração das credenciais
```

## Importante

Não edite arquivos dentro de `docs/` diretamente neste repositório — a
próxima sincronização vai sobrescrevê-los. Edições devem ser feitas nos
arquivos originais no Google Drive.
