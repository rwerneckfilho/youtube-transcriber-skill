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

Uma falha é apresentada com o nome do cenário. O processo encerra com código 1 quando algum cenário falha e 0 quando todos passam. O teste de busca com acentos, o cenário de falantes e os filtros de categoria requerem exemplos presentes na coleção atual.

## Verificação no navegador

`browser-smoke.cjs` usa Playwright com o Google Chrome instalado. Com Playwright acessível ao Node, execute:

```sh
node tests/browser-smoke.cjs
```

O endereço padrão é `http://127.0.0.1:8765`; `CATALOG_URL` aceita outro endereço local. Disponibilize `playwright` e `pngjs` em uma instalação Node de testes, por exemplo com `npm install --no-save playwright pngjs`, ou aponte `NODE_PATH` para uma instalação existente desses pacotes. O Chrome deve estar instalado.

O roteiro verifica início, filtros, paginação, busca, acesso ao trecho encontrado, método, falantes, carregamento progressivo, diretório de canais, teclado e telas de 375 px. Abre e fecha o diálogo de categorias sem salvar. Bloqueia requisições de escrita à API e todo acesso HTTP externo durante a execução; recarrega a interface e repete a leitura nessas condições. Registra erros JavaScript e qualquer tentativa de acesso externo.

As capturas de tela ficam em `/tmp/youtube-catalog-home.png`, `/tmp/youtube-catalog-reader.png` e `/tmp/youtube-catalog-mobile.png`. O teste depende de vídeos classificados, com método, sem método, com falantes e com título acentuado, presentes na coleção inicial.

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

## Exercícios de resiliência

`browser-update.cjs` verifica a identidade rw / ai, fontes locais, contraste, seis tamanhos de janela e os fluxos de exclusão, cancelamento e restauração. As alterações só são permitidas em um segundo servidor descartável na porta `8766`, com a mesma imagem e fontes montadas para leitura, mas uma pasta de dados temporária. O catálogo principal na porta `8765` recebe apenas consultas.

```sh
CATALOG_URL=http://127.0.0.1:8765 TEST_CATALOG_URL=http://127.0.0.1:8766 TEST_SOURCE_DIR="$(cd ../yt-transcripts && pwd)" node tests/browser-update.cjs
```

Use `UPDATE_TEST_MODE=brand` para verificar apenas a aparência do catálogo principal, sem precisar do segundo servidor. O relatório fica em `/tmp/youtube-catalog-update-report.json`. O roteiro completo também confirma que os arquivos de origem permanecem idênticos após excluir e restaurar um vídeo.

`python3 tests/docker-lifecycle.py` cria seu próprio container temporário na porta `8766`. Execute-o com essa porta livre. Ele verifica categorias e exclusões após atualização, parada, reinício e recriação; restaura o vídeo com suas preferências e exercita a leitura com o container sem rede.

Os testes isolados do servidor devem usar uma pasta temporária de fontes e outra de dados. Nessa coleção descartável, verificar importação incremental, fontes incompletas, JSON inválido, remoção e retorno de vídeos, persistência de categorias após reimportação, sobreposição de varreduras e falhas de download de capas. Esses cenários não devem criar ou modificar arquivos em `yt-transcripts`.

Na integração Docker, parar e iniciar o serviço e repetir o aceite; depois recriar o container sem remover a pasta persistente e repetir. Com o aplicativo já carregado e as capas em cache, cortar o acesso externo e confirmar navegação, filtros, busca, leitura e downloads locais. O aceite HTTP não prova isoladamente que a rede externa está indisponível, nem substitui a inspeção visual e por teclado da interface.
