# churn-model — Project Memory

_Last updated: 2026-06-18_

## What it is

CatBoost probability-of-churn scorer for ClientA's SaaS platform. Uses enrichment features only (no raw PII). Shipped as a `.joblib` wrapper + CLI; on-prem deployable. AUC ~0.72 on holdout, KS ~0.38.

## Status

- Stage 09 (final training + packaging) complete.
- Joblib wrapper shipped to ClientA's environment.
- Model card written and approved.
- Open: engagement feature fill-rate collapse on May 2026 batches unexplained — see issues below.

## Pipeline stages

```
01_extract.py          → pull partner extract, validate schema
02_filter_split.py     → apply eligibility filters, train/val/test split (stratified)
03_audit.py            → IV/PSI audit per feature; flag leakers and unstable features
04_build_features.py   → WoE encoding (OptBinning), presence flags, REF_YEAR anchoring
05_bakeoff.py          → 6 frameworks, default params, composite score ranking
06_grid.py             → grid search on top 2 frameworks
07_v2.py               → feature selection (PSI gate → correlation pruning → SHAP rank cap 50)
08_optuna_tune.py      → Optuna, 30 trials, TPE, val_auc − 2·max(0, gap − 0.03)
09_train_final_and_package.py  → retrain on train+val, package to joblib, write MODEL_CARD.md
```

## Model details

| Metric | Value |
|--------|-------|
| Algorithm | CatBoost |
| AUC (holdout) | ~0.72 |
| KS (holdout) | ~0.38 |
| Gini | ~0.44 |
| Top decile lift | ~2.1× |
| Features (final) | 38 |
| Overfit gap | 0.018 (well within 0.03 threshold) |
| Score PSI (train vs test) | 0.042 (stable) |

## Key hyperparameters (from `optuna_best.json`)

```json
{
  "depth": 6,
  "learning_rate": 0.014,
  "l2_leaf_reg": 4.5,
  "iterations": 720,
  "class_weights": "balanced"
}
```

## Deployment notes

- Clients receive only pseudonymized IDs (hashed email/phone/device). Raw PII never leaves their environment.
- Joblib artifact: `package/churn_scorer_v1.2.joblib` — contains preprocessing pipeline + estimator + feature schema + version + thresholds.
- Inference: `python3 score.py --input batch.csv --output scored.csv`
- `REF_YEAR=2026` baked into age-derived feature computation. Retrain when reference year is > 12 months stale.

## Known issues / open

- Engagement feature fill-rate collapse on some client batches (4× lower than training distribution). Root cause unknown; may reflect partner data pipeline changes on ClientA's side. Monitor fill rate before every scoring run; skip engagement features if fill < 20%.
- AUC ceiling at ~0.72 with single-partner enrichment. Multi-partner join is the only clear path above it; cross-partner join not yet built.

---

## Lessons captured inline

[GOTCHA]: `REF_YEAR` is baked into all age-derived features. If you retrain more than 12 months after the original training date without updating `REF_YEAR`, age buckets silently drift and PSI spikes. Add a staleness check at the top of `04_build_features.py`.

[LESSON]: Engagement features (social_network_score, active_days_last_30) drop to ~25% fill rate on some client batches. Do NOT impute with mean — the distribution of low-fill batches is structurally different from training. Either (a) monitor fill rate and gate on it, or (b) train a fill-stratified model. Imputing silently skewed predictions to middle deciles in testing.

[PATTERN]: Stage 09 is the canonical reproducibility entrypoint. If someone asks "how was this model trained?", point them to `09_train_final_and_package.py`. It seeds everything, loads `optuna_best.json`, retrains on train+val, evaluates on held-out test, and writes the artifact + MODEL_CARD in one run.

[GOTCHA]: One feature SHAP-dominating 3–5× the next is a leakage red flag, not a win. In an early run, `days_since_last_login` dominated SHAP at 4.2× the next feature. Investigation revealed it was being computed from data that included post-churn activity in the training window — a classic leakage. Dropped and retrained.
