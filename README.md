# Case-Study-Marketing-Data-Science-Tchibo
Starting Situation &amp; Challenge Objective: The marketing department requires a forecast model to predict total daily demand (sales) for operational control using the marketing budget and other relevant factors. Performance Target: The quotient of the Mean Absolute Error (MAE) and the average sales value must not exceed 15%.

## Setup

Requires [uv](https://docs.astral.sh/uv/). Python 3.12 is pinned via `.python-version`.

```bash
uv sync                  # one .venv: core analysis, Jupyter/dev tools, Google Meridian (TensorFlow + JAX),
                         # weather data and the MMM Studio backend (src/app/backend, editable)
uv run jupyter lab
```

See [PLAN.md](PLAN.md) for the project plan.

## Forecasting a new budget plan

1. Fill a copy of [data/input/budget_plan_template.csv](data/input/budget_plan_template.csv):
   - **Rows:** one per future day, consecutive, starting the day after the last history day.
   - **Columns:** the planned daily cost in € per paid channel (`sea, catalog, display, affiliate, display_network, social_media, psm`).
   - **Catalog:** put its full cost on the send-out day, as in the history.
2. Run the forecast. It refits the Meridian model with the plan days as hold-out rows, which takes about 2–3 minutes on CPU:

   ```bash
   uv run tchibo-forecast --plan data/input/budget_plan_2025-01.csv --out outputs/forecast_2025-01.csv
   ```

3. The output has one row per day with these columns:
   - `forecast`: the expected sales.
   - `prediction_lo` / `prediction_hi`: a 90 % interval for an actual day.
   - `credible_lo` / `credible_hi`: uncertainty of the expected level only.
   - `planned_spend`: the plan's total spend for that day.

[notebooks/05_forecast.ipynb](notebooks/05_forecast.ipynb) shows two more things:

- **A leak-free back-test:** a forecast of Q4 2024 from the budget alone scores 8.5 % MAE / mean.
- **Budget scenarios:** `forecast.scenario(result, alt_plan)` evaluates alternative plans in seconds.

## Standalone Web Application (MMM Studio)

A self-contained full-stack web application (`FastAPI` + `PostgreSQL/Procrastinate` + `React/TypeScript/MUI/Plotly`) that reproduces all exploratory analyses, feature engineering, OLS benchmarks, Google Meridian Bayesian MMM fits, budget optimisations, and interactive 2025 scenario forecasts from a single uploaded `.xlsx` workbook is located in [`src/app/`](src/app/README.md).
