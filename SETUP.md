# Configuração da sincronização Google Drive → GitHub

Siga estes passos uma única vez. Depois disso, a sincronização roda sozinha.

## 1. Preparar a pasta no Google Drive

1. Crie (ou escolha) a pasta no Google Drive que vai guardar os documentos
   da Produtec.
2. Abra a pasta e copie o **ID da pasta**, que fica na URL:
   `https://drive.google.com/drive/folders/ESTE_TRECHO_AQUI_É_O_ID`
3. Guarde esse ID — você vai usá-lo no passo 4.

## 2. Criar a Service Account no Google Cloud

1. Acesse [console.cloud.google.com](https://console.cloud.google.com/) e
   crie um projeto novo (ou use um existente).
2. No menu, vá em **APIs e serviços > Biblioteca**, procure por
   **Google Drive API** e clique em **Ativar**.
3. Vá em **APIs e serviços > Credenciais > Criar credenciais > Conta de
   serviço**.
4. Dê um nome (ex: `produtec-drive-sync`) e conclua a criação.
5. Clique na service account criada, aba **Chaves > Adicionar chave >
   Criar nova chave > JSON**. Um arquivo `.json` vai ser baixado — guarde-o,
   ele não pode ser baixado de novo depois.
6. Copie o **e-mail** da service account (algo como
   `produtec-drive-sync@seu-projeto.iam.gserviceaccount.com`).

## 3. Compartilhar a pasta do Drive com a Service Account

1. Volte à pasta do Google Drive (passo 1).
2. Clique em **Compartilhar**, cole o e-mail da service account e dê
   permissão de **Leitor**.

## 4. Configurar os Secrets no GitHub

No repositório, vá em **Settings > Secrets and variables > Actions > New
repository secret** e crie dois secrets:

| Nome | Valor |
|---|---|
| `GDRIVE_CREDENTIALS` | Cole o conteúdo inteiro do arquivo `.json` baixado no passo 2.5 |
| `GDRIVE_FOLDER_ID` | O ID da pasta copiado no passo 1 |

## 5. Habilitar permissão de escrita do workflow

Em **Settings > Actions > General > Workflow permissions**, marque
**Read and write permissions** e salve. (Sem isso, o workflow não consegue
commitar os arquivos sincronizados de volta no repositório.)

## 6. Testar

Vá na aba **Actions**, clique no workflow **Sync Google Drive** e depois em
**Run workflow**. Acompanhe o log — ao final, os arquivos devem aparecer
dentro da pasta `docs/` do repositório.

A partir daqui, o workflow roda sozinho a cada 30 minutos (ajustável em
`.github/workflows/sync-drive.yml`, campo `cron`).

## Problemas comuns

- **"File not found" ou pasta vazia**: confira se a pasta foi mesmo
  compartilhada com o e-mail exato da service account.
- **Erro de autenticação**: confira se o secret `GDRIVE_CREDENTIALS` tem o
  JSON completo, sem cortar nenhuma chave.
- **Workflow não commita nada**: confira o passo 5 (permissões de escrita).
