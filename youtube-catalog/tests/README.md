# Verificação de aceite

Com o catálogo ligado, execute a partir da pasta `youtube-catalog`:

```sh
python3 tests/acceptance.py --url http://127.0.0.1:8765 --source ../yt-transcripts
```

O programa usa somente a biblioteca padrão do Python e requisições de leitura. Aguarda a importação inicial por até 90 segundos (`--wait` altera esse prazo). A coleção original nunca recebe arquivos de teste, alterações de categorias ou outros dados de verificação.

Os totais esperados são calculados diretamente dos metadados e transcrições disponíveis; não há uma constante de 82 vídeos. O teste consulta a lixeira separadamente e subtrai seus vídeos das verificações do acervo ativo. A soma entre ativos e lixeira deve corresponder às fontes, portanto históricos de fontes removidas podem causar uma diferença legítima. Categorias adicionais do usuário são permitidas. As categorias efetivas retornadas pelos vídeos são usadas para verificar os filtros, preservando personalizações.

O aceite verifica:

- Estatísticas, todos os IDs e títulos, paginação sem repetições e filtros por canal, categoria e idioma.
- Busca com e sem acentos e em maiúsculas, busca no texto transcrito e acesso ao segmento correto.
- Todos os segmentos do vídeo mais longo em páginas de 100, com milissegundos comparados à transcrição original.
- Métodos sem cabeçalho YAML, ausência de método, falantes e downloads TXT/SRT/JSON idênticos aos originais.
- Erros JSON para rotas inexistentes, bloqueio de caminhos fora dos downloads, capas locais e carregamento da interface por endereço direto.
- SHA-256 dos metadados, métodos e transcrições antes e depois dos testes. Mídia de áudio/vídeo não é lida nem alterada.

Uma falha é apresentada com o nome do cenário. O processo encerra com código 1 quando algum cenário falha e 0 quando todos passam. Cenários que dependem de exemplos específicos, como títulos acentuados, falantes e classificações, informam quando não encontram uma amostra ativa. Os downloads são conferidos mesmo quando todos os vídeos com falantes estiverem na lixeira.

## Verificação no navegador

`browser-smoke.cjs` usa Playwright com o Google Chrome instalado. Com Playwright acessível ao Node, execute:

```sh
node tests/browser-smoke.cjs
```

O endereço padrão é `http://127.0.0.1:8765`; `CATALOG_URL` aceita outro endereço local. Disponibilize `playwright` e `pngjs` em uma instalação Node de testes, por exemplo com `npm install --no-save playwright pngjs`, ou aponte `NODE_PATH` para uma instalação existente desses pacotes. O Chrome deve estar instalado.

O roteiro verifica início, filtros, paginação, busca, acesso ao trecho encontrado, método, falantes, carregamento progressivo, diretório de canais, teclado e telas de 375 px. Abre e fecha o diálogo de categorias sem salvar. Bloqueia requisições de escrita à API e todo acesso HTTP externo durante a execução; recarrega a interface e repete a leitura nessas condições. Registra erros JavaScript e qualquer tentativa de acesso externo.

As capturas de tela ficam em `/tmp/youtube-catalog-home.png`, `/tmp/youtube-catalog-reader.png` e `/tmp/youtube-catalog-mobile.png`. O teste usa vídeos classificados, com método e com título acentuado da coleção inicial. As verificações de falantes e método ausente informam quando não há amostras ativas.

## Exclusão definitiva: somente dados descartáveis

O roteiro `browser-permanent.cjs` apaga de verdade um vídeo artificial e seus arquivos. Ele se recusa a executar fora da porta local `8766` ou contra uma pasta que não seja a fixture temporária marcada pelo preparador:

```sh
python3 tests/create-permanent-fixture.py
```

O preparador imprime os caminhos `source` e `data` de uma pasta temporária nova, contendo exatamente dois vídeos falsos, transcrições, metadados, métodos e pequenos arquivos de áudio. Inicie um servidor isolado em `127.0.0.1:8766`, montando essa fonte com escrita permitida e usando sua pasta de dados exclusiva. Nunca conecte esse servidor à coleção original. Depois execute:

```sh
TEST_SOURCE_DIR="<source informado pelo preparador>" node tests/browser-permanent.cjs
```

O roteiro verifica os avisos, o nome do vídeo, foco em Cancelar, Escape, confirmação exata `EXCLUIR`, modal mobile e exclusão da pasta completa. Compara hashes do outro vídeo, verifica sua leitura e seus downloads e atualiza o catálogo para provar que o vídeo removido não reaparece. As ações de mover para a lixeira e restaurar continuam cobertas separadamente por `browser-update.cjs` e devem preservar todos os originais.

Cada execução bem-sucedida requer uma fixture nova, pois a anterior foi parcialmente apagada. As capturas ficam em `/tmp/youtube-catalog-permanent-dialog.png` e `/tmp/youtube-catalog-permanent-mobile.png`; o relatório fica em `/tmp/youtube-catalog-permanent-report.json`.

## Fila e idiomas da interface com API simulada

`browser-jobs.cjs` testa somente a interface compilada de um servidor isolado em `127.0.0.1:8766`. Todas as rotas `/api/*` são interceptadas pelo Playwright e respondidas por fixtures em memória dentro do próprio teste. Nenhum trabalho real é enfileirado e nenhum vídeo é baixado ou transcrito.

```sh
node tests/browser-jobs.cjs
```

O roteiro cobre envio de vídeo e playlist, seleção do idioma da transcrição, estados da fila, logs, cancelamento e nova tentativa. Também verifica PT/EN/ES nas rotas e diálogos, persistência da preferência da interface após recarregar e layout móvel. As fixtures usam conteúdo neutro para detectar texto da interface que continue em português nas traduções. O relatório declara explicitamente essa cobertura simulada; testes de processamento real, migração e persistência do volume precisam ser executados separadamente.

O relatório fica em `/tmp/youtube-catalog-jobs-report.json`, com capturas por idioma. Os roteiros anteriores iniciam seus contextos isolados explicitamente em português, sem modificar a preferência do navegador pessoal do usuário.

## Volume nomeado: integração totalmente sintética

Com a imagem já construída e a porta `8767` livre, execute:

```sh
python3 tests/docker-volume.py
```

O roteiro fixa o ID da imagem local `youtube-catalog:local` (`--image` permite outra imagem já existente), sem baixar imagens. Cria um volume nomeado e uma rede interna exclusivos, gera dois vídeos artificiais dentro do volume e reserva somente `127.0.0.1:8767` no host. Consulta a API HTTP pelo loopback do container, pois algumas versões do Docker Desktop bloqueiam portas publicadas em redes internas. Não monta nem consulta a coleção original e não usa os servidores das portas `8765` e `8766`. Ao terminar, remove somente os recursos que criou.

Verifica categorias editadas, lixeira, fontes, capa armazenada, área de modelos e arquivos de trabalho após reimportar, desligar, ligar e recriar o container. A restauração deve recuperar a data original, a transcrição, o método e as categorias escolhidas. Também cria uma tarefa pendente e cancela outra para conferir sua persistência. O worker fica desabilitado: isso não testa processamento, downloads ou transcrição real.

A última etapa usa `--network none` e o cliente de testes do FastAPI dentro do container para conferir interface, fontes locais, busca com acentos, segmentos, downloads, capa, lixeira e fila. A rede interna impede acesso externo nas demais etapas. Dez arquivos sintéticos são comparados por SHA-256; o teste também confirma que nenhum modelo foi baixado nem começou um trabalho de transcrição. O relatório fica em `/tmp/youtube-catalog-volume-report.json`.

## Exercícios de resiliência

`browser-update.cjs` verifica a identidade rw / ai, fontes locais, contraste, seis tamanhos de janela e os fluxos de exclusão, cancelamento e restauração. As alterações só são permitidas em um segundo servidor descartável na porta `8766`, com a mesma imagem e fontes montadas para leitura, mas uma pasta de dados temporária. O catálogo principal na porta `8765` recebe apenas consultas.

```sh
CATALOG_URL=http://127.0.0.1:8765 TEST_CATALOG_URL=http://127.0.0.1:8766 TEST_SOURCE_DIR="$(cd ../yt-transcripts && pwd)" node tests/browser-update.cjs
```

Use `UPDATE_TEST_MODE=brand` para verificar apenas a aparência do catálogo principal, sem precisar do segundo servidor. O relatório fica em `/tmp/youtube-catalog-update-report.json`. O roteiro completo também confirma que os arquivos de origem permanecem idênticos após excluir e restaurar um vídeo.

`python3 tests/docker-lifecycle.py` cria seu próprio container temporário na porta `8766`. Execute-o com essa porta livre. Ele verifica categorias e exclusões após atualização, parada, reinício e recriação; restaura o vídeo com suas preferências e exercita a leitura com o container sem rede.

Os testes isolados do servidor devem usar uma pasta temporária de fontes e outra de dados. Nessa coleção descartável, verificar importação incremental, fontes incompletas, JSON inválido, remoção e retorno de vídeos, persistência de categorias após reimportação, sobreposição de varreduras e falhas de download de capas. Esses cenários não devem criar ou modificar arquivos em `yt-transcripts`.

Na integração Docker, parar e iniciar o serviço e repetir o aceite; depois recriar o container sem remover a pasta persistente e repetir. Com o aplicativo já carregado e as capas em cache, cortar o acesso externo e confirmar navegação, filtros, busca, leitura e downloads locais. O aceite HTTP não prova isoladamente que a rede externa está indisponível, nem substitui a inspeção visual e por teclado da interface.
