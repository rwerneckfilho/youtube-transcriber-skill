# YouTube Transcriber Skill

Skill para o Codex que baixa o áudio de um vídeo do YouTube e gera uma transcrição completa localmente com `yt-dlp`, `ffmpeg` e Whisper.cpp.

Não usa API paga. Na primeira execução, o script baixa o modelo multilíngue `large-v3-turbo-q5_0` para o cache local e valida seu checksum SHA-256.

## Requisitos

- Codex com suporte a skills
- Python 3
- `curl`
- `yt-dlp`
- `ffmpeg`
- `whisper-cli` (fornecido pelo pacote `whisper-cpp`)

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

Quando a saída estiver dentro de uma pasta chamada `yt-transcripts`, a skill também atualiza automaticamente o arquivo `INDEX.md` da coleção.

## Observações

- O modelo padrão ocupa espaço considerável e é baixado apenas na primeira execução.
- Use `--model /caminho/modelo.bin` para selecionar outro modelo GGML compatível com Whisper.cpp.
- Use `--language auto` quando o idioma não for conhecido.
- Respeite direitos autorais e os termos da plataforma. Use a transcrição para conteúdo que você tem autorização para processar.
