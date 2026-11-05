# Proposal

## Why

A Hill Valley Tech possui cinco salas de reunião gerenciadas anteriormente por uma planilha compartilhada e necessita expor essa capacidade para que assistentes e agentes autônomos da empresa realizem reservas programaticamente.
Para isso, a arquitetura divide a solução em duas pontas sem estado (stateless): um servidor MCP que encapsula o domínio de salas e reservas com ciclo MRTR (Multi-Round Tool Result), e um agente que atua simultaneamente como host MCP por dentro e servidor A2A (Agent-to-Agent) por fora, conectando a necessidade de interação humana/cliente via pausa de Task (`TASK_STATE_INPUT_REQUIRED`) com a elicitation do MCP.

## What Changes

- **Servidor MCP (`servidor-mcp/`)**:
  - Implementação em Streamable HTTP na porta 7301 (`/mcp`).
  - Publicação do resource `politica://uso` com o conteúdo de `dados/politica-de-uso.md`.
  - Disponibilização das tools `listar_salas`, `consultar_disponibilidade` e `reservar_sala`, com JSON Schemas estritos para entrada e emissão de `structuredContent` e texto formatado.
  - Implementação do ciclo MRTR em `reservar_sala` quando há conflito de horário: emissão de `input_required` com elicitation em form mode contendo alternativas ordenadas por capacidade e ID.
  - Emissão e verificação de `requestState` seguro protegido por HMAC (usando segredo configurado na variável de ambiente `REQUEST_STATE_SECRET`) com expiração entre 5 e 30 minutos e tolerância a reinicialização do processo.
  - Validação estrita de metadados em `_meta` (`protocolVersion`, `clientCapabilities`) e headers espelhados (`MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`), rejeitando requests inválidos com `-32602`, `-32020` ou `-32021` conforme a especificação.
  - Logging em `stderr` contendo método, id e `traceparent` de cada requisição.

- **Agente A2A e Host MCP (`agente/`)**:
  - Servidor A2A em JSON-RPC 2.0 sobre HTTP na porta 7300 (`/a2a`) e publicação do Agent Card em `/.well-known/agent-card.json` (v1.0) declarando a skill `reservar-sala`.
  - Métodos `SendMessage` e `GetTask` gerenciando o ciclo de vida da Task (`TASK_STATE_SUBMITTED`, `TASK_STATE_WORKING`, `TASK_STATE_INPUT_REQUIRED`, `TASK_STATE_COMPLETED`, `TASK_STATE_CANCELED`, `TASK_STATE_FAILED`).
  - Host MCP: descoberta em runtime das tools via `tools/list` antes da primeira chamada, leitura da versão no resource `politica://uso`, e envio de metadados obrigatórios com declaração de elicitation em form mode.
  - Propagação do trace context (`traceparent`) recebido do cliente A2A nos metadados MCP.
  - Construção da "Ponte": ao receber `input_required` do MCP, colocar a Task em `TASK_STATE_INPUT_REQUIRED` emitindo `alternativas: <ids>`, associar o `requestState` opaco à Task sem expô-lo ao cliente, e processar a resposta (`escolha=<id>` ou `escolha=recusar`), executando o retry MCP com um novo JSON-RPC ID.

- **Documentação e Validação**:
  - Atualização do `README.md` com instruções completas de inicialização, arquitetura da ponte, decisões técnicas de integridade e saída de execução do validador.
  - Garantia de 100% de aprovação nas 36 verificações de `validador/validar.py`.

## Capabilities

### New Capabilities
- `mcp-server`: Servidor MCP em Streamable HTTP na porta 7301 que expõe as ferramentas do domínio de salas de reunião, validações de política, o resource de política de uso e o ciclo MRTR com elicitation em form mode e `requestState` assinado com HMAC.
- `a2a-agent`: Servidor A2A na porta 7300 e host MCP que publica o Agent Card, processa mensagens e tarefas A2A determinísticas (`reservar-sala`) e orquestra a ponte entre o `input_required` do MCP e a pausa de Task (`TASK_STATE_INPUT_REQUIRED`).

### Modified Capabilities
<!-- Nenhuma capability existente está sendo modificada -->

## Impact

- Novos serviços independentes em `servidor-mcp/` e `agente/`.
- Dependência de SDK oficial MCP (Python ou TypeScript/Node.js) e bibliotecas de HTTP/JSON-RPC.
- Nenhuma alteração nas pastas protegidas `dados/`, `validador/` e `exemplos/`.
- Execução determinística sem dependência de LLM no caminho de execução.
