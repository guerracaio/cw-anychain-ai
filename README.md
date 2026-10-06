# Anychain Transaction Assistant

Assistente local que explica e diagnostica transações EVM a partir de evidências verificáveis. Você informa o hash de uma transação e recebe:

- o que aconteceu: status, remetente, destino, valor, função e argumentos decodificados, transferências, eventos e chamadas internas;
- por que falhou, quando falhou: motivo do revert, onde ele nasce no código e leituras de estado;
- por que o contrato se comporta assim: trechos do código verificado e dos repositórios configurados, com commit, caminho e linhas;
- uma explicação em linguagem natural, escrita por um LLM que cita as evidências usadas e separa fatos de inferências.

A ferramenta é de análise e depuração: não assina nem envia transações. É **agnóstica de rede**. Explorer, RPC, repositórios e modelo vêm de configuração, e trocar de rede (Ethereum, Sepolia, Base, uma rede privada compatível com EVM) não exige mudar código.

## Arquitetura

```text
Blockscout   -> o que aconteceu: transação, recibo, logs, transferências, ABI, código verificado
RPC          -> estado da blockchain: eth_call, saldos, repetição da transação no bloco anterior (somente leitura)
Repositórios -> por que o contrato se comporta assim: Solidity, modificadores, erros, herança (GitHub)
LLM          -> raciocina sobre as evidências e explica, citando-as; nunca é a fonte dos fatos
```

O pipeline é **determinístico primeiro, agêntico depois**:

```text
validar hash -> coletar (Blockscout + RPC) -> resolver ABI -> decodificar calldata e eventos
  -> trechos de código (explorer + repositórios) -> diagnóstico de falha
  -> agente LLM com ferramentas de leitura -> validação das citações -> resposta estruturada
```

Tudo o que o código faz de forma confiável (requisições, conversão de valores, decodificação ABI, busca no repositório, motivo do revert) acontece antes do LLM. O modelo recebe um pacote compacto de evidências com ids, pode pedir mais (código-fonte, leituras `view` via `eth_call`, saldos) e precisa citar ids existentes em cada afirmação. Afirmações sem evidência válida ou com endereços que não aparecem em nenhuma fonte são descartadas. Sem LLM configurado, ou se ele falhar, a resposta traz só a parte determinística e explica o motivo.

```text
backend/app/api/          endpoints HTTP (análise e configurações)
backend/app/config/       modelos Pydantic, carregamento de YAML e perfis editáveis pela UI
backend/app/domain/       schemas da resposta, independentes de provedor
backend/app/explorer/     cliente Blockscout
backend/app/blockchain/   cliente RPC, normalização, resolução de ABI e decodificação
backend/app/repositories/ cliente GitHub, busca seletiva e extração de símbolos Solidity
backend/app/services/     orquestração da análise, diagnóstico, contexto de código e defesas
backend/app/llm/          interface de provedor e adapters OpenAI e Gemini (únicos com SDKs de LLM)
backend/app/agent/        prompts, ferramentas, schema da resposta e loop de ferramentas
backend/tests/            testes com serviços externos simulados
backend/scripts/          análise ao vivo pela linha de comando e avaliação de modelos
frontend/                 Next.js + React + Tailwind
config/                   exemplos de redes
evals/                    casos fixos, snapshots e modelos da avaliação
examples/                 análises reais documentadas
docs/funcionamento.md     referência técnica detalhada
```

Backend em Python 3.12+ (FastAPI, Pydantic, `httpx`, `eth-abi`); frontend em Next.js, React, TypeScript e Tailwind. Não há banco de dados, fila nem índice vetorial: a aplicação não guarda estado, exceto os perfis de rede editados pela interface (arquivos locais).

## Início rápido com Docker

Requer Docker com Compose v2.24 ou superior.

```bash
cp .env.example .env      # opcional: RPC_URL, GITHUB_TOKEN, LLM_PROVIDER, LLM_MODEL e a chave do provedor
docker compose up --build
```

Abra <http://127.0.0.1:3000>. Sem `.env`, a rede é a Ethereum Mainnet com o Blockscout público, sem RPC e sem explicação por IA. A análise funciona, informando o que ficou indisponível.

- Só o frontend é publicado, em `127.0.0.1:3000` (outra porta: `FRONTEND_PORT=3200 docker compose up`). Ele encaminha `/api/*` para o backend pela rede interna do Compose.
- Os perfis criados pela interface ficam no volume `profiles`. `docker compose down -v` apaga esse volume e volta aos exemplos.
- **Para editar configurações pela interface no Docker, defina `ADMIN_TOKEN` no `.env`.** Sem ele, a edição só é aceita de loopback, e no Docker as requisições chegam pelo container do frontend. Nesse caso, a página de configurações abre em modo somente leitura.

## Execução local (sem Docker)

Requisitos: Python 3.12+, Node.js 20.9+ e npm.

**Windows / PowerShell**, na raiz do projeto:

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install -e './backend[dev]'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --reload-dir backend/app --host 127.0.0.1 --port 8000
```

Em outro terminal:

```powershell
cd frontend
npm.cmd ci
npm.cmd run dev
```

Usar `npm.cmd` e o Python da `.venv` diretamente dispensa mudar a política de execução do PowerShell.

**Linux / macOS:**

```bash
cp .env.example .env
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.lock
.venv/bin/python -m pip install -e './backend[dev]'
.venv/bin/python -m uvicorn app.main:app --app-dir backend --reload --reload-dir backend/app --host 127.0.0.1 --port 8000
# em outro terminal
cd frontend && npm ci && npm run dev
```

- Interface: <http://127.0.0.1:3000>
- Saúde do backend: <http://127.0.0.1:8000/health>
- Documentação interativa da API: <http://127.0.0.1:8000/docs>

O frontend encaminha `/api/*` para `BACKEND_URL` (padrão `http://127.0.0.1:8000`). Para mudar o destino, copie `frontend/.env.local.example` para `frontend/.env.local`. O endereço é resolvido no build e não é exposto ao navegador.

## Configuração

Uma rede é um arquivo YAML. Exemplos: [`config/ethereum-mainnet.example.yaml`](config/ethereum-mainnet.example.yaml) e [`config/sepolia.example.yaml`](config/sepolia.example.yaml).

```yaml
network:
  id: ethereum-mainnet
  name: Ethereum Mainnet
  chain_id: 1
  native_currency: ETH
  native_decimals: 18
explorer:
  type: blockscout
  base_url: https://eth.blockscout.com
rpc:
  url: ${RPC_URL:-}
repositories:
  - url: https://github.com/circlefin/stablecoin-evm # USDC / EURC (FiatToken)
    branch: master
  - url: https://github.com/Uniswap/universal-router
    branch: main
    # Opcional: nomes para contratos que o explorer não conhece (não verificados, redes privadas)
    # contracts:
    #   - address: "0x..."
    #     name: MeuContrato
github:
  token: ${GITHUB_TOKEN:-}
abi:
  strategies: [explorer, repository]
llm:
  provider: ${LLM_PROVIDER:-}  # openai | gemini
  model: ${LLM_MODEL:-}
  thinking_level: ${LLM_THINKING_LEVEL:-low}
  input_price_per_million: ${LLM_INPUT_PRICE_PER_MILLION:-}
  output_price_per_million: ${LLM_OUTPUT_PRICE_PER_MILLION:-}
analysis:
  max_explorer_pages: 3
  max_collection_items: 100
  max_abi_contracts: 10
  collection_timeout_seconds: 25
  max_agent_steps: 10
  max_output_tokens: 8192
  agent_timeout_seconds: 90
  max_llm_context_chars: 60000
  max_tool_result_chars: 8000
  tool_timeout_seconds: 15
  max_payload_bytes: 65536
  max_contract_payload_bytes: 2097152
  max_repository_excerpt_chars: 12000
  include_security_notes: true
```

`${VAR}` exige uma variável não vazia e `${VAR:-valor}` define um padrão. A expansão acontece depois do parsing do YAML, então valores do ambiente não criam campos. Campos desconhecidos, URLs inválidas e limites fora do intervalo impedem a inicialização com um erro que cita só os nomes dos campos, nunca os valores. O `explorer.base_url` é a raiz pública do Blockscout, sem `/api/v2`. Antes de usar o RPC, a aplicação confere `eth_chainId`; um endpoint de outra rede é descartado com aviso.

### Variáveis de ambiente (`.env`)

| Variável | Uso |
| --- | --- |
| `APP_CONFIG` | YAML da rede inicial (padrão: `config/ethereum-mainnet.example.yaml`) |
| `RPC_URL` | Endpoint RPC, opcional (ex.: `https://ethereum-rpc.publicnode.com`, público, só estado recente). Pode conter credenciais; nunca é exposto nem registrado |
| `GITHUB_TOKEN` | Opcional. Sem ele, o GitHub limita a 60 requisições por hora, o que algumas análises com repositório podem esgotar |
| `LLM_PROVIDER`, `LLM_MODEL` | `openai` ou `gemini` e o modelo (ex.: `gpt-5-mini`, `gemini-3.8-flash`). Vazios desativam a explicação |
| `LLM_THINKING_LEVEL` | Esforço de raciocínio: `minimal`, `low` (padrão), `medium`, `high` ou `none` para modelos sem raciocínio |
| `LLM_INPUT_PRICE_PER_MILLION`, `LLM_OUTPUT_PRICE_PER_MILLION` | Opcionais: preços em US$ por 1M tokens, usados só no custo estimado |
| `OPENAI_API_KEY`, `GEMINI_API_KEY` | Chave do provedor escolhido; nunca é exposta nem registrada |
| `ANTHROPIC_API_KEY` | Reservada para um adapter futuro |
| `ADMIN_TOKEN` | Opcional. Quando definido, é exigido para alterar configurações pela interface; sem ele, alterações só de loopback |

Modelo padrão recomendado: `gpt-5-mini` com `thinking_level: low`. Nas análises de [`examples/`](examples/), cada explicação custou menos de US$ 0,01.

### Trocar de rede e editar pela interface

A página **Configurações** (ícone de engrenagem) gerencia **perfis de rede** sem reiniciar o backend:

- Cada perfil é um YAML completo em `config/local/profiles/<id>.yaml`, ignorado pelo Git, e o perfil ativo fica em `config/local/state.json`.
- Na primeira execução, os arquivos `config/*.yaml` e o de `APP_CONFIG` são copiados literalmente, com os placeholders `${...}`. Depois disso, os perfis locais passam a ser a fonte da configuração; apagar `config/local/` volta ao estado inicial.
- Pela interface é possível editar rede, explorer, RPC, repositórios (com mapeamentos endereço → contrato), token do GitHub e LLM; criar, duplicar, ativar e excluir perfis; e **testar a conexão** (explorer, chain ID do RPC, branch de cada repositório e configuração do LLM).
- A validação usa as mesmas regras do YAML e a gravação é atômica. Ativar um perfil vale para as próximas análises.
- **Segredos** (RPC, token do GitHub e chave do LLM) são só de escrita: a interface mostra de onde vêm (arquivo local ou variável de ambiente), nunca o valor. Campos ligados a variáveis aparecem como “via VAR” e mantêm o placeholder quando não mudam.
- **Acesso:** leitura liberada, sem segredos. A escrita exige `X-Admin-Token` quando `ADMIN_TOKEN` está definido; sem ele, só de loopback. **Defina `ADMIN_TOKEN` sempre que outras pessoas puderem acessar a interface.**

Por arquivo, sem interface: crie outro YAML com a mesma estrutura e aponte `APP_CONFIG` para ele. Isso só vale antes da primeira execução ou depois de apagar `config/local/`.

Exemplo de outra rede pronta para cadastrar pela interface: Base (`chain_id` 8453, explorer `https://base.blockscout.com`, RPC público `https://mainnet.base.org`, repositório `circlefin/stablecoin-evm` na branch `master`).

## Exemplos

[`examples/README.md`](examples/README.md) traz três análises reais completas, com explicação, evidências citadas, custo e uma nota de revisão:

| Caso | Foco | Destaques |
| --- | --- | --- |
| [Transferência ERC-20 (USDT)](examples/README.md#1-transferência-erc-20--foco-suporte) | suporte | Resolvida em um passo, sem ferramentas; ~US$ 0,003 |
| [Falha “Price slippage check” (Uniswap V3)](examples/README.md#2-transação-com-falha--foco-desenvolvedor) | desenvolvedor | `multicall` decodificado em `mint` + `refundETH`, `require` localizado no código, causa provável com `amount1Min`; RPC sem histórico declarado como limitação |
| [USDC via proxy com repositório](examples/README.md#3-lógica-de-negócio-do-repositório--foco-auditoria) | auditoria | Código verificado + `circlefin/stablecoin-evm` com commit e linhas; pausa e blacklist como pontos de revisão |

Para analisar pela linha de comando com qualquer configuração:

```bash
PYTHONPATH=backend .venv/bin/python backend/scripts/smoke_analysis.py <hash> --mode developer \
  --config config/ethereum-mainnet.example.yaml --output resultado.json
```

## API

| Endpoint | Resposta |
| --- | --- |
| `POST /api/analyze` | `{"tx_hash": "0x…", "mode": "developer" \| "support" \| "auditor"}`. Retorna `200` com resultado completo ou parcial, ou `422` para entrada inválida |
| `GET /api/network` | Dados públicos da rede ativa |
| `GET /health` | Processo pronto, rede ativa e se há LLM configurado (não testa conexões) |
| `/api/settings/...` | Perfis de rede (veja a seção anterior) |

A resposta contém `network`, `status`, `summary`, `transaction` (com `decoded_input`), `calls`, `transfers`, `events`, `diagnosis` (`confirmed`, `likely_causes`, `next_steps`, `revert`, `replay`, `state_reads`), `contract_context`, `security_notes`, `sources`, `uncertainties`, `explanation` e `explanation_issue`. Cada item aponta para `evidence_ids` presentes em `sources`. A rede vem da configuração do servidor, não do corpo da requisição. Os detalhes estão em [`docs/funcionamento.md`](docs/funcionamento.md#contrato-http) e no OpenAPI (`/docs`).

Os focos mudam só a apresentação: **desenvolvedor** (calldata, funções, reverts, estado, código e passos de depuração), **suporte** (linguagem simples, impacto e próximos passos) e **auditoria** (controle de acesso, premissas de confiança e padrões suspeitos). As evidências são as mesmas nos três.

## Segurança

- Todo conteúdo externo (comentários e NatSpec, mensagens de revert, strings decodificadas, símbolos de token, resultados de ferramentas) é tratado como dado, nunca como instrução. O pacote enviado ao modelo é delimitado, e tentativas de fechar o delimitador são escapadas. Textos com aparência de instrução para IA são sinalizados em `security_notes`.
- O modelo não acessa rede: só pede ferramentas fixas, de leitura, com argumentos validados. `read_contract` aceita apenas funções `view`/`pure` da ABI verificada.
- Não há chaves privadas, assinatura nem envio de transações, e nenhum código de repositório é executado.
- Os repositórios consultados são só os configurados. Todas as chamadas externas têm timeout, limite de tamanho e no máximo uma repetição para falhas transitórias.
- Segredos não aparecem em respostas nem em logs. Erros inesperados retornam `500` com um `request_id`, e o log registra só o tipo da exceção.

Os logs registram, por análise: `request_id`, rede, hash, modo, status, duração, cada chamada externa (fonte, duração, status HTTP), passos e tokens do LLM, custo estimado, motivos de degradação e citações removidas.

## Testes e verificação

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
.\.venv\Scripts\python.exe -m ruff check backend
.\.venv\Scripts\python.exe -m ruff format --check backend
cd frontend
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run build
```

Em Linux/macOS, use `.venv/bin/python` e `npm`. Os 250 testes do backend não acessam serviços externos. Eles cobrem configuração, clientes, parsing, decodificação, repositórios, diagnóstico, adapters de LLM, loop do agente, defesas contra prompt injection, configurações pela interface e avaliação ([cobertura completa](docs/funcionamento.md#cobertura-dos-testes)).

A avaliação de modelos usa 9 transações fixas, snapshots das evidências e pontuação determinística, sem LLM juiz: `python backend/scripts/evaluate.py run`. Veja [Avaliação de modelos](docs/funcionamento.md#avaliação-de-modelos).

## Limitações

- **ABI:** sem ABI verificada no explorer nem artefato no repositório, a função não é identificada. O seletor de 4 bytes nunca é tratado como identidade da função, e calldata, logs e transferências são preservados em formato bruto. Codificações de segundo nível que a ABI não descreve (como os `commands` do Universal Router) continuam como bytes, e `multicall` é aberto em apenas um nível.
- **Estado histórico:** RPCs públicos guardam só estado recente. Sem nó de arquivo, a repetição da transação no bloco anterior e as leituras de estado de transações antigas ficam indisponíveis, e o motivo da falha passa a depender do explorer. O estado “ao fim do bloco anterior” não inclui transações anteriores do mesmo bloco.
- **Traces:** sem `debug_trace*`, as chamadas internas vêm do indexador do Blockscout, quando disponíveis. Numa chamada aninhada, o contrato que reverteu é inferido pelo erro.
- **Proxies:** a implementação usada é a atual informada pelo explorer, que pode diferir da vigente no bloco. A resposta avisa.
- **Dependência de provedores:** a disponibilidade, os limites de uso e a qualidade dos dados do Blockscout, do RPC, do GitHub (60 requisições por hora sem token) e do LLM limitam a análise. Falhas geram resultados parciais com incertezas explícitas.
- **Cobertura dos repositórios:** a associação é feita pelo nome do arquivo `<Contrato>.sol` na branch configurada, que pode diferir do código implantado (o código verificado prevalece). Herança em submódulos e dependências externas não é seguida.
- **LLM:** a explicação é uma interpretação e varia entre execuções. As citações são verificadas, mas uma inferência bem citada ainda pode estar errada, e por isso aparece rotulada como inferência ou causa provável. Os provedores implementados são OpenAI e Gemini.
- **Configurações pela interface:** pensadas para uso local por um operador. Não há usuários nem permissões além do `ADMIN_TOKEN`.

Referências: [OpenAPI Blockscout v2](https://github.com/blockscout/blockscout-api-v2-swagger/blob/main/swagger.yaml), [EVM JSON-RPC](https://ethereum.org/developers/docs/apis/json-rpc/), [Next.js](https://nextjs.org/docs) e [FastAPI](https://fastapi.tiangolo.com/).
