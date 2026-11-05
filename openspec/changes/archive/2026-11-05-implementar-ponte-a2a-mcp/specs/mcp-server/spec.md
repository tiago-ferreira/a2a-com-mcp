# Spec Delta

## Purpose

Provê a interface de servidor MCP sobre transporte Streamable HTTP para o domínio de reserva de salas, expondo catálogo, consulta de disponibilidade, recursos de política e ciclo MRTR com estado protegido por integridade criptográfica.

## ADDED Requirements

### Requirement: Servidor Streamable HTTP e Metadados Obrigatórios
O servidor MCP SHALL atender requisições na porta 7301 em endpoint Streamable HTTP `/mcp` e validar metadados de protocolo em cada requisição.

#### Scenario: Requisição sem metadados obrigatórios
- **WHEN** uma chamada JSON-RPC é enviada sem `io.modelcontextprotocol/protocolVersion` ou sem `io.modelcontextprotocol/clientCapabilities` em `_meta`
- **THEN** o servidor retorna erro JSON-RPC `-32602` com código HTTP `400`

#### Scenario: Divergência entre headers HTTP e corpo JSON-RPC
- **WHEN** os headers `MCP-Protocol-Version`, `Mcp-Method` ou `Mcp-Name` divergirem do payload JSON-RPC
- **THEN** o servidor rejeita a chamada com código de erro `-32020`

### Requirement: Descoberta de Ferramentas e Recursos de Política
O servidor MCP SHALL expor as ferramentas de salas e o recurso de política de uso conforme os dados oficiais do domínio.

#### Scenario: Listagem de ferramentas
- **WHEN** o cliente executa `tools/list`
- **THEN** o servidor retorna as ferramentas `listar_salas`, `consultar_disponibilidade` e `reservar_sala` com schemas JSON Schema válidos

#### Scenario: Leitura do recurso de política de uso
- **WHEN** o cliente executa `resources/read` para a URI `politica://uso`
- **THEN** o servidor retorna o texto de `dados/politica-de-uso.md` com `mimeType` `text/markdown`

#### Scenario: Leitura de recurso inexistente
- **WHEN** o cliente executa `resources/read` para uma URI não mapeada
- **THEN** o servidor rejeita a requisição com erro JSON-RPC `-32602`

### Requirement: Validações de Domínio e Consultas de Salas
O servidor MCP SHALL validar horários, durações e integridade de identificadores ao consultar disponibilidade ou listar salas.

#### Scenario: Listagem estruturada de salas
- **WHEN** a ferramenta `listar_salas` é executada
- **THEN** o servidor retorna a lista de salas em `structuredContent` e em bloco de texto conforme `dados/salas.json`

#### Scenario: Consulta com identificador de sala inexistente
- **WHEN** `consultar_disponibilidade` recebe um identificador de sala ausente no cadastro
- **THEN** a execução conclui com `isError: true` e a mensagem exata `Sala inexistente: <id informado>`

#### Scenario: Consulta fora da janela permitida
- **WHEN** o intervalo solicitado iniciar antes das 08:00 ou terminar após as 20:00 no fuso de São Paulo (-03:00)
- **THEN** a execução conclui com `isError: true` e a mensagem exata `Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00`

#### Scenario: Consulta com duração superior a duas horas
- **WHEN** o intervalo entre início e fim for maior que duas horas
- **THEN** a execução conclui com `isError: true` e a mensagem exata `Duracao acima do limite: a politica permite no maximo 2 horas`

#### Scenario: Consulta com intervalo invertido ou vazio
- **WHEN** o horário de fim for menor ou igual ao horário de início
- **THEN** a execução conclui com `isError: true` e a mensagem exata `Intervalo invalido: fim deve ser posterior a inicio`

### Requirement: Ciclo MRTR e Elicitation em Caso de Conflito
A ferramenta `reservar_sala` SHALL emitir `input_required` com elicitation em form mode quando houver conflito de horário e existirem salas alternativas.

#### Scenario: Conflito com alternativas disponíveis
- **WHEN** a sala solicitada estiver ocupada e houver salas livres no intervalo com capacidade igual ou superior
- **THEN** o servidor retorna `resultType` igual a `input_required` com elicitation em form mode contendo até 3 alternativas ordenadas por capacidade e id, além de `requestState` assinado

#### Scenario: Conflito sem nenhuma alternativa viável
- **WHEN** a sala solicitada estiver ocupada e não houver nenhuma sala alternativa livre que atenda aos critérios
- **THEN** o servidor retorna `isError: true` com a mensagem exata `Sem alternativas disponiveis no intervalo`

#### Scenario: Cliente sem capability de elicitation form mode
- **WHEN** um cliente que não declarou `elicitation: { form: {} }` requisita um intervalo em conflito
- **THEN** o servidor retorna erro JSON-RPC `-32021` com HTTP `400` listando a capability requerida em `data.requiredCapabilities`

### Requirement: Integridade e Resolução de Retry do MRTR
O servidor MCP SHALL validar o `requestState` e concluir a reserva ou recusa ao receber o retry do cliente.

#### Scenario: Retry aceitando alternativa com dados válidos
- **WHEN** o cliente reenvia a chamada com `inputResponses` aceitando uma sala alternativa válida e com `requestState` íntegro
- **THEN** o servidor conclui a reserva gerando ID e retornando `resultType: complete` com `structuredContent`

#### Scenario: Retry com recusa do usuário
- **WHEN** o cliente reenvia a chamada com ação de recusa (`action: decline` ou `cancel`)
- **THEN** o servidor conclui com `isError: false` ou ausente e `structuredContent` com `reservado: false`

#### Scenario: Retry com requestState adulterado ou expirado
- **WHEN** o cliente envia um `requestState` modificado ou com assinatura HMAC inválida
- **THEN** o servidor rejeita a chamada com código de erro `-32602`

#### Scenario: Retomada após reinício do processo do servidor
- **WHEN** o servidor MCP é reiniciado entre o `input_required` e a recepção do retry válido
- **THEN** o servidor valida o `requestState` usando `REQUEST_STATE_SECRET` e conclui a reserva com sucesso
