# Security boundary

The private Python runner detects source changes, but hashing is not a security
sandbox.

Run participant code offline in a container, virtual machine, or restricted
operating-system account. It should access only:

- the frozen submission
- today's observation path
- today's action path
- its private memory folder for the current seed

Never expose:

- `official_runner/private_runtime/`
- `official_runner/private_config/`
- `official_runner/private_inputs/`
- generated private scenarios
- Oracle source
- other teams' submissions or logs

Do not regenerate final seeds after seeing a participant result.

