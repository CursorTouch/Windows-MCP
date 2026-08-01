# Windows MCP GPT — Operação resiliente

## Arquitetura operacional

O runtime persistente utiliza uma única cadeia operacional:

`Tarefa Agendada -> Supervisor -> tunnel-client -> python -m windows_mcp serve --transport stdio`

A tarefa agendada `Windows-MCP-GPT-Supervisor` inicia o supervisor sem janela. O supervisor mantém exatamente um túnel, valida a cadeia MCP real e persiste o estado no diretório local `.orquestrador`, que não deve ser versionado.

O perfil do túnel deve executar diretamente:

`D:/Projetos/WINDOWS-MCP-TEST/.venv/Scripts/python.exe -m windows_mcp serve --transport stdio`

Não usar `uv run` no perfil operacional. A sincronização automática do `uv` pode tentar substituir executáveis em uso e impedir a inicialização do MCP.


## Exclusividade do supervisor

Somente a tarefa `Windows-MCP-GPT-Supervisor` pode administrar o runtime STDIO. As tarefas legadas `Windows MCP GPT Watchdog`, `Windows MCP GPT HTTP Server` e `Windows MCP GPT HTTP Migration Once` são incompatíveis com esta arquitetura porque tentam migrar ou reconectar o mesmo túnel para HTTP.

O script de migração exporta o XML dessas tarefas e as desativa antes de alterar o perfil ou reiniciar o runtime. Se a desativação falhar, a migração é abortada sem tocar no túnel. O watchdog local legado também foi neutralizado para nunca administrar o runtime quando o supervisor persistente estiver ativo.

## Saúde e recuperação

O supervisor considera o runtime saudável somente quando todas as condições são verdadeiras:

- existe exatamente um processo `tunnel-client` do perfil gerenciado;
- existe processo MCP descendente com o comando direto esperado;
- `/healthz` retorna `live`;
- `/readyz` retorna `ready`;
- `/api/status` e `/api/system` confirmam o canal principal;
- o polling do plano de controle é recente;
- não há evento fatal recente de `502`, `504`, EOF ou pipe STDIO fechado.

Após falhas consecutivas, o supervisor encerra somente a cadeia defeituosa e cria um novo túnel com backoff e limite de reinícios. Reinícios bem-sucedidos zeram o histórico após ciclos saudáveis.

## Persistência

A fila usa caixa de entrada atômica, arquivos de especificação, resultados e evidências. Estados possíveis:

- `pending`
- `running`
- `completed`
- `failed`
- `interrupted`

Trabalhos longos são executados por processo-filho independente. O heartbeat e a recuperação do túnel continuam ativos durante testes e builds. Após reinício do supervisor, resultados persistidos são reconciliados sem repetir tarefas concluídas. Observações resumíveis continuam na mesma tarefa, preservando identidade, início, amostras e PIDs-base; não são clonadas nem reiniciadas do zero.

Gravações JSON usam arquivos temporários únicos, `fsync`, substituição atômica e retentativa. Bloqueios transitórios do Windows geram `persistence_retry`; não encerram o supervisor nem descartam a fila em memória.

## Isolamento de ferramentas

### Screenshot

O Screenshot permanece em quarentena por padrão no transporte STDIO. A captura gráfica é executada em processo isolado quando habilitada. Falha, timeout ou bloqueio de desktop deve afetar somente a chamada de Screenshot; as demais ferramentas e a sessão MCP permanecem disponíveis.

### SystemQuery

`SystemQuery` é somente leitura e não aceita comandos arbitrários. Consultas externas permitidas usam processo isolado, sem shell e sem pipes herdados, com prazo rígido e encerramento da árvore completa.

`git_status` não inicia o Git. Ele lê localmente `.git/HEAD` e o índice Git, compara metadados dos arquivos rastreados e omite a varredura de arquivos não rastreados. Isso evita que um `git status` lento atinja o TTL do túnel e feche o STDIO.

## Comandos operacionais

Enfileirar uma validação integral:

```powershell
& "D:\Projetos\WINDOWS-MCP-TEST\.venv\Scripts\python.exe" `
  "D:\Projetos\WINDOWS-MCP-TEST\scripts\enqueue_supervisor_task.py" `
  validate_project --timeout-seconds 1800
```

Consultar heartbeat:

```powershell
Get-Content "D:\Projetos\WINDOWS-MCP-TEST\.orquestrador\supervisor\heartbeat.json" -Raw
```

Consultar fila:

```powershell
Get-Content "D:\Projetos\WINDOWS-MCP-TEST\.orquestrador\supervisor\queue.json" -Raw
```

## Evidências aprovadas em 31/07/2026

- validação integral: lint, 442 testes, wheel e sdist;
- 500 chamadas concorrentes, concorrência 20, watchdog desligado;
- 500 chamadas concorrentes, concorrência 20, watchdog ligado;
- chamadas mistas de Health, SystemQuery date_time e SystemQuery git_status;
- rejeição de ação perigosa sem perda da sessão;
- Screenshot em quarentena seguido de Health no mesmo processo MCP;
- recuperação controlada do túnel em 12,723 segundos;
- recuperação controlada após encerramento exclusivo do processo MCP filho em 11,94 segundos;
- processo MCP antigo encerrado sem órfãos, novo MCP saudável e exatamente um túnel após a recuperação;
- 60 segundos de estabilidade, 55 amostras e nenhum túnel duplicado;
- bloqueio real de queue.json absorvido com retentativa, sem queda do supervisor e sem perda de tarefa.



## TTL da conexão MCP

O `tunnel-client` usa 10 minutos como limite padrão de vida da conexão MCP. Esse padrão encerrou fluxos técnicos longos e produziu `MCP connection TTL reached`, fechamento do STDIO, respostas 502 e reinício do túnel.

O perfil gerenciado fixa `mcp.connection_max_ttl` em `336h` (14 dias). O Supervisor audita e restaura esse valor junto com o comando oficial. A margem permite sessões prolongadas sem recriar o túnel existente e não impõe uma janela fixa de conclusão.

## Limpeza de runtime órfão

Quando o túnel encerra após uma quebra do STDIO, o lançador Python e o servidor MCP podem permanecer vivos sem pertencer ao novo túnel. Esses processos órfãos mantêm uma instância inválida do runtime e podem provocar respostas 502, conexões duplicadas e falsas leituras de saúde.

O Supervisor identifica somente processos cuja linha de comando corresponde exatamente ao runtime MCP oficial e que não são descendentes do túnel ativo. Apenas essas árvores são encerradas; testes, builds e outros processos do projeto são preservados. A limpeza é registrada como `orphan_runtime_removed` no log, no estado, no heartbeat e no checkpoint. O teste de recuperação aplica o mesmo critério: processos auxiliares do túnel, como a ponte `codex app-server`, não são classificados como runtimes MCP órfãos.

## Observação prolongada controlada pelo operador

Não existe janela fixa obrigatória para concluir o projeto. Observações prolongadas são tarefas explícitas, configuradas pelo operador por `target_wall_seconds`. Elas registram PIDs iniciais do túnel e do MCP, amostras saudáveis, indisponibilidades transitórias e falhas. Uma troca real de PID ou três amostras consecutivas não saudáveis reprovam a janela. A observação de 280 horas iniciada em 31/07/2026 foi interrompida por ordem explícita do operador em 01/08/2026, após 2.037 amostras, 2.027 saudáveis, nenhuma falha definitiva e nenhuma troca de PID. Ela não deve ser retomada sem nova ordem explícita.

## Isolamento de timeout do PowerShell

O executor PowerShell não envia mais `CTRL_BREAK_EVENT`. Esse sinal produzia encerramentos `0xC000013A` no processo MCP durante cancelamentos ou timeouts, derrubando o STDIO e gerando respostas 502.

Cada comando agora é iniciado em grupo de processo próprio e sem janela de console. Em timeout ou cancelamento, somente a árvore filha é encerrada por PID. A ferramenta também reserva dois segundos do prazo solicitado para concluir a limpeza antes do limite externo do túnel.

Critério de aceitação executado: `Start-Sleep -Seconds 10` com prazo de 5 segundos retornou `Command execution timed out`; uma chamada `Health` imediatamente posterior confirmou o mesmo PID MCP `2480` ativo.
## Pacote local legado

O diretório local WINDOWS-MCP-GPT não é versionado. Quando utilizado, sua configuração deve iniciar diretamente .venv/Scripts/python.exe -m windows_mcp serve --transport stdio; uv run é proibido no runtime persistente.

## Semântica da observação prolongada

A observação de estabilidade distingue falha real de continuidade de indisponibilidade transitória da sonda de saúde. Uma troca explícita do PID do túnel ou do processo MCP reprova a janela imediatamente. Timeout isolado da API local, arquivo de saúde temporariamente indisponível ou ausência transitória de metadados não são classificados como troca de PID quando a árvore de processos confirma os mesmos processos.

Amostras transitórias são registradas separadamente. O contador é zerado após uma amostra saudável. Três amostras não saudáveis consecutivas reprovam a janela, alinhadas ao limite de recuperação do Supervisor. Essa regra impede falso positivo de quebra de continuidade durante uma chamada longa sem ocultar indisponibilidade persistente.
