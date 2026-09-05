# rw / ai · Catálogo de transcrições

Biblioteca local com capas, busca no texto, categorias e análises de método. Inclui uma fila para transcrever vídeos e playlists do YouTube no próprio Docker, sem serviço pago de transcrição. Interface em **português, inglês e espanhol**.

## Instalar, abrir e desligar

Requer Docker Desktop ou Docker Engine com Compose. A primeira construção baixa dependências e compila o motor local de transcrição.

```bash
# Dentro da pasta youtube-catalog, numa instalação nova:
cp .env.example .env
docker compose up -d --build
```

Abra [localhost:8765](http://localhost:8765). No macOS, também é possível abrir **Iniciar.command** e **Parar.command**. Preserve um `.env` já configurado. O projeto **youtube-catalog** aparece no Docker Desktop e pode ser ligado ou desligado pelos botões habituais; não inicia automaticamente.

```bash
docker compose stop       # desligar e preservar a fila e a biblioteca
docker compose start      # ligar novamente
docker compose ps         # consultar o estado
```

A porta é publicada apenas em `127.0.0.1`, sem login. Se quiser trocar a porta, altere `CATALOG_PORT` no `.env` e execute `docker compose up -d`.

## Adicionar vídeos ou uma playlist

1. Abra **Adicionar vídeos** e cole uma URL HTTPS de vídeo ou playlist do YouTube.
2. Use a detecção automática, ou escolha explicitamente **Vídeo** ou **Playlist**. Um link de vídeo com `list=` usa a playlist no modo automático; escolher Vídeo processa somente o vídeo.
3. Escolha o idioma falado: **Automático**, **Português**, **Inglês** ou **Espanhol**.
4. Envie para a fila. Uma playlist é percorrida inteira e seus vídeos são transcritos um por vez. Os resultados entram no catálogo automaticamente.

Acompanhe a etapa atual, as contagens e os resultados de cada vídeo. Itens privados, removidos ou com erro são sinalizados; os demais continuam. Vídeos já presentes, inclusive na Lixeira, não são sobrescritos. É possível cancelar um trabalho ou repetir os itens que falharam/foram cancelados. Lives ainda em andamento não são processadas.

Ao desligar o Docker, o processamento para. A fila permanece no banco e retoma ao ligar; uma etapa incompleta pode ser reiniciada, preservando os vídeos já concluídos. A biblioteca continua disponível durante o processamento. Transcrições longas podem levar tempo, pois usam a CPU local. `WHISPER_THREADS` no `.env` permite ajustar o número de threads.

O motor usa yt-dlp, ffmpeg e Whisper.cpp, com o modelo multilíngue `large-v3-turbo-q5_0`. O primeiro trabalho baixa aproximadamente 547 MiB de modelo e confere seu SHA-256; o arquivo fica em cache no volume. Não é necessário token de API. O idioma escolhido informa o idioma do áudio: não traduz a transcrição nem altera o conteúdo original.

## Idioma e navegação

O seletor de idioma alterna toda a interface entre PT, EN e ES e guarda a preferência neste navegador. Títulos, transcrições, categorias criadas pelo usuário e análises mantêm seu conteúdo original.

- **Início:** recentes e prateleiras por canal ou categoria.
- **Todos os vídeos:** busca combinada com canal, categoria, idioma e ordenação. Resultados levam ao trecho correspondente.
- **Transcrição:** texto com timestamps, busca interna, falantes quando disponíveis e carregamento progressivo.
- **Método:** análise existente em `METODO.md`, quando disponível. Novas transcrições não geram análises automaticamente.
- **Arquivos:** TXT, SRT e JSON, incluindo versões com falantes quando já existirem.
- **Categorias:** criar, renomear e atribuir vários temas; sugestões locais só são aplicadas quando selecionadas.
- **Lixeira:** Excluir do catálogo preserva os arquivos; Restaurar recupera o vídeo com suas categorias. Excluir definitivamente exige digitar EXCLUIR e apaga os arquivos daquele vídeo no armazenamento do Docker. Após uma falha parcial, a Lixeira permite confirmar novamente para concluir; a remoção física não é retomada automaticamente.

## Banco de dados e conteúdos dentro do Docker

O volume nomeado **youtube-catalog-storage** guarda toda a biblioteca. Ele é gerenciado pelo Docker e permanece no computador mesmo quando o container é parado, atualizado ou recriado:

| Dentro do container | Conteúdo |
| --- | --- |
| `/storage/catalog/catalog.sqlite3` | Banco SQLite: metadados, transcrições segmentadas, índice FTS5, métodos, categorias, Lixeira e fila |
| `/storage/transcripts/` | Arquivos de cada vídeo: `video.json`, TXT, SRT, JSON e demais originais importados |
| `/storage/catalog/thumbnails/` | Capas locais |
| `/storage/models/` | Modelo de transcrição verificado |
| `/storage/work/` | Trabalho incompleto e arquivos temporários da fila |

Não há dependência de uma pasta de transcrições no computador para usar a instalação nova. A variável `CATALOG_VOLUME` seleciona outro volume; trocar seu nome abre uma biblioteca diferente. **Não remova o volume nem execute `docker compose down -v` para simplesmente desligar**, pois isso apaga os dados persistentes.

## Migrar a coleção da versão anterior

Execute antes de iniciar uma biblioteca nova. A migração copia as fontes, as capas e o banco anterior, preservando categorias, métodos e Lixeira. As pastas antigas ficam intactas como cópia de segurança; passam a ser independentes da biblioteca no Docker. Alterações posteriores nessas pastas não são importadas automaticamente.

Os caminhos padrão são `../yt-transcripts` e `./data`. Para outras pastas, defina `LEGACY_TRANSCRIPTS_DIR` e `LEGACY_DATA_DIR` no `.env`. Ambas precisam existir.

```bash
docker compose stop
docker compose build
docker compose -f compose.yaml -f compose.migrate.yaml run --rm --no-deps --user 0:0 --entrypoint python catalog /app/scripts/storage.py import-legacy --source /legacy/transcripts --data /legacy/data
docker compose up -d
```

Se houver uma exclusão definitiva incompleta, conclua-a na Lixeira da versão anterior antes de migrar. O processo confere as cópias e a integridade do banco. Não sobrescreve um volume que já contenha uma biblioteca. Depois de concluído, repetir a migração não reimporta vídeos apagados. Uma cópia interrompida exige repetir explicitamente o comando; o aplicativo não inicia enquanto houver uma migração incompleta. Para importar uma coleção sem banco anterior, use uma pasta vazia como `LEGACY_DATA_DIR`.

## Backup

Desligue antes de copiar a biblioteca. Um backup inclui banco, conteúdos, fila e modelo:

```bash
docker compose stop
mkdir -p backups
docker compose run --rm --no-deps --user 0:0 --cap-add DAC_OVERRIDE --entrypoint python -v "$PWD/backups:/backup" catalog /app/scripts/storage.py backup /backup/biblioteca.tar.gz
```

Use um nome de arquivo novo a cada backup. O comando não sobrescreve backups anteriores. A pasta `backups/` fica fora do volume. Para restaurar, com o aplicativo desligado, extraia o arquivo em um **volume novo** e configure `CATALOG_VOLUME` para esse volume. Mantenha a propriedade dos arquivos em `1000:1000`, como na biblioteca original. Só remova a biblioteca anterior depois de conferir a restauração.

## Internet e privacidade

A instalação baixa dependências. Um trabalho solicitado acessa o YouTube para metadados e áudio; o primeiro uso também baixa o modelo do Hugging Face. Capas podem ser baixadas em segundo plano. Áudio e texto são processados localmente; não há envio a uma API de transcrição ou serviço de telemetria.

Sem internet, é possível navegar, buscar, ler, editar categorias e baixar os arquivos já armazenados. Novos trabalhos podem falhar e ser repetidos quando a conexão voltar. As fontes da interface são locais. Para impedir novos downloads de capas, use `DOWNLOAD_THUMBNAILS=false` no `.env` e recrie o container.

## Atualização e verificação

```bash
docker compose up -d --build
```

O volume é reutilizado. Banco, modelos e conteúdos não fazem parte do repositório. Para investigar inicialização, use `docker compose logs --tail=80`.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/python -m pytest backend/tests tests/test_storage.py -q
```

O contrato da API está em [CONTRACT.md](CONTRACT.md). Os roteiros de teste, incluindo idioma, playlists, exclusão e persistência, estão em [tests/README.md](tests/README.md). As verificações destrutivas usam dados descartáveis.

Referências: [Whisper.cpp](https://github.com/ggml-org/whisper.cpp), [yt-dlp](https://github.com/yt-dlp/yt-dlp), [volumes do Docker](https://docs.docker.com/engine/storage/volumes/).
