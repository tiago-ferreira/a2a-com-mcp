# Spec Delta

## Purpose

Provê o agente autônomo atuando como servidor A2A com Agent Card e ciclo de vida de Task na camada externa, e como host MCP na camada interna, orquestrando a ponte entre pausas de Task e elicitation MRTR.

## ADDED Requirements

### Requirement: Agent Card e Interface JSON-RPC A2A
O agente SHALL atender requisições na porta 7300, disponibilizando o Agent Card na URI `/.well-known/agent-card.json` e os métodos JSON-RPC em `/a2a`.

#### Scenario: Obtenção do Agent Card
- **WHEN** um cliente executa GET em `/.well-known/agent-card.json`
- **THEN** o agente responde 200 com Agent Card v1.0 contendo URL do endpoint, protocolo `1.0` e a skill `reservar-sala`

#### Scenario: Consulta de estado de Task existente
- **WHEN** um cliente chama `GetTask` fornecendo um `taskId` válido
- **THEN** o agente retorna o objeto da Task contendo `id`, `contextId`, status atual e artifacts caso existam

### Requirement: Operação como Host MCP e Propagação de Contexto
O agente SHALL atuar como host MCP descobrindo capacidades dinamicamente e propagando metadados e rastreabilidade.

#### Scenario: Descoberta dinâmica de ferramentas
- **WHEN** o agente se prepara para interagir com o servidor MCP
- **THEN** o agente executa `tools/list` antes de qualquer execução de `tools/call` sem codificar listas fixas

#### Scenario: Leitura da política e versão do domínio
- **WHEN** o agente inicializa ou sincroniza com o servidor MCP
- **THEN** o agente lê o resource `politica://uso` e extrai a versão para composição dos artifacts de reserva

#### Scenario: Propagação de traceparent e declaração de capabilities
- **WHEN** uma requisição A2A chega com header `traceparent`
- **THEN** o agente propaga o mesmo trace-id em `traceparent` no `_meta` das chamadas MCP e declara `elicitation: { form: {} }`

### Requirement: Execução Determinística e Tratamento de Erros da Tool
O agente SHALL interpretar pedidos de reserva em formato fixo e refletir erros de execução em estado terminal da Task.

#### Scenario: Pedido com sala livre e sucesso direto
- **WHEN** o cliente envia `SendMessage` com o formato `reservar sala=<id> inicio=<iso8601> fim=<iso8601> responsavel=<nome>` e a sala está desimpedida
- **THEN** o agente abre uma Task, transita por `WORKING` e conclui em `TASK_STATE_COMPLETED` com o artifact `reserva` contendo os dados e a política

#### Scenario: Erro de execução da tool no servidor MCP
- **WHEN** o servidor MCP devolve uma resposta de tool com `isError: true`
- **THEN** o agente transita a Task para `TASK_STATE_FAILED` mantendo a mensagem exata de erro visível no histórico

#### Scenario: Tentativa de envio para Task terminal
- **WHEN** um `SendMessage` referencia uma Task com status `COMPLETED`, `CANCELED` ou `FAILED`
- **THEN** o agente rejeita a requisição retornando erro de protocolo

### Requirement: Mecanismo da Ponte e Resolução de Conflito
O agente SHALL realizar a ponte entre a resposta `input_required` do MCP e a máquina de estados de Task do A2A sem expor o estado opaco.

#### Scenario: Transição para INPUT_REQUIRED com alternativas
- **WHEN** o servidor MCP devolve `resultType: input_required`
- **THEN** o agente transita a Task para `TASK_STATE_INPUT_REQUIRED`, devolve a mensagem `alternativas: <ids>` e guarda o `requestState` associado à Task

#### Scenario: Retomada com escolha de alternativa válida
- **WHEN** o cliente envia `SendMessage` com `escolha=<id>` referenciando a Task pausada
- **THEN** o agente envia retry ao MCP com um novo JSON-RPC ID contendo `inputResponses` e o `requestState`, concluindo a Task em `TASK_STATE_COMPLETED`

#### Scenario: Retomada com recusa de alternativas
- **WHEN** o cliente envia `SendMessage` com `escolha=recusar`
- **THEN** o agente envia retry ao MCP com ação `decline` e encerra a Task em `TASK_STATE_CANCELED`

#### Scenario: Resposta com alternativa fora das opções
- **WHEN** o cliente envia `escolha=<id>` com sala não oferecida na elicitation
- **THEN** o agente mantém a Task em `TASK_STATE_INPUT_REQUIRED` e repete a linha de alternativas

#### Scenario: Ocultamento do requestState
- **WHEN** o agente emite mensagens, atualizações de Task ou expõe artifacts
- **THEN** o `requestState` nunca é retornado nem vazado nas respostas A2A
