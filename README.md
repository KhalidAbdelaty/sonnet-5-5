# Rivermark Reconciliation Agent

Rivermark is a runnable Python companion project for exploring the Claude Sonnet 5.5 Messages API. The agent investigates a fictional month-end revenue mismatch with read-only tools, submits evidence-backed correction plans, and receives limited write access only after Python validates a plan.

The project demonstrates:

- Adaptive thinking with an initial `medium` effort level
- Read-only investigation tools with strict schemas
- A deterministic Python plan gate
- Deferred tools exposed with mid-conversation tool changes
- Per-message effort changes after a failed independent check
- Prompt caching and cost tracking from API usage
- Final verification against a harness-only bank record

## Requirements

- Python 3.10 or newer
- An Anthropic API key with access to `claude-sonnet-5-5`

## Setup

On Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

On macOS or Linux:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Add your key to `.env`:

```text
ANTHROPIC_API_KEY=your_key_here
```

Do not commit `.env`.

## Run the Streamlit app

Regenerate the deterministic data and architecture image, then start the app:

```powershell
python build_data.py
python build_architecture.py
streamlit run app_streamlit.py
```

Use **Show the latest recorded run** to inspect the included run without making an API call. **Reconcile the month** starts a new billed API run.

## Run from the command line

Start a live run that begins at medium effort and escalates after a failed check:

```powershell
python run_experiment.py
```

After a main run saves a failure-point fork, run the matched control without escalating effort:

```powershell
python run_experiment.py --control
```

Both commands call the Anthropic API and bill the key in `.env`.

## Project structure

- `app_streamlit.py`: Streamlit interface and recorded-run viewer
- `run_experiment.py`: command-line runner
- `first_call.py`: smallest API example
- `build_data.py`: deterministic fictional close-data generator
- `build_architecture.py`: trust-boundary diagram generator
- `rivermark/`: agent loop, tools, plan gate, verifier, and cost calculation
- `data/`: model-visible records and the harness-only bank record
- `runs/`: one recorded main run and its matched control
- `assets/`: images required by the Streamlit interface

The agent never edits files under `data/`. Approved corrections are appended to `outputs/<run_id>/adjustments.jsonl`, and the final report is saved beside them.
