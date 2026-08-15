# verification/ — standalone verification scripts

One script per verified claim or requirement. Rules (from `../../goal.md` §2):

- **Standalone & runnable** via the project `.venv` (e.g. `python verify_per_phase_routing.py`).
- **Exit non-zero on failure** so a verifier subagent gets a clean pass/fail signal.
- **Capture output** alongside the script: `verify_<topic>.out.txt`.
- **Run by a SEPARATE verifier subagent**, never by the agent that wrote the code/claim.

Naming: `verify_<topic>.py`. Example topics: `per_phase_routing`, `gate_runner_parallel`,
`env_vs_real_classification`, `merge_queue_serialization`, `skip_create_story`.
