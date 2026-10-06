# Exemplos de análises

Três análises reais, executadas em 5 de outubro de 2026 com a configuração [`config/ethereum-mainnet.example.yaml`](../config/ethereum-mainnet.example.yaml), RPC público e `openai:gpt-5-mini` (`thinking_level: low`). Cada uma usa um foco diferente. Os JSON são as respostas completas de `POST /api/analyze`, com todas as evidências; o texto abaixo foi gerado a partir deles, sem edição, e cada análise termina com uma nota de revisão humana.

| # | Caso | Foco | O que demonstra |
| --- | --- | --- | --- |
| 1 | Transferência ERC-20 (USDT) | suporte | Caso simples resolvido em um passo, sem ferramentas: a decodificação determinística já entrega função, valor e evento |
| 2 | Falha com diagnóstico (Uniswap V3, “Price slippage check”) | desenvolvedor | Motivo do revert, chamadas agrupadas em `multicall` decodificadas, código do `require`, causa provável com o parâmetro exato e degradação explícita quando o RPC não tem estado histórico |
| 3 | USDC atrás de proxy com repositório | auditoria | Implementação do proxy, código verificado **e** do repositório `circlefin/stablecoin-evm` (commit e linhas), notas de segurança como pontos para revisão |

Custos estimados com preços de referência do `gpt-5-mini` (US$ 0,25 / 2,00 por 1M tokens de entrada / saída).

Para repetir (acessa explorer, RPC, GitHub e o LLM configurados no `.env`):

```bash
PYTHONPATH=backend python backend/scripts/smoke_analysis.py <hash> --mode support \
  --config config/ethereum-mainnet.example.yaml --output examples/resultado.json
```

As respostas do modelo variam entre execuções; as evidências e a parte determinística, não.

## 1. Transferência ERC-20 — foco suporte

| Campo | Valor |
| --- | --- |
| Transação | [`0x0efab637…7e3ec9`](https://eth.blockscout.com/tx/0x0efab637e8e1a0503bf7e0e9e28a981bd83827cd2cbf73964938097e4f7e3ec9) |
| Rede / foco | `ethereum-mainnet` / suporte (`support`) |
| Status | `success` |
| Função decodificada | `transfer(address,uint256)` |
| Modelo | `openai:gpt-5-mini` · 1 passo · ferramentas: nenhuma |
| Tokens / tempo do LLM | 3756 entrada · 1174 saída · 11.8 s · ~US$ 0.003 |
| Fundamentação | 0 itens descartados · 0 identificadores não verificados |
| Resultado completo | [`01-erc20-transfer.json`](01-erc20-transfer.json) |

**Resumo determinístico (sem LLM):** A transação foi executada com sucesso segundo as fontes consultadas. Função chamada, decodificada pela ABI: transfer(address,uint256).

**Explicação da IA:** Transação bem-sucedida: um envio de USDT foi realizado do endereço `0x8f8d1206d1bce12ff892731f8a140ed18b93884e` para `0xe0fe6653425be82c3f6f5e6e9142e145771cdd26`. A função chamada foi `transfer(address,uint256)` do contrato `0xdac17f958d2ee523a2206206994597c13d831ec7` (TetherToken). O recibo e os logs mostram um evento `Transfer` correspondente e o valor transferido foi `49190000` (USDT com 6 decimais).

<details><summary>Como a IA chegou a essa conclusão</summary>

- **Observado:** O hash da transação é `0x0efab637e8e1a0503bf7e0e9e28a981bd83827cd2cbf73964938097e4f7e3ec9` e o status foi reportado como bem-sucedido. — `explorer.transaction` `rpc.receipt`
- **Decodificado:** A calldata decodificada corresponde à chamada `transfer(address,uint256)` com argumentos `_to = 0xe0fe6653425be82c3f6f5e6e9142e145771cdd26` e `_value = 49190000`. — `decoder.input` `explorer.abi.0xdac17f958d2ee523a2206206994597c13d831ec7`
- **Observado:** O contrato alvo é `0xdac17f958d2ee523a2206206994597c13d831ec7` (rotulado como USDT/TetherToken) e é um contrato, não uma carteira EOA. — `explorer.transaction` `explorer.source.0xdac17f958d2ee523a2206206994597c13d831ec7`
- **Observado:** Os logs do recibo contêm um evento `Transfer(address,address,uint256)` do mesmo contrato, mostrando `from = 0x8f8d1206d1bce12ff892731f8a140ed18b93884e`, `to = 0xe0fe6653425be82c3f6f5e6e9142e145771cdd26` e `value = 49190000`. — `rpc.logs` `decoder.logs`
- **Decodificado:** Os agregados de transferências de token mostram um token transferido: `token_address = 0xdac17f958d2ee523a2206206994597c13d831ec7`, `token_symbol = USDT`, `decimals = 6`, `value = 49190000`. — `explorer.transfers`
- **Código:** O trecho de código do contrato `TetherToken` mostra que `transfer` aplica o modificador `whenNotPaused` e verifica blacklist; em caso de `deprecated` delegaria para `UpgradedStandardToken`. — `explorer.source.0xdac17f958d2ee523a2206206994597c13d831ec7`
- **Inferência:** A transferência de `49190000` com 6 decimais corresponde a 49.19 USDT (49190000 / 10^6). — `explorer.transfers`

</details>

**Pontos de atenção**

- A função `transfer` do contrato TetherToken tem controles de pausa (`whenNotPaused`) e blacklist; essas condições podem bloquear transferências em outras transações, mas nesta execução a chamada foi bem-sucedida. — `explorer.source.0xdac17f958d2ee523a2206206994597c13d831ec7` `explorer.transaction`

**Próximos passos**

- Se você é o remetente e esperava outro resultado: verifique o saldo antes da transação e confirme que `49.19 USDT` saiu da sua carteira — consulte exploradores com o hash `0x0efab637e8e1a0503bf7e0e9e28a981bd83827cd2cbf73964938097e4f7e3ec9`.
- Se você é o destinatário e não recebeu o valor na interface desejada: consulte o histórico do token para `0xe0fe6653425be82c3f6f5e6e9142e145771cdd26` e confirme o saldo de USDT; o log `Transfer` confirma que a cadeia registrou a transferência.
- Para suporte adicional: forneça capturas de tela do saldo on-chain e o hash da transação para investigação do wallet provider.

**Incertezas declaradas pela IA**

- O pacote de evidência indica que o repositório de origem completo de `TetherToken` não foi encontrado nos repositórios configurados; detalhes adicionais de implementação (por exemplo, lógica de `UpgradedStandardToken`) não puderam ser verificados além do trecho fornecido (`explorer.source.0xdac17f958d2ee523a2206206994597c13d831ec7`).

<details><summary>Evidências citadas</summary>

| Id | Fonte | Descrição |
| --- | --- | --- |
| `explorer.transaction` | [blockscout](https://eth.blockscout.com/api/v2/transactions/0x0efab637e8e1a0503bf7e0e9e28a981bd83827cd2cbf73964938097e4f7e3ec9) | Blockscout: dados indexados da transação |
| `rpc.receipt` | rpc | RPC: eth_getTransactionReceipt (bloco informado no resultado) |
| `explorer.transfers` | [blockscout](https://eth.blockscout.com/api/v2/transactions/0x0efab637e8e1a0503bf7e0e9e28a981bd83827cd2cbf73964938097e4f7e3ec9/token-transfers) | Blockscout: token-transfers |
| `rpc.logs` | rpc | RPC: logs do recibo |
| `explorer.abi.0xdac17f958d2ee523a2206206994597c13d831ec7` | [blockscout](https://eth.blockscout.com/api/v2/smart-contracts/0xdac17f958d2ee523a2206206994597c13d831ec7) | Blockscout: ABI verificada do contrato |
| `decoder.input` | decoder | Decodificação determinística da calldata pela ABI |
| `decoder.logs` | decoder | Decodificação determinística dos logs pela ABI do emissor |
| `explorer.source.0xdac17f958d2ee523a2206206994597c13d831ec7` | [blockscout](https://eth.blockscout.com/api/v2/smart-contracts/0xdac17f958d2ee523a2206206994597c13d831ec7) | Blockscout: código-fonte verificado de TetherToken |

</details>

> **Revisão:** correta. 49.190.000 unidades com 6 decimais = 49,19 USDT, conferido no explorer. O modelo não pediu ferramentas porque o pacote de evidências já trazia a chamada e o evento decodificados. O `TetherToken` não está nos repositórios configurados; a análise usa só o código verificado do explorer e declara isso.

## 2. Transação com falha — foco desenvolvedor

| Campo | Valor |
| --- | --- |
| Transação | [`0xa0107a2a…b1ae0d`](https://eth.blockscout.com/tx/0xa0107a2a9736dfbde0c1dbfd0d53fd2e8c122f3b9177675e058735e427b1ae0d) |
| Rede / foco | `ethereum-mainnet` / desenvolvedor (`developer`) |
| Status | `failed` |
| Função decodificada | `multicall(bytes[])` |
| Modelo | `openai:gpt-5-mini` · 2 passos · ferramentas: get_contract_info |
| Tokens / tempo do LLM | 14795 entrada · 1531 saída · 18.9 s · ~US$ 0.007 |
| Fundamentação | 1 item descartado · 0 identificadores não verificados |
| Resultado completo | [`02-failed-slippage.json`](02-failed-slippage.json) |

**Resumo determinístico (sem LLM):** A transação falhou. O valor informado não deve ser tratado como transferência concluída. Função chamada, decodificada pela ABI: multicall(bytes[]). Chamadas agrupadas, na ordem: mint((address,address,uint24,int24,int24,uint256,uint256,uint256,uint256,address,uint256)), refundETH().

**Explicação da IA:** A transação `0xa0107a2a9736dfbde0c1dbfd0d53fd2e8c122f3b9177675e058735e427b1ae0d` chamada `multicall(bytes[])` falhou com revert. O revert retornado foi "Price slippage check" e ocorreu durante uma chamada interna (delegatecall) dentro de `multicall` ao tentar executar `mint(...)`/fluxo de adição de liquidez. Não foi possível repetir a transação no estado anterior pelo RPC disponível.

<details><summary>Como a IA chegou a essa conclusão</summary>

- **Observado:** A transação falhou de acordo com fontes indexadas e recibo RPC. — `explorer.transaction` `rpc.receipt`
- **Decodificado:** Função externa chamada: `multicall(bytes[])` com dois payloads: primeiro `mint((address,address,uint24,int24,int24,uint256,uint256,uint256,uint256,address,uint256))`, segundo `refundETH()`. Argumentos do `mint` incluem `token0 = 0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48`, `token1 = 0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2`, `fee = 100`, `amount0Desired = 173442`, `amount1Desired = 99999731491510043`, `amount0Min = 0`, `amount1Min = 97522639128243803`, `recipient = 0x143299ce2b02ee230bc66a70799c1088c4ebfb08`. — `decoder.input`
- **Observado:** Várias chamadas internas e delegatecalls falharam durante a execução, incluindo delegatecall do próprio contrato e chamadas para `0xa0b8...` e `0xc02a...` conforme o trace indexado. — `explorer.calls`
- **Código:** O contrato `Multicall.multicall` faz `delegatecall` para cada payload e, ao receber `success == false`, extrai e reverte com a string decodificada do `result` (linhas de tratamento de erro). — `explorer.source.0x46a15b0b27311cedf172ab29e4f4766fbe7f4364`
- **Código:** Tanto em `LiquidityManagement.addLiquidity` quanto em `NonfungiblePositionManager.decreaseLiquidity` há um `require(..., 'Price slippage check')` que reverte quando os valores efetivos `amount0/amount1` são menores que `amount0Min/amount1Min`. — `explorer.source.0x46a15b0b27311cedf172ab29e4f4766fbe7f4364`
- **Inferência:** O revert "Price slippage check" indica que, durante o fluxo de `mint`/`addLiquidity`, os valores retornados pelo pool (`amount0` ou `amount1`) ficaram abaixo dos mínimos exigidos (`amount0Min`/`amount1Min`) e portanto a transação foi revertida. — `decoder.input` `explorer.source.0x46a15b0b27311cedf172ab29e4f4766fbe7f4364` `explorer.transaction`

</details>

**Causas prováveis**

- Os valores efetivos de token mintados foram menores que `amount1Min = 97522639128243803` (ou `amount0Min`), fazendo o `require` falhar durante `addLiquidity`/`mint`. — `decoder.input` `explorer.source.0x46a15b0b27311cedf172ab29e4f4766fbe7f4364` `explorer.transaction`

**Pontos de atenção**

- O padrão `multicall` usa `delegatecall` e repropaga o revert string do callee; isso torna a mensagem de erro visível ao originador mas também indica que um revert interno interrompe todo o `multicall`. Verifique se isso é o comportamento desejado ao agregar operações sensíveis à ordem/estado. — `explorer.source.0x46a15b0b27311cedf172ab29e4f4766fbe7f4364` `explorer.calls`

**Próximos passos**

- Localize o `require('Price slippage check')` no código-fonte citado para confirmar qual função (provavelmente `addLiquidity` -> `pool.mint` ou `decreaseLiquidity`) disparou o revert (use o trecho em `explorer.source...`).
- Repetir a chamada localmente contra um nó archive para ler `pool.slot0()` e `positions(...)` no bloco anterior e simular a execução do `LiquidityAmounts.getLiquidityForAmounts` e `pool.mint` para calcular `amount0`/`amount1` reais e comparar com `amount0Min`/`amount1Min`.
- Se o desvio de preço for esperado, aumentar `amountXMin` (ou tolerância) ou ajustar `amountXDesired`/ticks; caso contrário, investigar why pool state diverged (front-running, oráculo, ou mudanças de liquidez).
- Evite reenviar com os mesmos parâmetros sem checar: a chamada provavelmente falhará novamente enquanto a condição de slippage não for atendida.

**Incertezas declaradas pela IA**

- Não é possível determinar exatamente qual chamada interna (ex.: `mint` vs outro caminho) gerou o revert apenas com os dados fornecidos; o indexador mostra múltiplas chamadas falhas `delegatecall` e `call`, mas sem trace completo não há um id único do site do revert (e o replay no bloco anterior foi negado). Evidence necessária: um trace de execução (debug_traceTransaction) ou réplica local com nó archive para confirmar valores retornados por `pool.mint` e qual `require` foi atingido.

<details><summary>Evidências citadas</summary>

| Id | Fonte | Descrição |
| --- | --- | --- |
| `explorer.transaction` | [blockscout](https://eth.blockscout.com/api/v2/transactions/0xa0107a2a9736dfbde0c1dbfd0d53fd2e8c122f3b9177675e058735e427b1ae0d) | Blockscout: dados indexados da transação |
| `rpc.receipt` | rpc | RPC: eth_getTransactionReceipt (bloco informado no resultado) |
| `explorer.calls` | [blockscout](https://eth.blockscout.com/api/v2/transactions/0xa0107a2a9736dfbde0c1dbfd0d53fd2e8c122f3b9177675e058735e427b1ae0d/internal-transactions) | Blockscout: internal-transactions |
| `decoder.input` | decoder | Decodificação determinística da calldata pela ABI |
| `explorer.source.0x46a15b0b27311cedf172ab29e4f4766fbe7f4364` | [blockscout](https://eth.blockscout.com/api/v2/smart-contracts/0x46a15b0b27311cedf172ab29e4f4766fbe7f4364) | Blockscout: código-fonte verificado de NonfungiblePositionManager |

</details>

> **Revisão:** diagnóstico consistente com a evidência. O RPC público recusou a repetição no bloco anterior (sem estado histórico), então o motivo veio do explorer, e a limitação aparece em `uncertainties`. A chamada `multicall(bytes[])` foi aberta deterministicamente em `mint(...)` e `refundETH()`. Com isso, a causa provável cita o parâmetro exato: `amount1Min` exigia ao menos 97,5% do WETH enviado (tolerância de ~2,5%). O localizador de código lista todos os `require` com a mesma mensagem, inclusive `decreaseLiquidity`. O modelo ficou com `mint` → `addLiquidity` por causa das chamadas decodificadas. A verificação de saída descartou uma afirmação sem evidência válida. Ressalva: as "chamadas internas que falharam" são consequência do revert (toda a execução é desfeita), não falhas independentes.

## 3. Lógica de negócio do repositório — foco auditoria

| Campo | Valor |
| --- | --- |
| Transação | [`0x10cf88ef…3255b1`](https://eth.blockscout.com/tx/0x10cf88efd1ecf75d7599ab932d7dde19be9bb723f82bd5b2dae55fb2373255b1) |
| Rede / foco | `ethereum-mainnet` / auditoria (`auditor`) |
| Status | `success` |
| Função decodificada | `transfer(address,uint256)` |
| Modelo | `openai:gpt-5-mini` · 1 passo · ferramentas: nenhuma |
| Tokens / tempo do LLM | 7055 entrada · 1829 saída · 20.0 s · ~US$ 0.006 |
| Fundamentação | 0 itens descartados · 0 identificadores não verificados |
| Resultado completo | [`03-usdc-repository-grounding.json`](03-usdc-repository-grounding.json) |

**Resumo determinístico (sem LLM):** A transação foi executada com sucesso segundo as fontes consultadas. Função chamada, decodificada pela ABI: transfer(address,uint256).

**Explicação da IA:** Transação bem-sucedida que chamou `transfer(address,uint256)` no contrato proxy `0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48` (USDC). A chamada delegou para a implementação `0x43506849d7c04f9138d1a2050bbf3a0c054402dd` e resultou no evento `Transfer` de 446000000 (USDC, 6 decimais).

<details><summary>Como a IA chegou a essa conclusão</summary>

- **Observado:** A transação foi enviada por `0x1e2733af1a1f6447a46f59ee0693730f277d31e8` para o contrato `0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48` com calldata `0xa9059cbb...` (selector `0xa9059cbb`). — `explorer.transaction` `rpc.transaction` `decoder.input`
- **Decodificado:** A função chamada foi `transfer(address,uint256)` com argumentos: `to = 0x9d7d8b567ee10bcb4f9db438245e1d0668175d72`, `value = 446000000`. — `decoder.input` `explorer.transaction`
- **Observado:** O recibo e os logs mostram um `Transfer` emitido pelo mesmo endereço do token, indicando sucesso da transferência on-chain. — `rpc.receipt` `rpc.logs` `explorer.logs` `explorer.transfers`
- **Observado:** Há uma chamada interna do token proxy para `0x43506849d7c04f9138d1a2050bbf3a0c054402dd` via `delegatecall` (sucesso). — `explorer.calls` `explorer.contract.0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48`
- **Código:** O código verificado da implementação (`FiatTokenV1.transfer`) aplica os modificadores `whenNotPaused` e `notBlacklisted(msg.sender)` e `notBlacklisted(to)` antes de executar `_transfer`. — `explorer.source.0x43506849d7c04f9138d1a2050bbf3a0c054402dd` `repository.circlefin/stablecoin-evm:contracts/v1/FiatTokenV1.sol`
- **Código:** O modificador `whenNotPaused` requer que a variável `paused` seja falsa; `notBlacklisted` chama `_isBlacklisted` e reverte se verdadeiro. — `explorer.source.0x43506849d7c04f9138d1a2050bbf3a0c054402dd` `repository.circlefin/stablecoin-evm:contracts/v1/Pausable.sol` `repository.circlefin/stablecoin-evm:contracts/v1/Blacklistable.sol`
- **Inferência:** Como a transação foi bem-sucedida e o evento `Transfer` foi emitido, as checagens `whenNotPaused` e `notBlacklisted` (para `msg.sender` e `to`) passaram, e o saldo de `msg.sender` foi suficiente para `value = 446000000`. — `rpc.receipt` `rpc.logs` `explorer.source.0x43506849d7c04f9138d1a2050bbf3a0c054402dd` `decoder.input`
- **Observado:** O pacote de evidências nota que `0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48` é um proxy e que a implementação apontada é `0x43506849d7c04f9138d1a2050bbf3a0c054402dd`; há uma advertência de que o slot de implementação deve ser verificado por RPC histórico para confirmar a implementação no bloco. — `explorer.contract.0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48`

</details>

**Pontos de atenção**

- Padrão proxy com `delegatecall` transfere execução e armazenamento para a implementação; privilégios administrativos na implementação ou no controlador do proxy podem afetar todos os saldos e lógica do token. — `explorer.calls` `explorer.contract.0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48`
- A presença de `whenNotPaused` e `notBlacklisted` indica mecanismos de controle centralizados (pausar e blacklist). Revise quem pode acionar `pause`/`unpause` e alterar a lista negra; essas capacidades são de alto impacto operacional e de custódia. — `explorer.source.0x43506849d7c04f9138d1a2050bbf3a0c054402dd` `repository.circlefin/stablecoin-evm:contracts/v1/Blacklistable.sol` `repository.circlefin/stablecoin-evm:contracts/v1/Pausable.sol`
- Não há evidência aqui sobre limites administrativos (multisig, timelock). A ausência dessa informação no pacote justifica revisão adicional: acesso administrativo sem salvaguardas on-chain aumenta risco de abuso. — `explorer.source.0x43506849d7c04f9138d1a2050bbf3a0c054402dd`

**Próximos passos**

- Verificar o slot de implementação do proxy `0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48` no bloco `26101182` via RPC histórico para confirmar que a implementação era `0x43506849d7c04f9138d1a2050bbf3a0c054402dd` (e.g. `eth_getStorageAt`).
- Consultar, por leitura RPC histórica, os estados relevantes: o booleano `paused`, o mapeamento/estrutura que armazena blacklist e os endereços com privilégios administrativos (pausar, adicionar blacklist, atualizar implementação).
- Auditar quem tem os papéis de administração do proxy e da lógica (`upgrade`, `pause`, `blacklist`) e revisar controles fora da cadeia (processos de upgrade, multisig, timelock).

**Incertezas declaradas pela IA**

- O pacote não contém leituras históricas do slot de implementação do proxy no bloco 26101182; portanto não é possível afirmar com certeza que a implementação usada naquele bloco era `0x43506849d7c04f9138d1a2050bbf3a0c054402dd` sem uma leitura RPC histórica.
- Não há leituras on-chain no pacote que confirmem o estado de `paused`, o mapeamento de blacklist, nem os endereços com privilégios administrativos naquele bloco; essas leituras são necessárias para maior confiança operacional.

<details><summary>Evidências citadas</summary>

| Id | Fonte | Descrição |
| --- | --- | --- |
| `explorer.transaction` | [blockscout](https://eth.blockscout.com/api/v2/transactions/0x10cf88efd1ecf75d7599ab932d7dde19be9bb723f82bd5b2dae55fb2373255b1) | Blockscout: dados indexados da transação |
| `rpc.transaction` | rpc | RPC: eth_getTransactionByHash (bloco informado no resultado) |
| `rpc.receipt` | rpc | RPC: eth_getTransactionReceipt (bloco informado no resultado) |
| `explorer.logs` | [blockscout](https://eth.blockscout.com/api/v2/transactions/0x10cf88efd1ecf75d7599ab932d7dde19be9bb723f82bd5b2dae55fb2373255b1/logs) | Blockscout: logs |
| `explorer.transfers` | [blockscout](https://eth.blockscout.com/api/v2/transactions/0x10cf88efd1ecf75d7599ab932d7dde19be9bb723f82bd5b2dae55fb2373255b1/token-transfers) | Blockscout: token-transfers |
| `explorer.calls` | [blockscout](https://eth.blockscout.com/api/v2/transactions/0x10cf88efd1ecf75d7599ab932d7dde19be9bb723f82bd5b2dae55fb2373255b1/internal-transactions) | Blockscout: internal-transactions |
| `rpc.logs` | rpc | RPC: logs do recibo |
| `explorer.contract.0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48` | [blockscout](https://eth.blockscout.com/api/v2/smart-contracts/0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48) | Blockscout: metadados do contrato e implementação de proxy |
| `decoder.input` | decoder | Decodificação determinística da calldata pela ABI |
| `explorer.source.0x43506849d7c04f9138d1a2050bbf3a0c054402dd` | [blockscout](https://eth.blockscout.com/api/v2/smart-contracts/0x43506849d7c04f9138d1a2050bbf3a0c054402dd) | Blockscout: código-fonte verificado de FiatTokenV2_2 |
| `repository.circlefin/stablecoin-evm:contracts/v1/FiatTokenV1.sol` | [repository](https://github.com/circlefin/stablecoin-evm/blob/fc85788bc7c23cefe3df1a757133048bfddadeaa/contracts/v1/FiatTokenV1.sol) | Repositório circlefin/stablecoin-evm: contracts/v1/FiatTokenV1.sol |
| `repository.circlefin/stablecoin-evm:contracts/v1/Pausable.sol` | [repository](https://github.com/circlefin/stablecoin-evm/blob/fc85788bc7c23cefe3df1a757133048bfddadeaa/contracts/v1/Pausable.sol) | Repositório circlefin/stablecoin-evm: contracts/v1/Pausable.sol |
| `repository.circlefin/stablecoin-evm:contracts/v1/Blacklistable.sol` | [repository](https://github.com/circlefin/stablecoin-evm/blob/fc85788bc7c23cefe3df1a757133048bfddadeaa/contracts/v1/Blacklistable.sol) | Repositório circlefin/stablecoin-evm: contracts/v1/Blacklistable.sol |

</details>

> **Revisão:** correta e com o tom pedido para auditoria. Os pontos de pausa e blacklist aparecem como itens para revisão, não como vulnerabilidades. As citações apontam para o código verificado e para permalinks do repositório no commit `fc85788bc7c2`. As incertezas reconhecem que a implementação informada é a atual, não necessariamente a do bloco. O caminho absoluto de outra máquina no código verificado (`/Users/…/stablecoin-evm-private-eurc-mainnet-eth/…`) vem do próprio Blockscout: é o caminho usado na verificação do contrato.

## Coletas determinísticas anteriores

[`collection/`](collection/) guarda resultados sem LLM, coletados durante o desenvolvimento (1 e 2 de outubro de 2026): transferência nativa, ERC-20 com e sem RPC, proxy, swap com vários contratos, contexto de código e três falhas (mensagem de revert, erro customizado com corrida no bloco e falta de gas). As falhas foram coletadas quando a repetição via RPC ainda era possível e são a base dos snapshots de avaliação em [`evals/`](../evals/).
