# Design

## Context

A solução integra dois protocolos stateless com papéis complementares no ecossistema de agentes:
1. **MCP (Model Context Protocol - revisão 2026-07-28)**: utilizado internamente para expor as capacidades do domínio (salas de reunião, políticas de uso e catálogo de reservas) sobre transporte Streamable HTTP na porta 7301.
2. **A2A (Agent-to-Agent Protocol v1.0)**: utilizado externamente como fachada interoperável sobre JSON-RPC 2.0 na porta 7300, permitindo que outros agentes descubram a skill `reservar-sala` via Agent Card e controlem o ciclo de vida de tarefas (`Task`).

A intersecção crítica ("A Ponte") é o encontro entre o mecanismo MRTR (Multi-Round Tool Result) do MCP — que depende de `input_required` e de um `requestState` opaco assinado — e a máquina de estados de tarefas do A2A, que transita por `TASK_STATE_INPUT_REQUIRED`.

## Goals / Non-Goals

**Goals:**
- Implementar dois processos totalmente desacoplados (`servidor-mcp` e `agente`), comunicando-se exclusivamente por HTTP.
- Implementar o ciclo completo de MRTR no servidor MCP com elicitation em form mode determinística e verificação estrita de integridade via HMAC-SHA256 (`REQUEST_STATE_SECRET`).
- Garantir a resiliência do `requestState` a reinicializações do servidor MCP sem dependência de estado em memória entre rodadas.
- Implementar o agente como host MCP dinâmico (descoberta via `tools/list` antes da execução) e servidor A2A padronizado (`/.well-known/agent-card.json`, `/a2a` com `SendMessage` e `GetTask`).
- Construir a máquina de estados da ponte no agente, mapeando o `requestState` por `taskId` em isolamento estrito sem expô-lo ao cliente externo.
- Propagar o contexto de rastreabilidade (`traceparent`) de ponta a ponta e registrá-lo em `stderr`.
- Atingir 100% de sucesso nas 36 verificações de `validador/validar.py`.

**Non-Goals:**
- Não utilizar Modelos de Linguagem (LLM) no caminho de execução: toda a interpretação e decisão é estritamente determinística e baseada em regras.
- Não utilizar banco de dados relacional ou ORM: dados iniciais são carregados de arquivos JSON e mutações mantidas em memória por processo.
- Não implementar autenticação/autorização (OAuth, tokens, esquemas de segurança).
- Não suportar streaming (SSE, push notifications ou `SendStreamingMessage`).
- Não persistir reservas em disco após encerramento do processo (exceto a validade do `requestState`).

## Decisions

### 1. Stack Tecnológica: Python com Uvicorn / Starlette / HTTPX
- **Decisão**: Utilizar Python 3.10+ para ambos os processos (`servidor-mcp` e `agente`), empregando Starlette/Uvicorn para as interfaces HTTP/JSON-RPC e `httpx` para a comunicação HTTP interna do agente como host MCP.
- **Justificativa**: O starter e validador já são desenvolvidos em Python sem dependências externas; Python possui suporte nativo robusto a criptografia (`hmac`, `hashlib`), manipulação de ISO 8601 e tipagem.
- **Alternativas consideradas**:
  - TypeScript/Node.js com SDK oficial: viável, porém exigiria gerenciar dois ambientes de runtime distintos no repositório (`node` + `python` para o validador).

### 2. Estrutura e Integridade Criptográfica do `requestState`
- **Decisão**: O `requestState` será um token auto-contido assinado por HMAC-SHA256, estruturado no formato:
  `base64url(payload_json) + "." + base64url(hmac_signature)`
  onde o `payload_json` contém:
  ```json
  {
    "sala": "<id_da_sala>",
    "inicio": "<iso8601>",
    "fim": "<iso8601>",
    "responsavel": "<nome>",
    "alternativas": ["<sala-1>", "<sala-2>"],
    "exp": 1793839200,
    "nonce": "<hex_aleatorio>"
  }
  ```
- **Segurança**:
  - A chave HMAC é obtida estritamente da variável de ambiente `REQUEST_STATE_SECRET` (mínimo de 32 bytes).
  - O tempo de expiração é fixado em 15 minutos (entre 5 e 30 minutos, atendendo ao requisito).
  - Qualquer adulteração de byte no payload ou assinatura invalida a checagem HMAC e dispara erro `-32602`.
  - Como todos os dados originais do pedido estão selados no token, o servidor MCP reconstrói o pedido de forma idêntica mesmo que tenha sido reiniciado entre o `input_required` e o retry.
  - No retry, os parâmetros informados pelo cliente são confrontados contra o conteúdo selado, impedindo ataques de injeção ou substituição de parâmetros.

### 3. Servidor MCP em Streamable HTTP
- **Decisão**: Endpoint único em `POST /mcp` na porta 7301 com validação estrita de protocolo:
  - Validação de `_meta`: requisições sem `io.modelcontextprotocol/protocolVersion` ou sem `io.modelcontextprotocol/clientCapabilities` recebem HTTP 400 com erro `-32602`.
  - Validação de headers espelhados: `MCP-Protocol-Version`, `Mcp-Method` e `Mcp-Name` são conferidos contra o corpo da requisição; discrepâncias recebem `-32020`.
  - Log em `sys.stderr` a cada requisição: `[MCP] method=<method> id=<id> traceparent=<traceparent>`.
  - Cálculo de salas alternativas:
    1. Filtrar salas cuja capacidade >= capacidade da sala requerida.
    2. Excluir salas com conflito de horário no intervalo especificado.
    3. Ordenar por capacidade crescente (`capacidade ASC`) e desempatar alfabeticamente por `id ASC`.
    4. Limitar a no máximo 3 salas alternativas.
  - Capability check: se o cliente não declarar `{"elicitation": {"form": {}}}`, responder erro `-32021` com HTTP 400 e payload `data: {"requiredCapabilities": ["elicitation.form"]}`.

### 4. Arquitetura do Agente: Servidor A2A e Host MCP
- **Decisão**:
  - Endpoint `GET /.well-known/agent-card.json` servindo o card oficial v1.0 com declaração da skill `reservar-sala`.
  - Endpoint `POST /a2a` processando requisições JSON-RPC 2.0 para `SendMessage` e `GetTask`.
  - Gerenciamento de tarefas em memória: `TaskStore` mapeia cada `taskId` a uma instância de `Task` contendo seu histórico de mensagens, artifacts e estado de ponte associado (`requestState`, chave de elicitation, alternatives válidas).
  - Isolamento de tarefas: cada `taskId` possui seu próprio contexto, permitindo que múltiplas reservas concorrentes fiquem em pausa (`TASK_STATE_INPUT_REQUIRED`) simultaneamente sem colisão de estado.

### 5. Mecânica da Ponte (The Bridge)
- **Decisão**:
  - Quando a chamada MCP a `reservar_sala` retorna `resultType: "input_required"`:
    - O agente extrai o `requestedSchema.properties.sala.enum` (ou const).
    - O agente formata a resposta da Task exatamente como `alternativas: <id1>, <id2>`.
    - O `requestState` é armazenado na Task localmente.
    - O status da Task é atualizado para `TASK_STATE_INPUT_REQUIRED`.
  - Quando o cliente A2A envia uma nova mensagem para a Task pausada:
    - Se a mensagem for `escolha=recusar`: o agente envia retry ao MCP com `action: "decline"` e marca a Task como `TASK_STATE_CANCELED`.
    - Se a mensagem for `escolha=<id>` e `<id>` pertencer às alternativas: o agente cria uma requisição MCP com **novo JSON-RPC ID**, anexa o `inputResponses` com `action: "accept"` e o `requestState` intacto, concluindo a Task em `TASK_STATE_COMPLETED` com o artifact `reserva`.
    - Se a escolha for inválida: a Task permanece em `TASK_STATE_INPUT_REQUIRED` e a lista de alternativas é reenviada.

## Risks / Trade-offs

- **[Risco: Reinício do processo MCP invalidar reservas existentes]**
  → *Mitigação*: O validador testa a capacidade de concluir um retry mesmo após reinício do servidor MCP; como o `requestState` carrega o estado selado de forma auto-contida e é validado com a chave `REQUEST_STATE_SECRET`, o servidor consegue validar o agendamento mesmo com processo recém-iniciado.
- **[Risco: Conflito de IDs JSON-RPC no retry]**
  → *Mitigação*: O agente utiliza um contador atômico ou UUID sequencial para requisições MCP, garantindo que o retry de MRTR possua id estritamente diferente da requisição inicial.
- **[Risco: Formatação de erros e textos divergente da especificação]**
  → *Mitigação*: Todas as strings de erro de domínio (`Sala inexistente: ...`, `Fora da janela de uso: ...`, `Duracao acima do limite: ...`, `Intervalo invalido: ...`, `Sem alternativas disponiveis no intervalo`, `alternativas: ...`) são centralizadas em constantes compartilhadas e estritamente auditadas contra o enunciado.
