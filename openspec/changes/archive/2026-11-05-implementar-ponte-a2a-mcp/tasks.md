# Tasks

## 1. Configuração do Ambiente e Estrutura Inicial

- [x] 1.1 Configurar o ambiente do projeto em `servidor-mcp/` e `agente/` com dependências travadas e scripts de execução, verificando que ambos os ambientes inicializam sem erros.
- [x] 1.2 Configurar arquivos de domínio e dados em `dados/` (`salas.json`, `reservas.json`, `politica-de-uso.md`), verificando a integridade dos dados e que não há alterações nos arquivos originais.

## 2. Servidor MCP: Transporte, Metadados e Ferramentas Básicas

- [x] 2.1 Implementar servidor Streamable HTTP na porta 7301 em `/mcp` com logging de método, id e traceparent em `stderr`, verificando a rejeição com `-32602` (HTTP 400) para requisições sem `_meta` e `-32020` para divergência de headers.
- [x] 2.2 Implementar o resource `politica://uso` servindo o conteúdo markdown de `dados/politica-de-uso.md`, verificando retorno com `mimeType: text/markdown` e `-32602` para URIs inválidas.
- [x] 2.3 Implementar a tool `listar_salas` retornando `structuredContent` e bloco de texto conforme `dados/salas.json`, verificando sua listagem no método `tools/list` com `inputSchema` válido.
- [x] 2.4 Implementar a tool `consultar_disponibilidade` aplicando as regras de horário (08h às 20h), limite de 2h, ordem temporal e existência da sala, verificando o retorno exato das mensagens de erro com `isError: true`.

## 3. Servidor MCP: Ciclo MRTR e Reserva de Sala

- [x] 3.1 Implementar a tool `reservar_sala` no caminho feliz (sem conflito), gerando novo ID de reserva e retornando `structuredContent` completo com o campo `politica`, verificando a visibilidade da nova reserva em consultas subsequentes.
- [x] 3.2 Implementar cálculo de salas alternativas livres (capacidade igual/maior, ordenadas por capacidade ASC e ID ASC, máximo 3) e retorno com `input_required` contendo elicitation em form mode, verificando o erro `Sem alternativas disponiveis no intervalo` quando não houver opções.
- [x] 3.3 Implementar checagem de capability do cliente exigindo `elicitation: { form: {} }`, verificando o retorno de erro `-32021` com HTTP 400 e `data.requiredCapabilities` quando a capability estiver ausente.
- [x] 3.4 Implementar geração e verificação de `requestState` assinado com HMAC-SHA256 usando `REQUEST_STATE_SECRET` e expiração de 15 minutos, verificando rejeição com `-32602` em caso de token adulterado ou expirado.
- [x] 3.5 Implementar tratamento do retry de `reservar_sala` recebendo `inputResponses` e `requestState`, verificando conclusão da reserva na sala escolhida, conclusão sem reserva para `action: decline/cancel`, e suporte a retries após reinício do processo MCP.

## 4. Agente: Servidor A2A e Gerenciamento de Tasks

- [x] 4.1 Implementar servidor HTTP na porta 7300 servindo o Agent Card oficial v1.0 em `GET /.well-known/agent-card.json` com a skill `reservar-sala`, verificando conformidade com `exemplos/wire/07-a2a-agent-card.json`.
- [x] 4.2 Implementar endpoint JSON-RPC 2.0 em `/a2a` com suporte aos métodos `SendMessage` e `GetTask`, verificando transições de estado da Task (`TASK_STATE_SUBMITTED`, `TASK_STATE_WORKING`) e rejeição de mensagens para Tasks em estados terminais.
- [x] 4.3 Implementar parser determinístico para pedidos no formato `reservar sala=<id> inicio=<iso8601> fim=<iso8601> responsavel=<nome>`, verificando geração de erro terminal `TASK_STATE_FAILED` quando a tool MCP retornar erro de execução.

## 5. Agente: Host MCP e A Ponte

- [x] 5.1 Implementar cliente MCP interno no agente executando `tools/list` antes de qualquer chamada e lendo a versão da política em `politica://uso`, verificando a emissão de `_meta` com a capability `elicitation: { form: {} }` e propagação do `traceparent`.
- [x] 5.2 Implementar a transição da Task para `TASK_STATE_INPUT_REQUIRED` ao receber `input_required` do MCP, formatando a mensagem `alternativas: <ids>` e armazenando o `requestState` opaco de forma isolada na Task sem vazá-lo para o cliente A2A.
- [x] 5.3 Implementar processamento de `escolha=<id>` e `escolha=recusar` em novo `SendMessage`, executando o retry MCP com um novo JSON-RPC ID e concluindo a Task em `TASK_STATE_COMPLETED` (com artifact `reserva`) ou `TASK_STATE_CANCELED`.
- [x] 5.4 Implementar validação de alternativas inválidas mantendo a Task em `TASK_STATE_INPUT_REQUIRED`, verificando que múltiplas Tasks pausadas simultaneamente não trocam de `requestState`.

## 6. Integração, Validação e Documentação

- [x] 6.1 Executar a suíte de conformidade completa `python3 validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301`, verificando que todas as 36 verificações resultam em PASS.
- [x] 6.2 Redigir o `README.md` com as seções obrigatórias: como rodar os dois processos e o validador a partir de um clone limpo, explicação detalhada de onde a ponte acontece, decisões técnicas (proteção do `requestState`, expiração e armazenamento de Tasks) e a saída completa do validador.
