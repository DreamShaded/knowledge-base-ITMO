"""
KGE prediction generator (ADR-0009 §3-4).
Produces predictions.parquet and writes to <urn:predictions> in Oxigraph.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.logging import get_logger

log = get_logger(__name__)

_PKM = "https://my-pkm.local/ontology#"
_MAX_ENTITIES_PER_SIDE = 500  # cap for very large graphs


def generate_predictions(model, factory, top_k: int = 10) -> list[dict]:
    """
    Return top-K (head, relation, tail, score) predictions per entity.
    Uses PyKEEN predict_target for head and tail prediction.
    """
    import pandas as pd
    from pykeen.predict import predict_target

    entity_labels = list(factory.entity_to_id.keys())[:_MAX_ENTITIES_PER_SIDE]
    relation_labels = list(factory.relation_to_id.keys())
    predictions = []

    for head_label in entity_labels:
        for rel_label in relation_labels:
            try:
                result = predict_target(
                    model=model,
                    head=head_label,
                    relation=rel_label,
                    triples_factory=factory,
                    target="tail",
                )
                df = result.df.nlargest(top_k, "score")
                for _, row in df.iterrows():
                    tail_label = row.get("tail_label", "")
                    if not tail_label:
                        continue
                    predictions.append({
                        "head_uri": head_label,
                        "predicted_relation": rel_label,
                        "tail_uri": tail_label,
                        "score": float(row["score"]),
                        "head_label": _short_label(head_label),
                        "tail_label": _short_label(tail_label),
                    })
            except Exception as exc:
                log.debug("predict_target_skip", head=head_label, rel=rel_label, err=str(exc))

    log.info("kge_predictions_generated", count=len(predictions))
    return predictions


def save_predictions_parquet(predictions: list[dict], path: Path) -> None:
    import pandas as pd

    df = pd.DataFrame(predictions)
    if df.empty:
        df = pd.DataFrame(columns=["head_uri", "predicted_relation", "tail_uri", "score",
                                   "head_label", "tail_label"])
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    log.info("kge_parquet_saved", path=str(path), rows=len(df))


def write_predictions_to_oxigraph(
    predictions: list[dict],
    oxigraph,
    run_id: str,
) -> None:
    """Write top predictions into <urn:predictions> with score + metadata."""
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    inserted = 0
    for pred in predictions:
        h = pred["head_uri"]
        r = pred["predicted_relation"]
        t = pred["tail_uri"]
        score = pred["score"]
        try:
            # Triple itself
            oxigraph.sparql_update(f"""
                INSERT DATA {{
                    GRAPH <urn:predictions> {{
                        <{h}> <{r}> <{t}> .
                        <{h}> <{_PKM}kgeScore> "{score}"^^<http://www.w3.org/2001/XMLSchema#double> .
                        <{h}> <{_PKM}kgeRunId> "{run_id}" .
                        <{h}> <{_PKM}kgeGeneratedAt> "{now}"^^<http://www.w3.org/2001/XMLSchema#dateTime> .
                    }}
                }}
            """)
            inserted += 1
        except Exception as exc:
            log.debug("kge_oxigraph_write_skip", h=h, r=r, t=t, err=str(exc))

    log.info("kge_predictions_written_to_graph", inserted=inserted)


def _short_label(uri: str) -> str:
    part = uri.split("#")[-1] if "#" in uri else uri.split("/")[-1]
    return part
