# Funcionamento detalhado

Referência técnica do pipeline, na ordem em que a análise acontece. Visão geral, instalação e configuração estão no [README](../README.md).

```text
validar entrada -> coletar (Blockscout + RPC) -> resolver ABI -> decodificar
  -> código-fonte (explorer + repositórios) -> diagnóstico de falha
  -> agente LLM (ferramentas de leitura) -> validação da saída -> resposta
```

## Coleta de dados (Blockscout e RPC)

- Consulta Blockscout v2: transação, `/logs`, `/token-transfers` e `/internal-transactions`. O cliente também distingue o endpoint legado de **status** de recibo de um recibo JSON-RPC completo.
- Com RPC configurado, consulta `eth_chainId`, `eth_getTransactionByHash` e `eth_getTransactionReceipt`. O cliente oferece `eth_call`, `eth_getBalance`, `eth_getCode` e `eth_getBlockByNumber` com referência explícita de bloco, usados no diagnóstico de falhas e pelas ferramentas do agente.
- Valores nativos são convertidos com inteiros e strings, sem arredondamento de ponto flutuante. Totais de tokens preservam o formato bruto do explorer, incluindo decimais e IDs de NFT quando fornecidos.
- Campos complementares são combinados; campos divergentes são omitidos e sinalizados. Divergência de status/bloco resulta em `unknown`. Um retorno RPC nulo não significa automaticamente pendência.
- Os logs do recibo têm prioridade quando disponíveis; os logs do explorer servem como alternativa. Dados de transações diferentes e logs removidos/malformados são descartados. Em falha confirmada, logs/transferências contraditórios não são exibidos como efetivados.
- Cada resposta HTTP externa é limitada por `max_payload_bytes` (após descompressão) e `tool_timeout_seconds`. Há uma única repetição para falhas transitórias de rede, timeout ou HTTP 429/502/503/504. Redirecionamentos não são seguidos.
- Cada coleção tem limites de páginas e itens; páginas repetidas são detectadas e itens idênticos são deduplicados. Uma falha posterior preserva dados já coletados. O prazo total `collection_timeout_seconds` encerra a coleta com resultado parcial. Os timeouts do frontend/proxy cobrem o máximo configurável de 120 segundos.
- Logs registram ID da requisição, método/fonte, duração, sucesso/erro e status HTTP; o resumo da análise registra rede, hash e quantidade de evidências. URLs RPC, credenciais e mensagens brutas de erro do provedor não são registradas.
- Não há assinatura, envio de transação, acesso a carteiras nem inferência de identidade de função por seletor.

## ABI e decodificação

- A ordem de `abi.strategies` é fixa: `explorer` e depois `repository` (veja [Código-fonte e repositórios](#código-fonte-e-repositórios)). Nenhum banco de seletores/assinaturas é consultado.
- O destinatário com calldata e os emissores de logs são resolvidos em paralelo, até `max_abi_contracts`, dentro do prazo restante de `collection_timeout_seconds`. Endereços repetidos são consultados uma vez.
- Cada contrato usa uma única consulta a `GET /api/v2/smart-contracts/{endereço}`, que traz ABI, verificação, tipo de proxy e implementações. Como a resposta também inclui o código-fonte verificado, ela tem o limite próprio `max_contract_payload_bytes`. O endpoint legado `getabi` não é usado porque o Blockscout público aplica a ele um limite de acesso bem menor.
- Em proxies, a ABI do próprio proxy é tentada antes da ABI das implementações (até duas), seguindo a ordem de despacho da EVM. A implementação é a **atual** informada pelo explorer; quando ela é usada, a resposta avisa que pode diferir da vigente no bloco da transação.
- A decodificação é feita em código (`eth-abi`, modo estrito): o seletor precisa corresponder a uma função da ABI e os dados precisam ser compatíveis. Eventos são casados pelo `topic0` e pela quantidade de parâmetros indexados, o que distingue, por exemplo, `Transfer` de ERC-20 e de ERC-721. Parâmetros indexados de tipo referência (`string`, `bytes`, arrays, tuplas) permanecem como hash e são sinalizados.
- Inteiros são strings decimais, bytes são hexadecimais e endereços ficam em minúsculas. Arrays acima de 50 itens e textos acima de 2000 caracteres são truncados e marcados.
- Evidências novas: `explorer.abi.<endereço>` (resumo da ABI e URL da consulta), `explorer.contract.<proxy>` (metadados do proxy, quando a implementação foi usada), `decoder.input` e `decoder.logs` (confiança `decoded`). Os `evidence_ids` dos dados decodificados encadeiam decodificador, ABI e dado bruto.
- **Chamadas agrupadas (`multicall`):** quando a função decodificada tem um argumento `bytes[]` cujos elementos são chamadas válidas para a mesma ABI (o contrato executa as chamadas em si mesmo via `delegatecall`), cada elemento é decodificado, um nível apenas e em modo estrito, em `decoded_input.nested_calls`, na ordem. Elementos que não decodificam aparecem com seletor e motivo; um `bytes[]` sem nenhuma chamada reconhecida (por exemplo, assinaturas) é ignorado. O resumo lista as chamadas e as incertezas avisam que, sem trace, não se sabe qual delas reverteu.
- Sem ABI, com seletor ausente da ABI, dados incompatíveis, limite de contratos ou prazo esgotado, os dados brutos continuam na resposta e `uncertainties` explica o motivo e o que seria necessário. Em transação com falha confirmada, somente a calldata é decodificada.

## Código-fonte e repositórios

- O alvo é o contrato cuja ABI decodificou a chamada (a implementação, no caso de proxy). Sem decodificação, só há busca se `repositories[].contracts` nomear o endereço.
- **Código verificado do explorer primeiro:** os arquivos já vêm na mesma consulta usada para a ABI, sem requisições extras, e correspondem ao bytecode verificado.
- **Repositórios configurados em seguida:** a branch é resolvida para um commit SHA (`git/ref`), a árvore é listada uma vez e só são baixados arquivos `<Contrato>.sol` (caminhos de teste, mocks, scripts e `lib/` têm prioridade menor). A declaração `contract <Nome>` é conferida no conteúdo. Nada é compilado nem executado.
- A busca segue a herança em largura, a partir do contrato mais derivado, em lotes paralelos e até 16 arquivos por contrato. Trechos: declaração do contrato, função chamada (com NatSpec, até duas sobrecargas), modificadores aplicados, erros customizados de `revert`/`require` e funções internas chamadas diretamente. Não há recursão além desse primeiro nível.
- O total de caracteres respeita `max_repository_excerpt_chars`; com explorer e repositório, cada um recebe metade. Trechos cortados são marcados.
- Fallback de ABI pelo repositório: com o nome do contrato (do explorer ou de `contracts`), procura artefatos compilados (`out/`, `artifacts/`, `abi/`, `build/`, `deployments/`) com `<Contrato>.json`. A resposta avisa que o artefato foi associado por nome, sem confirmação de bytecode.
- Citações: `explorer.source.<endereço>`, `repository.<dono>/<repo>:<caminho>` e `repository.abi.<endereço>`. Cada trecho traz caminho, linhas, commit e um permalink `blob/<commit>/<caminho>#Lx-Ly`.
- Commits ficam em cache por 5 minutos; árvores e arquivos, por commit (imutáveis), em memória do processo. Arquivos e árvore usam o limite `max_contract_payload_bytes`. A busca usa o prazo restante de `collection_timeout_seconds`.
- Erros do GitHub (403/limite, 404, árvore truncada, limite de arquivos) viram incertezas específicas; o contexto do explorer é mantido. Código e comentários são tratados como evidência não confiável e só são exibidos como texto.
- Limitações: a branch atual pode diferir do código implantado (o código verificado prevalece); a associação ao repositório é por nome de arquivo; herança fora do repositório (dependências em submódulos) não é seguida; a linearização é aproximada pela busca em largura.

## Agente LLM

- **Determinístico primeiro:** coleta, decodificação e trechos de código rodam antes. O modelo recebe um pacote compacto (status, campos, função e eventos decodificados, transferências, trechos de código, limitações e a lista de evidências com ids), limitado por `max_llm_context_chars`, sem os payloads brutos.
- **Interface de provedor:** `LLMProvider.start(...)` devolve uma sessão que mantém o histórico no formato nativo do provedor (o Gemini exige devolver as *thought signatures*; a OpenAI, os itens de raciocínio criptografados). Só `backend/app/llm/` importa os SDKs `openai` e `google-genai`, carregados sob demanda; outro provedor é um novo adapter, sem mudar o agente.
- **OpenAI:** usa a Responses API com `tool_choice: required`, chamadas paralelas e `store: false` (nada fica guardado na OpenAI; a sessão reenvia o próprio histórico, incluindo `reasoning.encrypted_content`). `thinking_level` vira `reasoning.effort`; `temperature` só é enviada se configurada (modelos de raciocínio não a aceitam). Tokens de raciocínio são separados da saída para o custo. `prompt_cache_key` fixo permite reaproveitar o prefixo (instruções e ferramentas) entre análises. Erros viram códigos seguros; `429` por falta de créditos aparece como `llm_quota_exceeded`. O SDK repete até três vezes 408/409/429/5xx e falhas de conexão.
- **Loop explícito, sem framework:** a cada passo o modelo pede ferramentas ou envia `submit_analysis`. Os dois provedores rodam em modo de chamada de ferramenta obrigatória, com execução automática do SDK desativada; o harness executa as ferramentas em paralelo e devolve resultados estruturados. Limites: `max_agent_steps` (o último passo pede o envio), `agent_timeout_seconds`, `tool_timeout_seconds`, `max_tool_result_chars`, `max_output_tokens` (inclui tokens de raciocínio) e `llm.timeout_seconds` (`llm.thinking_level`, opcional, reduz o raciocínio em modelos compatíveis; o exemplo usa `low`), com até três novas tentativas do SDK (espera crescente) para 429/5xx. Chamadas repetidas e ferramentas desconhecidas são recusadas.
- **Transferências simples:** sem calldata, logs, transferências, chamadas internas ou destinatário contrato (`transaction.recipient_is_contract`, informado pelo explorer), só `submit_analysis` é oferecida e a resposta sai em um passo.
- **Ferramentas:** `get_contract_info` (metadados, proxy e assinaturas da ABI), `get_contract_source` (função, modificador, erro, evento ou contrato, seguindo a herança; explorer e depois repositórios). Com RPC configurado **e** rede confirmada: `read_contract` (somente funções `view`/`pure` da ABI verificada, via `eth_call`) e `get_native_balance`, no bloco anterior (`before`), do próprio bloco (`after`) ou `latest`. Cada resultado vira evidência `tool.<n>.<ferramenta>` citável; trechos de código pedidos pelo agente também aparecem em `contract_context`.
- **Resposta validada:** `submit_analysis` segue um schema (resumo, achados rotulados como observado/decodificado/estado/código/inferência, causas prováveis, próximos passos, notas de segurança e incertezas). Envio inválido volta ao modelo com os campos com erro. Ids de evidência inexistentes são removidos; itens sem nenhuma evidência válida são descartados e contados em `discarded_items`.
- **Separação de fatos e inferência:** a explicação fica em `explanation`, ao lado do resultado determinístico, que não é alterado. Sem provedor, com erro, limite de passos ou prazo esgotado, `explanation` é `null` e `explanation_issue` diz o motivo (`llm_not_configured`, `llm_api_key_missing`, `llm_rate_limited`, `agent_step_limit`, `llm_timeout`…).
- **Prompt injection:** o prompt de sistema declara que código, comentários, revert strings, nomes de tokens e textos de repositório são dados não confiáveis; o pacote vai delimitado por `<evidence>`. O modelo não acessa rede nem executa nada: só pede ferramentas fixas, de leitura, com argumentos validados.
- **Observabilidade:** cada passo registra provedor, modelo, ferramentas, tokens e duração (entrada, saída, raciocínio); o final registra passos, chamadas, itens descartados, custo estimado (se houver preços configurados) e duração. Conteúdo de prompts, respostas e chaves não é registrado.
- **Modos:** `developer`, `support` e `auditor` mudam só o foco e a linguagem da explicação, não as evidências.

## Diagnóstico de falhas

Executado só quando o status confirmado é `failed`, antes do agente:

- **Repetição via RPC:** com RPC configurado e rede confirmada, a transação é repetida com `eth_call` (mesmos `from`, `to`, `data`, `value` e limite de gas) no estado ao fim do bloco anterior. O resultado vira a evidência `rpc.replay` e `diagnosis.replay` registra `reverted`, `succeeded`, `unavailable` ou `not_attempted`. Mensagens de erro do provedor são apenas classificadas (revert, falta de gas ou estado histórico indisponível) e nunca retornadas nem registradas.
- **Motivo do revert (`diagnosis.revert`):** os bytes da repetição são decodificados em código: `Error(string)`, `Panic(uint256)` (com o significado do código, ex.: `0x11` overflow) ou erro customizado das ABIs obtidas, incluindo as dos contratos chamados internamente. Seletores que colidem são sinalizados. Sem bytes decodificáveis, usa-se o `revert_reason` do Blockscout (já decodificado ou bruto) ou o resultado `out of gas` do explorer.
- **Onde o erro nasce:** no código verificado dos contratos envolvidos, são localizados a declaração do erro customizado e as funções que o lançam (`revert Nome(` ou `require(..., Nome(...))`), ou o `require` cuja mensagem literal coincide; comentários são ignorados. Os trechos entram em `contract_context` com os motivos `error_declaration` e `revert_site`.
- **Leituras de estado:** em `transfer` e `transferFrom` de tokens, `balanceOf` e `allowance` são lidos no bloco anterior (`diagnosis.state_reads`, evidências `rpc.state.<n>`); valor menor que o exigido vira causa provável.
- **Hipóteses com evidência:** repetição bem-sucedida com gas esgotado aponta falta de gas; sem gas esgotado, aponta mudança de estado por uma transação anterior no mesmo bloco. Tudo o que é inferência vai para `likely_causes`; `confirmed` traz só o que o explorer ou o nó reportaram.
- **Limitações:** o estado ao fim do bloco anterior não inclui as transações anteriores do mesmo bloco; RPCs públicos guardam só estado recente (sem nó de arquivo, transações antigas ficam com `replay: unavailable` e dependem do explorer); sem trace, o contrato exato que reverteu numa chamada aninhada é inferido pelo erro.
- O agente recebe o diagnóstico completo e a ferramenta `read_contract` passa a distinguir chamada revertida (`call_reverted`) de estado histórico indisponível.

## Confiabilidade e defesas contra prompt injection

- **Prompt injection:** todo conteúdo externo (comentários e NatSpec, mensagens de revert, argumentos de texto decodificados, símbolos de token, resultados de ferramentas) é dado, nunca instrução. O pacote enviado ao modelo é JSON delimitado por `<evidence>`; qualquer `<evidence`/`</evidence` vindo de fontes externas é escapado (`<`), então não pode fechar o bloco. Um detector determinístico (inglês e português) marca textos que parecem instruções para IA ("ignore previous instructions", "system prompt", "desconsidere as instruções"...): a análise ganha um ponto de atenção em `security_notes` com as evidências de origem, e resultados de ferramentas com esse texto chegam ao modelo com um campo `warning`. O detector apenas sinaliza; nada é executado ou removido.
- **Verificação da saída da IA:** além de exigir citações existentes, achados, causas e notas que mencionem um endereço (20 bytes) ou hash (32 bytes) ausente de todas as evidências e resultados de ferramentas são descartados. No texto livre (resumo, passos, incertezas), esses identificadores ficam listados em `explanation.unverified_identifiers` e a interface mostra um aviso.
- **Integridade das citações:** ao final de cada análise, ids de evidência que não correspondem a uma fonte retornada são removidos e contados (`dangling_citations` no log; esperado 0). Os testes verificam a invariante em cenários de sucesso, ABI ausente, falha diagnosticada e com agente.
- **Erros inesperados:** respondem `500` com `{"error": {"code": "internal_error", ...}}` e o `request_id` (também no cabeçalho `X-Request-ID`); o log registra só o tipo da exceção, nunca a mensagem.
- **Observabilidade:** cada análise termina com uma linha de log com `request_id`, rede, hash, modo, status, duração, evidências, incertezas, motivos de degradação (`degraded=explorer.logs:timeout,...`, códigos da aplicação), entradas sinalizadas, citações removidas, resultado da explicação, passos e custo estimado do LLM. Chamadas HTTP e passos do agente já registravam ferramenta, duração, status HTTP e tokens.
- **Já existentes:** timeouts por chamada e prazo total, repetição única para 429/5xx/rede, limites de tamanho de resposta, retries do SDK do LLM, segredos fora de respostas e logs, e resultados parciais quando qualquer fonte ou o modelo falha.

## Avaliação de modelos

Um conjunto fixo de transações permite medir a qualidade das explicações e comparar modelos sobre exatamente as mesmas evidências.

- **Casos (`evals/cases.yaml`):** nove transações da Ethereum Mainnet cobrindo os oito cenários de referência — transferência nativa, ERC-20, falha com revert decodificado, falha que exige raciocínio sobre estado, lógica de negócio de repositório (USDC via proxy), ABI ausente, falha parcial de fonte (sem RPC) e interação com vários contratos — mais falta de gas. Cada caso define o modo de análise e as expectativas.
- **Snapshots (`evals/snapshots/`):** a análise determinística de cada caso é congelada sem LLM. Assim, mudanças no estado do RPC público (repetição indisponível com o tempo) ou no explorer não alteram a entrada dos modelos. Os três casos de falha vêm de [`examples/collection/`](../examples/collection/), coletados quando a repetição ainda era possível. As ferramentas do agente continuam consultando explorer, RPC e repositórios ao vivo.
- **Pontuação determinística (sem LLM juiz):** menções esperadas (grupos de termos, sem diferenciar maiúsculas e acentos), citações exigidas por prefixo de evidência (`rpc.replay`, `repository.`…), `grounded` (nenhum item descartado nem identificador não verificado), causa provável nas falhas, orçamento de ferramentas e termos proibidos (ex.: "executada com sucesso" numa falha). A pontuação do caso é a fração de verificações aprovadas; execuções sem explicação contam como zero.
- **Métricas por modelo:** explicações produzidas, pontuação média, casos 100%, descartes, identificadores não verificados, passos, ferramentas, tokens, latência, custo estimado (preços em `evals/models.yaml`, que é configuração) e falhas por código.
- **Execução:**

```bash
python backend/scripts/evaluate.py collect             # cria snapshots ausentes (--refresh recria)
python backend/scripts/evaluate.py run                 # todos os modelos de evals/models.yaml
python backend/scripts/evaluate.py run --models gemini:gemini-3.8-flash --cases erc20-transfer
python backend/scripts/evaluate.py run --retry-failed evals/results/<pasta>   # refaz falhas transitórias
```

  Use `PYTHONPATH=backend` e o Python do `.venv`, com a chave do provedor no `.env`. O resultado vai para `evals/results/<data>-<label>/` (`results.json` e `report.md`). `--delay` espaça as chamadas e falhas transitórias (limite de uso, sobrecarga, timeout) são repetidas uma vez; `--retry-failed` completa depois só o que falhou por limite do provedor.
- **Comparação de modelos:** os candidatos estão em `evals/models.yaml` (`gpt-5-mini` e modelos Gemini Flash/Flash-Lite, preços conferidos em 2 de outubro de 2026). A comparação completa entre modelos ficou fora do escopo: a cota do nível gratuito do Gemini interrompeu a rodada após os primeiros casos, e o padrão adotado passou a ser `gpt-5-mini` (OpenAI), também listado em `evals/models.yaml`. Numa execução parcial, o Gemini 3.8 Flash explicou os seis primeiros casos sem nenhum item descartado ou identificador não verificado, com 1–2 passos e US$ 0,003–0,02 por análise. Outros provedores (por exemplo, Anthropic) entram na comparação implementando um adapter de `LLMProvider` e adicionando-os a `evals/models.yaml`.

## Contrato HTTP

| Endpoint | Resposta atual |
| --- | --- |
| `GET /health` | `200`, processo pronto, rede ativa, `analysis_available: true` e `explanation_available` (provedor configurado, sem testar a conexão) |
| `GET /api/network` | `200`, ID, nome, chain ID e moeda nativa da configuração |
| `POST /api/analyze` | `422` para entrada inválida; `200` com resultado completo ou parcial para hash válido |

O endpoint de saúde verifica a aplicação e o carregamento da configuração; **não verifica conectividade com explorer, RPC ou LLM**.

```json
{
  "tx_hash": "0x0000000000000000000000000000000000000000000000000000000000000000",
  "mode": "developer"
}
```

O hash acima é apenas uma entrada sintética de formato válido, não uma transação real. Os modos `developer`, `support` e `auditor` mudam o foco da explicação do LLM.

A resposta contém `network`, `tx_hash`, `request_id`, `status`, `summary`, `transaction`, `calls`, `transfers`, `events`, `diagnosis`, `contract_context`, `security_notes`, `sources`, `uncertainties`, `mode`, `explanation` e `explanation_issue`. Erros inesperados retornam `500` com `error.code = "internal_error"` e o `request_id`. O header `X-Request-ID` corresponde ao ID no corpo e nos logs. `transaction.field_sources` e `status_evidence_ids` vinculam os campos normalizados às evidências. `transaction.decoded_input` e `events[].decoded` trazem função/evento, assinatura canônica, argumentos e origem da ABI, ou `null` quando não houve decodificação. As coleções trazem `evidence_ids`; as fontes preservam os dados consultados e a paginação. Citações RPC incluem método, parâmetros e bloco do resultado, sem expor o endpoint.

Mesmo se todos os provedores falharem ou não encontrarem o hash, a API retorna um resultado parcial com `status: unknown`, campos indisponíveis como `null` e instruções para conferir hash, rede e fontes. Isso não confirma que a transação inexiste. Os schemas estão em `backend/app/domain/analysis.py`, `transaction.py` e no OpenAPI.

## Cobertura dos testes

Os testes de backend não acessam serviços externos. Cobrem configuração, validação de hash/modo, clientes HTTP/Blockscout/RPC, paginação, limites, valores exatos, logs, fallback, falhas, resultados nulos, rede RPC divergente, citações, sigilo nos logs e preservação de evidências após timeout. ABI e decodificação: seletores/tópicos canônicos, calldata real de USDT, `uint256` máximo, tuplas, arrays truncados, indexados por hash, layout de evento por quantidade de tópicos, ABI ausente/malformada, limite próprio de payload de contrato, prioridade de estratégias, proxies, cache por endereço, prazo esgotado e limite de contratos. Código e repositórios: extração de símbolos Solidity (comentários/strings com chaves, bases, modificadores, erros, chamadas internas, NatSpec), cliente GitHub com GitHub simulado (token opcional, cache, 403 sem repetição, arquivos inválidos), herança, orçamento de caracteres, limite de arquivos, permalinks, fallback de ABI por artefato e mapeamento, validação de URL/branch e sigilo do token. Adapter OpenAI: formato da requisição (ferramentas, `tool_choice`, `store`, raciocínio), histórico com itens de raciocínio e resultados por `call_id`, texto solto e argumentos JSON inválidos, mapeamento de erros (incluindo falta de créditos) sem vazar mensagens, fábrica e loop completo do agente pelo adapter. Configurações pela UI: seed a partir dos exemplos preservando placeholders e sem sobrescrever edições, leituras sem valores secretos, placeholders mantidos quando o valor não muda, ações de segredo (definir, remover, voltar ao `.env`), erros 422 só com nomes de campo e sem gravação, criar/copiar/ativar/excluir perfis com troca imediata do explorer usado nas análises, ids que não escapam da pasta e teste de conexão (incluindo chain ID divergente). Avaliação: pontuação de cada expectativa, menções sem acento e caixa, termos proibidos, falha de grounding, execução sem explicação e agregação por modelo, validação dos arquivos de casos/modelos e coerência dos snapshots. Defesas: detector de texto com aparência de instrução (positivos e falsos positivos comuns), escape do delimitador de evidências, código-fonte com injeção sinalizado e não obedecido, aviso em resultados de ferramentas, descarte de achados com endereços inventados, remoção de citações órfãs e invariante de citações em vários cenários, erro inesperado sem vazamento e log com motivos de degradação. Diagnóstico: `eth_call` que distingue revert, falta de gas e estado histórico indisponível sem vazar mensagens; decodificação de `Error(string)`, `Panic`, erros customizados, colisões e dados malformados; localização de `revert`/`require` ignorando comentários; diagnóstico com erro customizado repetido, leitura de saldo, motivo do explorer sem histórico, falta de gas, mudança de estado no bloco e ausência de RPC. Agente: adapter Gemini com cliente simulado (declarações, modo obrigatório, histórico com thought signatures, ids de chamada, mapeamento de erros sem vazar mensagens), fábrica de provedores, loop com provedor roteirizado (citações inválidas descartadas, ferramentas viram evidências, `eth_call` no bloco anterior, ferramentas RPC só com rede verificada, erros de ferramenta, texto solto e envio inválido corrigidos, limite de passos, falhas do provedor), pacote de evidências com orçamento e codificação de argumentos de `eth_call`. Decodificação de `multicall`: elementos decodificados pela mesma ABI, elementos fora da ABI e `bytes[]` sem chamadas.
