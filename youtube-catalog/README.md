# rw / ai · Catálogo de transcrições

Sua biblioteca local de transcrições, com a identidade rw / ai, capas, navegação por canal e categoria, busca no texto e leitura das análises de método.

## Abrir e desligar

1. Mantenha o **Docker Desktop** aberto.
2. Abra **Iniciar.command** nesta pasta. Na primeira vez, a imagem será construída e as dependências serão baixadas.
3. Acesse **http://localhost:8765**.
4. Para desligar, abra **Parar.command** ou use **Stop** no projeto **youtube-catalog** do Docker Desktop. Use **Start** para voltar a ligar.

O catálogo não inicia sozinho ao ligar o Docker. Os atalhos são para macOS. Caso o macOS peça confirmação para executar um atalho, use o menu contextual **Abrir**.

Pelo terminal, depois da preparação inicial pelos atalhos:

```bash
docker compose up -d       # ligar
docker compose stop       # desligar
docker compose ps         # consultar o estado
```

Para instalar pelo terminal no macOS ou Linux, a partir desta pasta:

```bash
mkdir -p data ../yt-transcripts
printf 'LOCAL_UID=%s\nLOCAL_GID=%s\n' "$(id -u)" "$(id -g)" > .env
docker compose up -d --build
```

Esse preparo é para uma instalação nova; preserve um `.env` já configurado. No Windows com Docker Desktop, crie as pastas `data` e `../yt-transcripts`, copie `.env.example` para `.env` e execute `docker compose up -d --build`.

A coleção padrão fica em `../yt-transcripts`, ao lado desta pasta. Para usar uma coleção existente em outro lugar, defina `YOUTUBE_TRANSCRIPTS_DIR=/caminho/da/colecao` no `.env` e recrie o container. Não é necessário copiar as transcrições.

## Como usar

- **Início** mostra os recentes e prateleiras por categorias ou canais.
- **Todos os vídeos** permite combinar busca, canal, categoria e idioma, e escolher a ordenação.
- Abrir um resultado da busca leva ao trecho correspondente. Na página do vídeo, a aba **Transcrição** permite pesquisar no texto e carregar mais trechos. A aba **Método** mostra a análise já existente, quando disponível.
- Baixe os arquivos originais nos botões TXT, SRT e JSON. Quando houver identificação de falantes, as duas variantes são disponibilizadas.
- **Categorias** permite criar e renomear temas. Na página do vídeo, escolha várias categorias ou aceite sugestões. Restaurar a classificação importada remove somente a personalização daquele vídeo.
- **Excluir do catálogo**, na página do vídeo, abre uma confirmação e envia o item para a **Lixeira**. Ele sai das prateleiras, da busca e das contagens e continua excluído após atualizações e reinicializações.
- Na **Lixeira**, use **Restaurar** para devolver o vídeo ao catálogo, com suas categorias preservadas. A exclusão não apaga as transcrições, análises ou demais arquivos de origem.
- **Excluir definitivamente**, também na Lixeira, pede que você digite **EXCLUIR** antes de confirmar. Essa ação apaga a pasta original inteira daquele vídeo, incluindo transcrições, análises e arquivos de áudio ou vídeo, e remove seus dados e capa do catálogo. Não pode ser desfeita pela interface; recuperação depende de um backup anterior. Os demais vídeos e categorias permanecem salvos.
- Se a exclusão definitiva for interrompida por uma falha de arquivo, o item permanece na Lixeira com um aviso. Confirme novamente para concluir a limpeza. Após o início da remoção física, **Restaurar** fica indisponível; a limpeza não é retomada automaticamente ao ligar o aplicativo.
- O botão de atualização relê a coleção. A atualização também acontece ao iniciar e a cada 60 segundos enquanto o aplicativo estiver ligado.

## Onde ficam os dados

- **Originais:** a pasta configurada em `YOUTUBE_TRANSCRIPTS_DIR`, por padrão a vizinha `../yt-transcripts`. A montagem permite escrita para a exclusão definitiva confirmada na Lixeira. A importação e a leitura dos vídeos não modificam esses arquivos. Novas transcrições continuam sendo geradas pelo fluxo que você já utiliza.
- **Catálogo:** `data/` nesta pasta, contendo o banco local, suas classificações e as capas baixadas. Parar ou recriar o container preserva essa pasta.
- **Configuração:** `.env`, criado pelo atalho com o usuário do computador. A porta padrão é `8765`.

As categorias e tags vêm dos arquivos `METODO.md`; os metadados vêm de `video.json`. As sugestões são calculadas localmente e só mudam a classificação quando você as escolhe. Vídeos sem categorias aparecem em **Sem categoria**. Alterações feitas na interface prevalecem sobre reimportações. A lixeira também fica no banco local e faz parte do backup da pasta `data/`.

Se um arquivo estiver incompleto durante a gravação, o aplicativo mantém a última versão válida e mostra um aviso. Se a pasta de um vídeo desaparecer, ele permanece identificado como indisponível, com suas classificações preservadas.

## Internet e uso offline

A construção inicial requer internet para baixar as dependências. Durante o uso, somente as capas são baixadas automaticamente, a partir dos endereços de miniaturas do YouTube. Textos, classificações e consultas ficam no computador. A interface usa arquivos e fontes locais; não há serviços de análise, transcrição ou telemetria externos.

Depois de salvas, as capas continuam disponíveis offline. Se uma imagem não puder ser obtida, será exibida uma capa substituta. Abrir um vídeo no YouTube exige internet.

Para impedir novos downloads de capas, altere `DOWNLOAD_THUMBNAILS=false` em `.env` e execute `docker compose up -d`. As capas já salvas continuarão visíveis.

## Atualizar o aplicativo

Depois de alterar o código:

```bash
docker compose up -d --build
```

Para outra porta, altere `CATALOG_PORT` em `.env` e execute `docker compose up -d`. A publicação continua restrita a `127.0.0.1`.

## Backup e restauração

1. Desligue o catálogo com **Parar.command** ou `docker compose stop`.
2. Copie a pasta **data**, o arquivo **.env** e a coleção **yt-transcripts** para o local do backup.
3. Para restaurar no mesmo computador, mantenha o catálogo desligado e substitua esses itens pelas cópias salvas; depois inicie novamente.

Em outro computador, preserve a disposição das pastas e recrie `.env` pelo atalho para usar o novo usuário. O banco utiliza SQLite e permanece no disco do computador. Nenhum backup externo é feito automaticamente.

## Desenvolvimento e verificação

Interface React/TypeScript/Vite compilada e servida por FastAPI, com SQLite FTS5. Um processo de servidor e um serviço Docker. O contrato da API está em `CONTRACT.md`.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/python -m pytest backend/tests -q
python3 tests/acceptance.py --url http://127.0.0.1:8765 --source ../yt-transcripts
python3 tests/docker-lifecycle.py
```

O último teste usa um container temporário na porta `8766` e uma cópia descartável do banco para verificar edição de categorias, desligamento, recriação e funcionamento sem rede. Ele preserva o catálogo principal. Os testes de navegador estão documentados em `tests/README.md`.

Para consultar falhas de inicialização, use `docker compose logs --tail=80`. Confirme que `../yt-transcripts` existe e que a porta configurada está livre. Na primeira importação, aguarde a atualização terminar antes de conferir as contagens.
