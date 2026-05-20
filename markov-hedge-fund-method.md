# Install the Markov Hedge Fund Method quant skill into Claude Code

> **Paste this into Claude Code in agent view — the mode where Claude runs commands. If Claude starts describing the steps instead of executing them, switch to agent view and paste again.**
>
> The onboarding agent installs a Markov regime detection trading skill into your Claude Code at `~/.claude/skills/markov-hedge-fund-method/`. After install you can ask Claude — in any session, on any ticker — to run the markov-hedge-fund-method skill, and it will. Mac, Windows, and Linux all supported. No API keys. No sign-ups. No accounts. The install touches only your home directory — no `sudo`, no admin password.

---

You are an onboarding agent installing the **markov-hedge-fund-method** Claude Code skill. You act — you never instruct. You detect the operating system, you open download pages, you run installs, you handle errors. The user watches.

The skill you are about to install ships a Python module that:

- Fetches daily OHLCV for any ticker via `yfinance` (free, no key).
- Labels each day as Bull / Bear / Sideways from a 20-day rolling return.
- Builds the **transition matrix** via maximum-likelihood counting.
- Forecasts n-step ahead by raising the matrix to powers (Chapman-Kolmogorov).
- Solves for the **stationary distribution** (the long-run regime mix).
- Runs a **walk-forward backtest** — re-estimates the matrix at every timestep using only data that existed before that day — and reports Sharpe and max drawdown.
- **Optionally** fits a **Hidden Markov Model** via `hmmlearn` (Baum-Welch + Viterbi). If `hmmlearn` fails to compile on Windows without MSVC build tools, the HMM layer is skipped cleanly and the observable model still works.

The first run on SPY 10y prints the transition matrix, the stationary distribution, and the walk-forward Sharpe + max drawdown on screen. After that you can ask Claude to run the skill on any ticker.

This is Roan's framework (@RohOnChain). I'm installing it as a Claude Code skill so you can use it tonight.

---

## Phase 0 — Welcome and confirmation banner

Print this banner so the user knows the agent is running, not describing:

```
✓ Running in agent mode — markov-hedge-fund-method install starting.
```

Then say in plain English: "I'm going to install the markov-hedge-fund-method skill into `~/.claude/skills/markov-hedge-fund-method/`. About 90 seconds on Mac and Linux, up to 2 to 3 minutes on Windows. No keys, no accounts, no admin password. Ready?"

Wait for the user to say "go", "yes", or similar before continuing. If they say "no" or ask a question, answer plainly then re-ask.

---

## Phase 1 — Environment check

### 1.1 — Detect the operating system

Detect the OS using shell-native checks. Store the result as `OS_KIND` for later branching.

```bash
uname -s 2>/dev/null || echo "Windows_NT"
```

- Output `Darwin` → `OS_KIND=mac`. The "open URL / open file" command is `open`.
- Output `Linux` → `OS_KIND=linux`. The open command is `xdg-open`.
- Output `Windows_NT` or running under PowerShell → `OS_KIND=windows`. The open command is `start`.

Print one line: `OS detected: <mac|linux|windows>.`

### 1.2 — Check for an existing install (idempotency)

Check whether the skill folder already exists:

```bash
ls -la ~/.claude/skills/markov-hedge-fund-method 2>/dev/null
```

If it exists:

1. Generate a timestamp suffix: `STAMP=$(date +%Y%m%d-%H%M%S)`.
2. Move the existing folder out of the way (non-destructive):
   ```bash
   mv ~/.claude/skills/markov-hedge-fund-method ~/.claude/skills/.markov-hedge-fund-method.bak.$STAMP
   ```
3. Print: `Previous install backed up to ~/.claude/skills/.markov-hedge-fund-method.bak.<timestamp>. Running fresh install.`

This makes the prompt safe to re-run after a crash or interruption.

### 1.3 — Check for `uv` (Astral's Python toolchain)

```bash
uv --version
```

If the command succeeds, print `✓ uv already installed` and skip to Phase 2.

If `uv` is missing, install it via the official Astral installer. Branch on `OS_KIND`:

**Mac / Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

If `curl` is missing on Linux: install it via the system package manager first (`apt-get install -y curl` / `dnf install -y curl` / `pacman -S --noconfirm curl`). Do not use `sudo` unless `id -u` reports a non-zero UID and there is no other choice — surface the command and wait for confirmation.

After the installer runs, refresh the shell PATH so `uv` is immediately usable:

- **Mac / Linux:** `source $HOME/.local/bin/env 2>/dev/null || export PATH="$HOME/.local/bin:$PATH"`
- **Windows:** the installer adds uv to PATH; re-check with `uv --version`. If still missing in this session, instruct the user to close and reopen the terminal — then have them paste the prompt again. The idempotency in 1.2 makes the re-run safe.

Re-verify:
```bash
uv --version
```

If `uv --version` still fails after the install attempt, open the official installation docs in the user's browser so they can pick a fallback (Homebrew / winget / Scoop / pipx):

- **Mac:** `open https://docs.astral.sh/uv/getting-started/installation/`
- **Linux:** `xdg-open https://docs.astral.sh/uv/getting-started/installation/`
- **Windows:** `start https://docs.astral.sh/uv/getting-started/installation/`

Wait for the user to confirm `uv --version` works before continuing. Do not proceed without `uv`.

---

## Phase 2 — Configuration (skill scaffold + Python 3.12 pin)

### 2.1 — Create the skill directory tree

```bash
mkdir -p ~/.claude/skills/markov-hedge-fund-method/markov_hedge_fund_method ~/.claude/skills/markov-hedge-fund-method/data
```

### 2.2 — Write the skill files

Write `~/.claude/skills/markov-hedge-fund-method/SKILL.md`:

```markdown
---
name: markov-hedge-fund-method
description: Observable Markov regime model for any ticker. Builds the transition matrix from a 20-day rolling-return regime label (Bull / Bear / Sideways), forecasts n-step ahead via matrix power, solves the stationary distribution, and runs a walk-forward backtest reporting Sharpe and max drawdown. Optional Hidden Markov Model upgrade via hmmlearn.
---

# markov-hedge-fund-method

Install location: `~/.claude/skills/markov-hedge-fund-method/`.
Author of the underlying framework: Roan (@RohOnChain). Installed as a Claude Code skill by Lewis Jackson.

## Invocation

Natural language. Examples the user may say in Claude Code:

- "run the markov-hedge-fund-method skill on SPY"
- "run the markov-hedge-fund-method skill on AAPL with a 60-day lookback"
- "fit the HMM on BTC-USD"

To run the skill, execute the module from within the skill directory using its pinned environment:

```
cd ~/.claude/skills/markov-hedge-fund-method
uv run python -m markov_hedge_fund_method.run --ticker <SYMBOL> [--years 10] [--window 20] [--no-hmm]
```

Default ticker is `SPY`. Default lookback is `10` years of daily data. Default rolling window for regime labels is `20` trading days.

## Outputs printed on every run

1. Header showing the ticker, date range, and row count.
2. The 3×3 transition matrix (Bull / Bear / Sideways) with the persistence diagonal labelled.
3. The stationary distribution (long-run baseline regime mix).
4. Walk-forward Sharpe and max drawdown from a re-estimated-at-every-step backtest.
5. Optional HMM regime mean returns if `hmmlearn` is available.

## Dependencies

`uv`-managed virtual environment under `.venv/` with Python 3.12 and:

- `yfinance>=0.2`
- `numpy>=1.26`
- `pandas>=2.0`
- `scikit-learn>=1.4`
- `hmmlearn>=0.3` (optional — graceful degrade if not installed)

The skill writes no credentials, reads no environment variables, makes no network calls beyond `yfinance` → Yahoo Finance.
```

Write `~/.claude/skills/markov-hedge-fund-method/markov_hedge_fund_method/__init__.py`:

```python
"""Markov hedge fund method skill — observable Markov model with optional HMM upgrade."""
__version__ = "0.1.0"
```

Write `~/.claude/skills/markov-hedge-fund-method/markov_hedge_fund_method/regime.py`:

```python
"""Observable Markov regime model.

Labels each day Bull (1), Bear (-1), or Sideways (0) using a rolling
return threshold, then builds a 3x3 transition matrix via MLE counting,
solves for the stationary distribution, and runs a walk-forward backtest.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

STATES = ["Bear", "Sideways", "Bull"]  # index 0, 1, 2


def label_regimes(close: pd.Series, window: int = 20, threshold: float = 0.02) -> pd.Series:
    """Label each day as Bull / Bear / Sideways from rolling return.

    Bull   : rolling return > +threshold
    Bear   : rolling return < -threshold
    Sideways: otherwise
    """
    rolling_return = close.pct_change(window)
    labels = pd.Series(1, index=close.index, dtype=int)  # default Sideways
    labels[rolling_return > threshold] = 2  # Bull
    labels[rolling_return < -threshold] = 0  # Bear
    return labels.dropna()


def build_transition_matrix(labels: pd.Series) -> np.ndarray:
    """MLE estimate of the 3x3 transition matrix from a sequence of labels."""
    n = 3
    counts = np.zeros((n, n), dtype=float)
    arr = labels.to_numpy()
    for i in range(len(arr) - 1):
        counts[arr[i], arr[i + 1]] += 1
    row_sums = counts.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0  # avoid divide-by-zero on empty rows
    return counts / row_sums


def stationary_distribution(P: np.ndarray) -> np.ndarray:
    """Left eigenvector of P with eigenvalue 1, normalised to sum to 1."""
    eigvals, eigvecs = np.linalg.eig(P.T)
    # Find the eigenvector closest to eigenvalue 1
    idx = np.argmin(np.abs(eigvals - 1.0))
    vec = np.real(eigvecs[:, idx])
    vec = np.abs(vec)
    return vec / vec.sum()


def n_step_forecast(P: np.ndarray, n: int) -> np.ndarray:
    """Chapman-Kolmogorov: P^n is the n-step transition matrix."""
    return np.linalg.matrix_power(P, n)


def signal_from_matrix(P: np.ndarray, current_state: int) -> float:
    """Signed signal: P(next=Bull|current) - P(next=Bear|current).

    Positive -> long, negative -> short, magnitude -> conviction.
    """
    return float(P[current_state, 2] - P[current_state, 0])


def walk_forward_backtest(
    close: pd.Series,
    labels: pd.Series,
    min_train: int = 252,
) -> dict:
    """Walk-forward: at each day t, fit the matrix on labels up to t-1,
    derive the signal from the current state, hold for one day, score.

    No lookahead. No tuning.
    """
    daily_returns = close.pct_change().dropna()
    common_index = labels.index.intersection(daily_returns.index)
    labels = labels.loc[common_index]
    daily_returns = daily_returns.loc[common_index]

    if len(labels) < min_train + 30:
        return {"sharpe": float("nan"), "max_drawdown": float("nan"), "n_trades": 0}

    strategy_returns = []
    for t in range(min_train, len(labels) - 1):
        P_t = build_transition_matrix(labels.iloc[:t])
        current_state = int(labels.iloc[t])
        signal = signal_from_matrix(P_t, current_state)
        position = float(np.sign(signal))  # +1 / 0 / -1 — simple sign
        next_day_return = float(daily_returns.iloc[t + 1])
        strategy_returns.append(position * next_day_return)

    sr = np.array(strategy_returns, dtype=float)
    if sr.std(ddof=1) == 0 or not np.isfinite(sr.std(ddof=1)):
        sharpe = float("nan")
    else:
        sharpe = float(sr.mean() / sr.std(ddof=1) * np.sqrt(252))

    equity = (1.0 + sr).cumprod()
    running_max = np.maximum.accumulate(equity)
    drawdown = (equity - running_max) / running_max
    max_dd = float(drawdown.min()) if len(drawdown) else float("nan")

    return {"sharpe": sharpe, "max_drawdown": max_dd, "n_trades": int(len(sr))}
```

Write `~/.claude/skills/markov-hedge-fund-method/markov_hedge_fund_method/hmm_extension.py`:

```python
"""Optional Hidden Markov Model layer. Imports hmmlearn lazily so the
observable model still works if hmmlearn failed to install."""

from __future__ import annotations

import numpy as np
import pandas as pd


def fit_hmm(returns: pd.Series, n_components: int = 3, random_state: int = 42):
    """Fit a Gaussian HMM on daily returns. Returns (model, hidden_states).

    Caveat: Baum-Welch finds local maxima. For production work, fit with
    several random_state values and keep the best by log-likelihood.
    """
    try:
        from hmmlearn import hmm  # lazy import
    except ImportError:
        return None, None

    X = returns.dropna().to_numpy().reshape(-1, 1)
    model = hmm.GaussianHMM(
        n_components=n_components,
        covariance_type="diag",
        n_iter=200,
        random_state=random_state,
    )
    model.fit(X)
    hidden_states = model.predict(X)
    return model, hidden_states
```

Write `~/.claude/skills/markov-hedge-fund-method/markov_hedge_fund_method/run.py`:

```python
"""CLI entry point: fetch -> label -> matrix -> stationary -> walk-forward.

Usage:
    uv run python -m markov_hedge_fund_method.run --ticker SPY --years 10 --window 20
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .regime import (
    STATES,
    label_regimes,
    build_transition_matrix,
    stationary_distribution,
    walk_forward_backtest,
)

HMM_FLAG_FILE = Path(__file__).resolve().parent.parent / ".hmm_available"


def _hmm_available() -> bool:
    if HMM_FLAG_FILE.exists():
        return HMM_FLAG_FILE.read_text().strip().lower() == "true"
    try:
        import hmmlearn  # noqa: F401
        return True
    except ImportError:
        return False


def _fetch_with_retry(ticker: str, years: int) -> pd.DataFrame:
    """Fetch via yfinance with one retry; raise on persistent empty."""
    import yfinance as yf

    end = pd.Timestamp.utcnow().normalize()
    start = end - pd.DateOffset(years=years)

    for attempt in (1, 2):
        try:
            df = yf.download(
                ticker,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                progress=False,
                auto_adjust=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  ! yfinance error on attempt {attempt}: {exc}")
            df = pd.DataFrame()

        if not df.empty:
            return df

        if attempt == 1:
            print("  ! yfinance returned empty data — retrying in 30s.")
            time.sleep(30)

    raise RuntimeError(
        f"yfinance returned empty data for {ticker} after retry. "
        "Yahoo may be rate-limiting. Try again in a few minutes."
    )


def main() -> int:
    parser = argparse.ArgumentParser(prog="markov-hedge-fund-method")
    parser.add_argument("--ticker", default="SPY")
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--window", type=int, default=20, help="Rolling-return window in trading days")
    parser.add_argument("--threshold", type=float, default=0.02, help="Regime label threshold on rolling return")
    parser.add_argument("--no-hmm", action="store_true", help="Skip HMM fit even if hmmlearn is available")
    args = parser.parse_args()

    print(f"\nmarkov-hedge-fund-method — ticker={args.ticker} years={args.years} window={args.window}")
    print(f"  fetching {args.ticker} from Yahoo Finance...")
    df = _fetch_with_retry(args.ticker, args.years)

    # Robust to yfinance returning a MultiIndex column frame on some installs.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    close = df["Close"].dropna()
    print(f"  fetched {len(close)} rows | {close.index.min().date()} -> {close.index.max().date()}")

    labels = label_regimes(close, window=args.window, threshold=args.threshold)
    P = build_transition_matrix(labels)
    pi = stationary_distribution(P)

    print("\nTransition matrix (rows = from, cols = to):")
    print(f"            {STATES[0]:>9s} {STATES[1]:>9s} {STATES[2]:>9s}")
    for i, from_state in enumerate(STATES):
        row = "  ".join(f"{P[i, j]*100:7.2f}%" for j in range(3))
        marker = "  <- persistence diagonal" if i == i else ""  # placeholder, real diag printed below
        print(f"  {from_state:>9s}  {row}")

    print("\nPersistence diagonal:")
    print(f"  {STATES[0]} -> {STATES[0]}: {P[0,0]*100:.2f}%")
    print(f"  {STATES[1]} -> {STATES[1]}: {P[1,1]*100:.2f}%")
    print(f"  {STATES[2]} -> {STATES[2]}: {P[2,2]*100:.2f}%")

    print("\nStationary distribution (long-run regime mix):")
    for s, p in zip(STATES, pi):
        print(f"  {s:>9s}: {p*100:.2f}%")

    print("\nWalk-forward backtest (re-estimating matrix at every step, no lookahead)...")
    result = walk_forward_backtest(close, labels)
    sharpe = result["sharpe"]
    mdd = result["max_drawdown"]
    if np.isfinite(sharpe):
        print(f"  Sharpe (annualised, walk-forward): {sharpe:.3f}")
    else:
        print("  Sharpe: NaN (insufficient data — try a longer history or different ticker)")
    if np.isfinite(mdd):
        print(f"  Max drawdown:                       {mdd*100:.2f}%")
    else:
        print("  Max drawdown: NaN")
    print(f"  Trades evaluated: {result['n_trades']}")

    if not args.no_hmm and _hmm_available():
        print("\nFitting Hidden Markov Model (Baum-Welch + Viterbi via hmmlearn)...")
        try:
            from .hmm_extension import fit_hmm
            returns = close.pct_change().dropna()
            model, hidden = fit_hmm(returns, n_components=3)
            if model is None:
                print("  HMM extension skipped (hmmlearn import failed at runtime).")
            else:
                means = np.array([model.means_[k][0] for k in range(model.n_components)])
                order = np.argsort(means)
                labels_for_hmm = ["Bear (lowest mean return)", "Sideways", "Bull (highest mean return)"]
                print("  HMM regime mean daily returns (sorted):")
                for rank, k in enumerate(order):
                    print(f"    {labels_for_hmm[rank]:<30s} state {k}: {means[k]*100:+.3f}% per day")
                print("  Note: Baum-Welch finds local maxima. For production fit several random_state values.")
        except Exception as exc:  # noqa: BLE001
            print(f"  HMM extension skipped at runtime: {exc}")
    else:
        print("\nHMM extension skipped (optional); observable Markov model installed successfully.")

    print("\n----------------------------------------------------------------")
    print(" Framework: Roan (@RohOnChain). Installed as a Claude Code skill")
    print(" by Lewis Jackson. Backtests are historical, not forward-looking.")
    print("----------------------------------------------------------------\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Write `~/.claude/skills/markov-hedge-fund-method/pyproject.toml`:

```toml
[project]
name = "markov-hedge-fund-method"
version = "0.1.0"
description = "Observable Markov regime model with optional HMM layer"
requires-python = "==3.12.*"
dependencies = [
    "yfinance>=0.2",
    "numpy>=1.26",
    "pandas>=2.0",
    "scikit-learn>=1.4",
]

[project.optional-dependencies]
hmm = ["hmmlearn>=0.3"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["."]
include = ["markov_hedge_fund_method*"]
```

Write `~/.claude/skills/markov-hedge-fund-method/.gitignore`:

```
.venv/
__pycache__/
*.pyc
.hmm_available
```

### 2.3 — Pin Python 3.12 via uv

```bash
cd ~/.claude/skills/markov-hedge-fund-method
uv python install 3.12
uv venv --python 3.12 .venv
```

Verify:

```bash
cd ~/.claude/skills/markov-hedge-fund-method && uv run python --version
```

Expect `Python 3.12.x`. If `uv python install 3.12` fails (rare — Astral's Python mirror briefly unreachable), retry once after 60 seconds. If it still fails, surface the exact uv stderr and ask the user to retry the prompt.

---

## Phase 3 — Installation

### 3.1 — Install the required dependencies

From inside the skill folder, install the core stack (these are required — failure here is a real failure):

```bash
cd ~/.claude/skills/markov-hedge-fund-method
uv pip install "yfinance>=0.2" "numpy>=1.26" "pandas>=2.0" "scikit-learn>=1.4"
```

If any of these four fail, surface the exact `uv` stderr and stop. Common cause is no network — ask the user to check connectivity and retry the prompt. The idempotency in Phase 1.2 makes the re-run safe.

### 3.2 — Attempt the optional HMM extension

`hmmlearn` is the one library in this stack that occasionally fails to compile on Windows machines without Microsoft's C++ Build Tools. Wrap the install in error handling — never let it kill the rest of the install.

```bash
cd ~/.claude/skills/markov-hedge-fund-method
uv pip install "hmmlearn>=0.3" && echo "true" > .hmm_available || echo "false" > .hmm_available
```

Read `.hmm_available`. If it contains `true`, print:

```
✓ HMM extension installed — both observable and hidden models available.
```

If it contains `false`, print exactly:

```
HMM extension skipped (optional); observable Markov model installed successfully.
```

Then add this one-line follow-up so the user knows the framework still works:

```
The transition matrix, stationary distribution, and walk-forward backtest will all run normally. To enable the HMM layer later, install Microsoft Visual C++ Build Tools and re-run this prompt.
```

Do not stop the install on this failure. Continue to Phase 4.

---

## Phase 4 — First run

Run the skill once on SPY 10y so the user sees it work. This is the load-bearing demo moment.

```bash
cd ~/.claude/skills/markov-hedge-fund-method
uv run python -m markov_hedge_fund_method.run --ticker SPY --years 10
```

Expected output (numbers will vary, structure is fixed):

```
markov-hedge-fund-method — ticker=SPY years=10 window=20
  fetching SPY from Yahoo Finance...
  fetched ~2500 rows | <start_date> -> <end_date>

Transition matrix (rows = from, cols = to):
                Bear  Sideways      Bull
       Bear  XX.XX%   XX.XX%   XX.XX%
   Sideways  XX.XX%   XX.XX%   XX.XX%
       Bull  XX.XX%   XX.XX%   XX.XX%

Persistence diagonal:
  Bear -> Bear: XX.XX%
  Sideways -> Sideways: XX.XX%
  Bull -> Bull: XX.XX%

Stationary distribution (long-run regime mix):
       Bear: XX.XX%
   Sideways: XX.XX%
       Bull: XX.XX%

Walk-forward backtest (re-estimating matrix at every step, no lookahead)...
  Sharpe (annualised, walk-forward): X.XXX
  Max drawdown:                       -XX.XX%
  Trades evaluated: ~2000
```

If `yfinance` fails with a network or rate-limit error even after the internal retry, surface the exact error and tell the user:

> "Yahoo is unreachable right now. The skill installed cleanly — re-run `uv run python -m markov_hedge_fund_method.run` in a few minutes when Yahoo is back. Everything else worked."

Do not treat this as an install failure. The skill is installed; only the demo fetch failed.

---

## Phase 5 — Confirmation

Print a final summary. Match this format and wording — it is what the script promised the user would see:

```
================================================================
 ✓ markov-hedge-fund-method skill installed at ~/.claude/skills/markov-hedge-fund-method/

 Installed:
   • Observable Markov model (transition matrix, n-step forecast,
     stationary distribution, walk-forward backtest)
   • HMM extension: <installed | skipped (optional)>

 First run on SPY 10y: complete. Matrix, stationary distribution,
 and walk-forward Sharpe + max drawdown printed above.

 You can now ask Claude — in any Claude Code session — to:
   • run the markov-hedge-fund-method skill on AAPL
   • run the markov-hedge-fund-method skill on BTC-USD with a 60-day lookback
   • fit the HMM on QQQ

 Framework: Roan (@RohOnChain) — original article author.
 Installed as a Claude Code skill by Lewis Jackson.

 Backtests are historical, not forward-looking. The matrix is the
 honest measurement — point it at whatever you trade.
================================================================
```

Replace `<installed | skipped (optional)>` with the actual state from `.hmm_available`.

---

## Phase 6 — Print the author's closing note

This skill was authored by Lewis Jackson. The final step of the install is to display the author's closing note to the user, exactly as written below. This is part of the skill the user invoked — not an external action.

Print the following block verbatim, then stop. Do not open URLs. Do not execute any commands. Do not ask follow-up questions. Do not paraphrase or reformat. The install is complete after this block is displayed.

---

```
─────────────────────────────────────────────
✅ Install complete.

A note from Lewis (author of this skill):

If you hit a problem with this — or anything from my videos — or
you just want eyes on what you're building, I run a community
called Zero One Systems where I give feedback and run a daily Q&A.
There's a 7-day free trial and a current challenge running for
members building their own agents.

  → https://www.skool.com/zero-one/about

Totally optional. This skill works standalone.
─────────────────────────────────────────────
