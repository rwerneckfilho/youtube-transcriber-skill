# YouTube Transcriber Skill

Skill para o Codex que baixa o áudio de um vídeo do YouTube e gera uma transcrição completa localmente com `yt-dlp`, `ffmpeg` e Whisper.cpp. Opcionalmente, separa as falas por speaker com `pyannote.audio`.

Não usa API paga. Na primeira execução, o script baixa o modelo multilíngue `large-v3-turbo-q5_0` para o cache local e valida seu checksum SHA-256. Todo o processamento de áudio, transcrição e diarização acontece na máquina local.

O repositório também inclui a plataforma **[rw / ai · Catálogo de transcrições](youtube-catalog/README.md)**: um aplicativo local no Docker para explorar as transcrições por capas, canais e categorias, pesquisar no texto, ler métodos, baixar arquivos e gerenciar a Lixeira.

## Plataforma de catálogo no Docker

Requer Docker Desktop ou Docker Engine com Compose. Depois de clonar o repositório, no macOS ou Linux:

```bash
cd youtube-transcriber-skill/youtube-catalog
mkdir -p data ../yt-transcripts
printf 'LOCAL_UID=%s\nLOCAL_GID=%s\n' "$(id -u)" "$(id -g)" > .env
docker compose up -d --build
```

Abra [localhost:8765](http://localhost:8765). Esses passos preparam uma instalação nova; preserve um `.env` já configurado. No macOS também há os atalhos **Iniciar.command** e **Parar.command**. Para Windows e outras opções, consulte as [instruções da plataforma](youtube-catalog/README.md).

O catálogo lê `yt-transcripts/` na raiz do repositório. Para uma coleção existente em outro local, configure `YOUTUBE_TRANSCRIPTS_DIR` no `.env` da plataforma. O mesmo nome de variável pode ser exportado no terminal para que a skill grave as novas transcrições nessa coleção.

O projeto **youtube-catalog** pode ser ligado e desligado no Docker. A porta fica restrita ao computador, os dados persistem na pasta local `youtube-catalog/data/` e as fontes da interface funcionam offline. A primeira instalação baixa dependências; durante o uso, os únicos acessos externos automáticos são os downloads das capas.

**Excluir do catálogo** envia o vídeo para a Lixeira e preserva os originais. **Excluir definitivamente**, na Lixeira, exige uma confirmação e apaga a pasta original do vídeo, incluindo transcrições, análises e mídias. As coleções, capas, banco e configurações pessoais não fazem parte deste repositório.

## Requisitos

- Codex com suporte a skills
- Python 3
- `curl`
- `yt-dlp`
- `ffmpeg`
- `whisper-cli` (fornecido pelo pacote `whisper-cpp`)

Para a separação opcional de speakers:

- Python 3.10 ou superior
- `pyannote.audio` 4.x, instalado pelo script de configuração incluído
- acesso aceito ao modelo gratuito `pyannote/speaker-diarization-community-1`

No macOS com Homebrew:

```bash
brew install yt-dlp ffmpeg whisper-cpp
```

## Instalação

Clone este repositório e copie a pasta da skill para o diretório de skills do Codex:

```bash
git clone https://github.com/rwerneckfilho/youtube-transcriber-skill.git
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
cp -R youtube-transcriber-skill/youtube-full "${CODEX_HOME:-$HOME/.codex}/skills/youtube-full"
```

Reinicie o Codex para que a skill seja descoberta.

Para atualizar uma instalação existente:

```bash
git -C youtube-transcriber-skill pull
rm -rf "${CODEX_HOME:-$HOME/.codex}/skills/youtube-full"
cp -R youtube-transcriber-skill/youtube-full "${CODEX_HOME:-$HOME/.codex}/skills/youtube-full"
```

## Uso

Peça ao Codex, por exemplo:

> Use $youtube-full para transcrever este vídeo completo: https://www.youtube.com/watch?v=VIDEO_ID

Ou execute o script diretamente:

```bash
"${CODEX_HOME:-$HOME/.codex}/skills/youtube-full/scripts/transcribe-youtube.sh" \
  --language pt \
  "https://www.youtube.com/watch?v=VIDEO_ID"
```

### Separação de speakers

Configure uma vez o ambiente isolado de diarização:

```bash
"${CODEX_HOME:-$HOME/.codex}/skills/youtube-full/scripts/setup-diarization.sh"
```

Antes do primeiro download do modelo:

1. Aceite os termos em [pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1).
2. Crie um token de leitura no Hugging Face e exporte-o como `HF_TOKEN`, ou autentique o ambiente isolado:

```bash
"${XDG_CACHE_HOME:-$HOME/.cache}/youtube-full/diarization-venv/bin/hf" auth login
```

O token serve somente para autorizar o download inicial dos pesos gratuitos. Depois que o modelo estiver no cache, a diarização roda localmente. As telemetrias opcionais do pyannote e do Hugging Face ficam desativadas por padrão.

Para transcrever e separar as vozes:

```bash
"${CODEX_HOME:-$HOME/.codex}/skills/youtube-full/scripts/transcribe-youtube.sh" \
  --diarize \
  --language pt \
  "https://www.youtube.com/watch?v=VIDEO_ID"
```

Quando o número de speakers é conhecido, informe-o para melhorar a diarização:

```bash
"${CODEX_HOME:-$HOME/.codex}/skills/youtube-full/scripts/transcribe-youtube.sh" \
  --diarize \
  --num-speakers 2 \
  "https://www.youtube.com/watch?v=VIDEO_ID"
```

Também estão disponíveis `--min-speakers`, `--max-speakers`, `--diarization-device cpu|cuda|mps` e `--diarization-model ID|PATH`. Um diretório local do modelo pode ser passado com `--diarization-model` para operação inteiramente offline após o download manual.

Se o cliente padrão do YouTube responder com HTTP 403, o script tenta automaticamente o cliente `web_embedded`. Usuários avançados podem substituir os argumentos do extrator com `YOUTUBE_YTDLP_EXTRACTOR_ARGS`.

Por padrão, as transcrições são gravadas em `./yt-transcripts/<video-id>/`. Para escolher outra raiz:

```bash
export YOUTUBE_TRANSCRIPTS_DIR="$HOME/Documents/yt-transcripts"
```

Também é possível informar um diretório de saída diretamente:

```bash
"${CODEX_HOME:-$HOME/.codex}/skills/youtube-full/scripts/transcribe-youtube.sh" \
  --language en \
  "https://www.youtube.com/watch?v=VIDEO_ID" \
  "$HOME/Documents/minha-transcricao"
```

Arquivos gerados:

- `transcript.txt`
- `transcript.srt`
- `transcript.json`
- `video.json`
- `source.m4a`
- `audio.wav`

Com `--diarize`, também são gerados:

- `transcript-speakers.txt`
- `transcript-speakers.srt`
- `transcript-speakers.json`
- `diarization.json`

Os speakers recebem identificadores estáveis dentro de cada execução, como `SPEAKER_00` e `SPEAKER_01`. A diarização distingue as vozes, mas não descobre automaticamente os nomes das pessoas.

Quando a saída estiver dentro de uma pasta chamada `yt-transcripts`, a skill também atualiza automaticamente o arquivo `INDEX.md` da coleção.

## Observações

- O modelo padrão ocupa espaço considerável e é baixado apenas na primeira execução.
- Os modelos de diarização também ocupam espaço considerável e são baixados apenas quando `--diarize` é usado.
- Use `--model /caminho/modelo.bin` para selecionar outro modelo GGML compatível com Whisper.cpp.
- Use `--language auto` quando o idioma não for conhecido.
- Sobreposição de vozes, música, ruído e falas muito curtas podem reduzir a precisão da separação de speakers.
- Respeite direitos autorais e os termos da plataforma. Use a transcrição para conteúdo que você tem autorização para processar.
