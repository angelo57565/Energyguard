import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..anomaly import FEATURES, detector
from ..database import get_db
from ..models import Reading, utcnow
from ..schemas import FeedbackIn, ReadingIn, ReadingOut
from ..security import require_api_key
from ..services import evaluate_alerts, maybe_train, notify_events

router = APIRouter(dependencies=[Depends(require_api_key)])


def _to_naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


@router.post("/readings", response_model=ReadingOut, status_code=201)
def create_reading(
    payload: ReadingIn,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    reading = Reading(**payload.model_dump())

    features = [getattr(reading, f) for f in FEATURES]
    result = detector.predict(features)
    if result is not None:
        reading.is_anomaly, reading.anomaly_score = result
        if reading.is_anomaly:
            reasons = detector.explain(features)
            if reasons:
                reading.reasons = json.dumps(reasons)

    db.add(reading)
    db.commit()
    db.refresh(reading)

    event_ids = evaluate_alerts(db, reading)
    if event_ids:
        background.add_task(notify_events, event_ids)
    background.add_task(maybe_train)
    return reading


@router.get("/readings", response_model=list[ReadingOut])
def list_readings(
    device_id: str | None = None,
    limit: int = Query(100, ge=1, le=20000),
    since: datetime | None = None,
    db: Session = Depends(get_db),
):
    """Últimas leituras, em ordem cronológica (da mais antiga para a mais nova)."""
    q = select(Reading)
    if device_id:
        q = q.where(Reading.device_id == device_id)
    since = _to_naive_utc(since)
    if since:
        q = q.where(Reading.created_at >= since)
    rows = db.scalars(q.order_by(Reading.id.desc()).limit(limit)).all()
    return list(reversed(rows))


@router.get("/readings/latest", response_model=ReadingOut)
def latest_reading(device_id: str | None = None, db: Session = Depends(get_db)):
    q = select(Reading)
    if device_id:
        q = q.where(Reading.device_id == device_id)
    row = db.scalars(q.order_by(Reading.id.desc()).limit(1)).first()
    if row is None:
        raise HTTPException(404, "Nenhuma leitura encontrada")
    return row


@router.put("/readings/{reading_id}/feedback", response_model=ReadingOut)
def set_feedback(reading_id: int, body: FeedbackIn, db: Session = Depends(get_db)):
    """Marca uma leitura como anomalia real ('real'), não-anomalia ('false') ou remove o rótulo."""
    row = db.get(Reading, reading_id)
    if row is None:
        raise HTTPException(404, "Leitura não encontrada")
    row.feedback = body.label
    db.commit()
    return row


@router.get("/anomalies", response_model=list[ReadingOut])
def list_anomalies(
    device_id: str | None = None,
    limit: int = Query(100, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    q = select(Reading).where(Reading.is_anomaly.is_(True))
    if device_id:
        q = q.where(Reading.device_id == device_id)
    return db.scalars(q.order_by(Reading.id.desc()).limit(limit)).all()


@router.get("/devices")
def list_devices(db: Session = Depends(get_db)):
    rows = db.execute(
        select(Reading.device_id, func.count(Reading.id), func.max(Reading.created_at))
        .group_by(Reading.device_id)
        .order_by(Reading.device_id)
    ).all()
    out = []
    for device, n, last in rows:
        latest = db.scalars(
            select(Reading).where(Reading.device_id == device).order_by(Reading.id.desc()).limit(1)
        ).first()
        out.append({
            "device_id": device,
            "readings": n,
            "last_seen": (last.isoformat(timespec="seconds") + "Z") if last else None,
            "rssi": latest.rssi if latest else None,
            "uptime_s": latest.uptime_s if latest else None,
            "send_failures": latest.send_failures if latest else None,
        })
    return out


@router.get("/summary")
def summary(
    device_id: str | None = None,
    hours: int = Query(24, ge=1, le=24 * 30),
    db: Session = Depends(get_db),
):
    since = utcnow() - timedelta(hours=hours)
    q = select(
        func.count(Reading.id),
        func.avg(Reading.voltage),
        func.avg(Reading.current),
        func.max(Reading.current),
        func.avg(Reading.power),
        func.max(Reading.power),
        func.min(Reading.energy),
        func.max(Reading.energy),
        func.sum(case((Reading.is_anomaly.is_(True), 1), else_=0)),
    ).where(Reading.created_at >= since)
    if device_id:
        q = q.where(Reading.device_id == device_id)
    n, v_avg, i_avg, i_max, p_avg, p_max, e_min, e_max, n_anom = db.execute(q).one()

    def r(x, nd=2):
        return round(float(x), nd) if x is not None else None

    consumed = (e_max - e_min) if (e_max is not None and e_min is not None) else None
    return {
        "device_id": device_id,
        "hours": hours,
        "readings": int(n or 0),
        "anomalies": int(n_anom or 0),
        "voltage_avg": r(v_avg),
        "current_avg": r(i_avg),
        "current_max": r(i_max),
        "power_avg": r(p_avg),
        "power_max": r(p_max),
        "energy_kwh": r(consumed, 4),
    }
