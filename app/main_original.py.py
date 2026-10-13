"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
from importlib import reload
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from pymongo import MongoClient

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]
db.employees.create_index("emp_code", unique=True)
db.employees.create_index([("department", 1), ("joined_on", 1)])


db.attendance_logs.create_index(
    [("date", 1), ("late_minutes", 1), ("emp_code", 1)]
)

db.attendance_logs.create_index(
    [("date", -1), ("emp_code", 1), ("status", 1)]
)

db.attendance_logs.create_index(
    [("emp_code", 1), ("date", 1)],
    unique=True,
    name="unique_employee_attendance_date",
)


IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    punch_in_ist = punch_in.astimezone(IST)

    h, m = map(int, shift_start.split(":"))
    shift_start_time = punch_in_ist.replace(
        hour=h, minute=m, second=0, microsecond=0
    )

    elapsed_minutes = int(
        (punch_in_ist - shift_start_time).total_seconds() // 60
    )

    return elapsed_minutes if elapsed_minutes > 10 else 0



def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    return round((punch_out - punch_in).total_seconds() / 3600, 2)



def compute_overtime(punch_out, shift_end, date_str):
    from datetime import time

    h, m = map(int, shift_end.split(":"))
    end = datetime.fromisoformat(date_str).replace(
        hour=h, minute=m, second=0, microsecond=0, tzinfo=IST
    )

    if punch_out.tzinfo is None:
        punch_out = punch_out.replace(tzinfo=timezone.utc)

    elapsed_minutes = int((punch_out - end).total_seconds() // 60)

    return elapsed_minutes if elapsed_minutes >= 30 else 0



# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #

@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(status_code=409, detail="emp_code already exists")

    doc = body.model_dump()
    doc["created_at"] = int(datetime.now(timezone.utc).timestamp() * 1000)

    try:
        db.employees.insert_one(doc)
    except Exception:
        if db.employees.find_one({"emp_code": body.emp_code}):
            raise HTTPException(status_code=409, detail="emp_code already exists")
        raise

    return db.employees.find_one(
        {"emp_code": body.emp_code},
        {"_id": 0}
    )


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    q = {}

    if department:
        q["department"] = department

    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)

    items = list(
        db.employees.find(q, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# --------------------------------------------------------------------------- #
# NEW: Punch In
# --------------------------------------------------------------------------- #

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    from datetime import time
    from pymongo.errors import DuplicateKeyError

    emp = db.employees.find_one({"emp_code": body.emp_code})

    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.status not in ["PRESENT", "WFH", "ON_DUTY"]:
        raise HTTPException(
            status_code=422,
            detail="Status must be PRESENT, WFH, or ON_DUTY"
        )

    if emp["shift_start"] == emp["shift_end"]:
        raise HTTPException(
            status_code=422,
            detail="Shift start and end must be different"
        )

    if body.punched_at is not None and body.punched_at != 0:
        if (
            isinstance(body.punched_at, bool)
            or not isinstance(body.punched_at, int)
            or body.punched_at < 100000000000
        ):
            raise HTTPException(
                status_code=422,
                detail="punched_at must be epoch milliseconds"
            )

        ts = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc
        ).replace(microsecond=0)
    else:
        ts = datetime.now(timezone.utc).replace(microsecond=0)

    local_ts = ts.astimezone(IST)
    shift_start_hour, shift_start_minute = map(
        int, emp["shift_start"].split(":")
    )
    shift_end_hour, shift_end_minute = map(
        int, emp["shift_end"].split(":")
    )

    attendance_date = local_ts.date()

    # Overnight shift: before shift-end time belongs to previous date.
    if (
        emp["shift_end"] <= emp["shift_start"]
        and local_ts.time() < time(shift_end_hour, shift_end_minute)
    ):
        attendance_date -= timedelta(days=1)

    date_str = attendance_date.isoformat()

    shift_start_dt = datetime.combine(
        attendance_date,
        time(shift_start_hour, shift_start_minute)
    ).replace(tzinfo=IST)

    elapsed_minutes = int(
        (local_ts - shift_start_dt).total_seconds() // 60
    )

    late_minutes = max(0, elapsed_minutes) if elapsed_minutes > 10 else 0

    record = {
        "emp_code": body.emp_code,
        "date": date_str,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_minutes,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(record)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="Attendance already exists for this date"
        )

    return db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "date": date_str},
        {"_id": 0}
    )


# --------------------------------------------------------------------------- #
# List Attendance
# --------------------------------------------------------------------------- #

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    filters = {}

    if emp_code:
        filters["emp_code"] = emp_code

    if date_from or date_to:
        filters["date"] = {}

        if date_from:
            filters["date"]["$gte"] = date_from

        if date_to:
            filters["date"]["$lte"] = date_to

    if status:
        filters["status"] = status

    total = db.attendance_logs.count_documents(filters)

    skip = (page - 1) * page_size

    records = db.attendance_logs.find(
        filters,
        {"_id": 0},
    ).sort(
        [("date", -1), ("emp_code", 1)]
    ).skip(skip).limit(page_size)

    items = list(records)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }



@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if not emp:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    if body.punched_at is not None and body.punched_at != 0:
        if body.punched_at < 100000000000:
            raise HTTPException(
                status_code=422,
                detail="Use epoch milliseconds for punched_at",
            )

        ts = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc,
        ).replace(microsecond=0)
    else:
        ts = datetime.now(timezone.utc).replace(microsecond=0)

    # Find the latest open attendance record for this employee.
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_out": None,
            "punch_in": {"$lte": ts},
        },
        sort=[("punch_in", -1)],
    )

    if not record:
        existing = db.attendance_logs.find_one(
            {
                "emp_code": body.emp_code,
                "punch_out": {"$ne": None},
            },
            sort=[("punch_in", -1)],
        )

        if existing:
            raise HTTPException(
                status_code=409,
                detail="Attendance already punched out",
            )

        raise HTTPException(
            status_code=404,
            detail="No punch-in record found",
        )

    punch_in_time = record["punch_in"]

    # Normalize timestamps before subtraction.
    if punch_in_time.tzinfo is None:
        punch_in_time = punch_in_time.replace(tzinfo=timezone.utc)

    duration = (ts - punch_in_time).total_seconds()

    if duration <= 0:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in",
        )

    if duration > 86400:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be within 24 hours",
        )

    work_hours = compute_work_hours(punch_in_time, ts)

    overtime = compute_overtime(
        ts,
        emp["shift_end"],
        record["date"],
    )

    result = db.attendance_logs.update_one(
        {
            
    {
        "_id": record["_id"],
        "status": old.get("status"),
        "punch_in": old.get("punch_in"),
        "punch_out": old.get("punch_out"),
    },

                  },
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "overtime_minutes": overtime,
                "half_day": round(work_hours, 2) < 4.50,
            }
        },
    )

    if result.modified_count == 0:
        raise HTTPException(
            status_code=409,
            detail="Attendance record was already closed",
        )

    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]},
        {"_id": 0},
    )

    return updated
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from fastapi import HTTPException
from pydantic import BaseModel
from typing import Optional


class RegularizeRequest(BaseModel):
    reason: str
    regularized_by: str
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: RegularizeRequest,
):
    allowed_statuses = {
        "PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"
    }

    if len(body.reason.strip()) < 5 or len(body.reason) > 200:
        raise HTTPException(
            status_code=422,
            detail="Reason must be between 5 and 200 characters",
        )

    if not body.regularized_by.strip() or len(body.regularized_by) > 50:
        raise HTTPException(
            status_code=422,
            detail="Invalid regularized_by",
        )

    if body.status is not None and body.status not in allowed_statuses:
        raise HTTPException(status_code=422, detail="Invalid status")

    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format")

    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date,
    })

    if not record:
        raise HTTPException(
            status_code=404,
            detail="Attendance record not found",
        )

    old = dict(record)
    new_status = body.status if body.status is not None else old["status"]

    if new_status in {"ABSENT", "LEAVE"}:
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(
                status_code=422,
                detail="ABSENT or LEAVE cannot include punch times",
            )
        punch_in = None
        punch_out = None
    else:
        punch_in = old.get("punch_in")
        punch_out = old.get("punch_out")

        if body.punch_in is not None:
            if not 100000000000 <= body.punch_in <= 4102444800000:
                raise HTTPException(status_code=422, detail="Invalid punch_in")
            punch_in = datetime.fromtimestamp(
                body.punch_in / 1000, timezone.utc
            ).replace(microsecond=0)

        if body.punch_out is not None:
            if not 100000000000 <= body.punch_out <= 4102444800000:
                raise HTTPException(status_code=422, detail="Invalid punch_out")
            punch_out = datetime.fromtimestamp(
                body.punch_out / 1000, timezone.utc
            ).replace(microsecond=0)

        if punch_in is None:
            raise HTTPException(
                status_code=422,
                detail="Presence status requires punch_in",
            )

        # Compare attendance date using IST.
        ist = timezone(timedelta(hours=5, minutes=30))
        if punch_in.replace(tzinfo=timezone.utc).astimezone(ist).date().isoformat() != date:
            raise HTTPException(
                status_code=422,
                detail="punch_in must fall on the attendance date",
            )

        

        if punch_out is not None and punch_out.tzinfo is None:
            punch_out = punch_out.replace(tzinfo=timezone.utc)

        if punch_out is not None:
            if punch_out <= punch_in:
                raise HTTPException(
                    status_code=422,
                    detail="punch_out must be after punch_in",
                )

                    # Normalize timestamps before comparison.
        if punch_in.tzinfo is None:
            punch_in = punch_in.replace(tzinfo=timezone.utc)

        if punch_out is not None and punch_out.tzinfo is None:
            punch_out = punch_out.replace(tzinfo=timezone.utc)

        if punch_out is not None:
            if punch_out <= punch_in:
                raise HTTPException(
                    status_code=422,
                    detail="punch_out must be after punch_in",
                )

            if (punch_out - punch_in).total_seconds() > 86400:
                raise HTTPException(
                    status_code=422,
                    detail="Punch duration cannot exceed 24 hours",
                )
            

    def epoch_ms(value):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp() * 1000)

    def rounded_hours(start, end):
        seconds = Decimal(str((end - start).total_seconds()))
        return float((seconds / Decimal("3600")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        ))

    def calculate_late(start):
        if start is None:
            return 0
        ist = timezone(timedelta(hours=5, minutes=30))
        local_start = start.replace(tzinfo=timezone.utc).astimezone(ist)
        shift_h, shift_m = map(int, emp["shift_start"].split(":"))
        shift_start = local_start.replace(
            hour=shift_h, minute=shift_m, second=0, microsecond=0
        )
        minutes = int((local_start - shift_start).total_seconds() // 60)
        return minutes if minutes > 10 else 0

    changes = {}
    proposed = {
        "status": new_status,
        "punch_in": punch_in,
        "punch_out": punch_out,
    }

    for field, new_value in proposed.items():
        old_value = old.get(field)
        if field in {"punch_in", "punch_out"}:
            if epoch_ms(old_value) != epoch_ms(new_value):
                changes[field] = {
                    "from": epoch_ms(old_value),
                    "to": epoch_ms(new_value),
                }
        elif old_value != new_value:
            changes[field] = {"from": old_value, "to": new_value}

    if punch_out is None:
        work_hours = None
        overtime = 0
        half_day = False
    else:
        work_hours = rounded_hours(punch_in, punch_out)
        ist = timezone(timedelta(hours=5, minutes=30))
        local_out = punch_out.replace(tzinfo=timezone.utc).astimezone(ist)
        end_h, end_m = map(int, emp["shift_end"].split(":"))
        shift_end = local_out.replace(
            hour=end_h, minute=end_m, second=0, microsecond=0
        )
        if (end_h, end_m) <= tuple(map(int, emp["shift_start"].split(":"))):
            shift_end += timedelta(days=1)
        overtime_minutes = int(
            (local_out - shift_end).total_seconds() // 60
        )
        overtime = overtime_minutes if overtime_minutes >= 30 else 0
        half_day = work_hours < 4.50

    derived = {
        "late_minutes": calculate_late(punch_in),
        "work_hours": work_hours,
        "overtime_minutes": overtime,
        "half_day": half_day,
    }

    for field, value in derived.items():
        if old.get(field) != value:
            changes[field] = {"from": old.get(field), "to": value}

    if not changes:
        raise HTTPException(
            status_code=422,
            detail="Request does not change the record"
        )

    history_entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "status": old.get("status"),
            "punch_in": old.get("punch_in"),
            "punch_out": old.get("punch_out"),
        },
        {
            "$set": {
                "status": new_status,
                "punch_in": punch_in,
                "punch_out": punch_out,
                **derived,
            },
            "$push": {"history": history_entry},
        },
    )

    if result.modified_count == 0:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently",
        )

    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]}, {"_id": 0}
    )

    updated["punch_in"] = epoch_ms(updated.get("punch_in"))
    updated["punch_out"] = epoch_ms(updated.get("punch_out"))

    for entry in updated.get("history", []):
        entry["at"] = epoch_ms(entry.get("at"))
        for field in ("punch_in", "punch_out"):
            if field in entry.get("changes", {}):
                for key in ("from", "to"):
                    value = entry["changes"][field].get(key)
                    if isinstance(value, datetime):
                        entry["changes"][field][key] = epoch_ms(value)

    return updated
# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}

# --------------------------------------------------------------------------- #

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly_analytics(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
):
    from calendar import monthrange
    from decimal import Decimal, ROUND_HALF_UP

    emp = db.employees.find_one(
        {"emp_code": emp_code},
        {"_id": 0, "emp_code": 1, "joined_on": 1},
    )
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    try:
        year, mon = map(int, month.split("-"))
        first_day = datetime(year, mon, 1).date()
        last_day = datetime(
            year, mon, monthrange(year, mon)[1]
        ).date()
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid month")

    joined_on = datetime.strptime(emp["joined_on"], "%Y-%m-%d").date()
    start_day = max(first_day, joined_on)

    working_days = 0
    if start_day <= last_day:
        current = start_day
        while current <= last_day:
            if current.weekday() < 5:
                working_days += 1
            current += timedelta(days=1)

    records = list(db.attendance_logs.find(
        {
            "emp_code": emp_code,
            "date": {"$gte": month + "-01", "$lte": last_day.isoformat()},
        },
        {"_id": 0, "date": 1, "status": 1, "half_day": 1,
         "late_minutes": 1, "overtime_minutes": 1},
    ))

    present_statuses = {"PRESENT", "WFH", "ON_DUTY"}
    present_days = sum(
        (0.5 if r.get("half_day", False) else 1.0)
        for r in records
        if r.get("status") in present_statuses
        and datetime.strptime(
            r["date"], "%Y-%m-%d"
        ).date().weekday() < 5
    )
    leave_days = sum(1 for r in records if r.get("status") == "LEAVE")
    late_records = [r for r in records if r.get("late_minutes", 0) > 0]
    total_late_minutes = sum(r.get("late_minutes", 0) for r in records)
    total_overtime_minutes = sum(r.get("overtime_minutes", 0) for r in records)

    attendance_pct = None
    if working_days > 0:
        attendance_pct = float(
            (Decimal(str(present_days)) / Decimal(working_days) * 100)
            .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": len(late_records),
        "total_late_minutes": total_late_minutes,
        "total_overtime_minutes": total_overtime_minutes,
        "attendance_pct": attendance_pct,
    }

@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    department: Optional[str] = None,
):
    from calendar import monthrange

    try:
        year, mon = map(int, month.split("-"))
        last_day = datetime(
            year, mon, monthrange(year, mon)[1]
        ).date().isoformat()
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid month")

    pipeline = [
        {
            "$match": {
                "joined_on": {"$lte": last_day},
                **({"department": department} if department else {}),
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$code"]},
                                    {"$gte": ["$date", month + "-01"]},
                                    {"$lte": ["$date", last_day]},
                                ]
                            }
                        }
                    },
                    {
                        "$project": {
                            "_id": 0,
                            "date": 1,
                            "status": 1,
                            "half_day": 1,
                            "work_hours": 1,
                            "late_minutes": 1,
                        }
                    },
                ],
                "as": "logs",
            }
        },
        {
            "$project": {
                "department": 1,
                "present_values": {
                    "$map": {
                        "input": {
                            "$filter": {
                                "input": "$logs",
                                "as": "r",
                                "cond": {
                                    "$and": [
                                        {
                                            "$in": [
                                                "$$r.status",
                                                ["PRESENT", "WFH", "ON_DUTY"],
                                            ]
                                        },
                                        {
                                            "$in": [
                                                {
                                                    "$dayOfWeek": {
                                                        "$dateFromString": {
                                                            "dateString": "$$r.date"
                                                        }
                                                    }
                                                },
                                                [2, 3, 4, 5, 6],
                                            ]
                                        },
                                    ]
                                },
                            }
                        },
                        "as": "r",
                        "in": {
                            "$cond": [
                                {"$eq": ["$$r.half_day", True]},
                                0.5,
                                1.0,
                            ]
                        },
                    }
                },
                "leave_count": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "r",
                            "cond": {"$eq": ["$$r.status", "LEAVE"]},
                        }
                    }
                },
                "on_duty_count": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "r",
                            "cond": {"$eq": ["$$r.status", "ON_DUTY"]},
                        }
                    }
                },
                "late_values": {
                    "$map": {
                        "input": "$logs",
                        "as": "r",
                        "in": {"$ifNull": ["$$r.late_minutes", 0]},
                    }
                },
                "late_count": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "r",
                            "cond": {"$gt": [{"$ifNull": ["$$r.late_minutes", 0]}, 0]},
                        }
                    }
                },
                "work_values": {
                    "$map": {
                        "input": {
                            "$filter": {
                                "input": "$logs",
                                "as": "r",
                                "cond": {
                                    "$and": [
                                        {
                                            "$in": [
                                                "$$r.status",
                                                ["PRESENT", "WFH", "ON_DUTY"],
                                            ]
                                        },
                                        {"$ne": ["$$r.work_hours", None]},
                                    ]
                                },
                            }
                        },
                        "as": "r",
                        "in": "$$r.work_hours",
                    }
                },
            }
        },
        {
            "$addFields": {
                "present_days": {"$sum": "$present_values"},
                "total_late_minutes": {"$sum": "$late_values"},
                "work_sum": {"$sum": "$work_values"},
                "work_count": {"$size": "$work_values"},
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "present_days": {"$sum": "$present_days"},
                "late_count": {"$sum": "$late_count"},
                "total_late_minutes": {"$sum": "$total_late_minutes"},
                "leave_count": {"$sum": "$leave_count"},
                "on_duty_count": {"$sum": "$on_duty_count"},
                "work_sum": {"$sum": "$work_sum"},
                "work_count": {"$sum": "$work_count"},
            }
        },
        {"$sort": {"_id": 1}},
    ]

    results = list(db.employees.aggregate(pipeline))

    items = []
    for r in results:
        work_count = r["work_count"]
        items.append({
            "department": r["_id"],
            "month": month,
            "headcount": r["headcount"],
            "present_days": r["present_days"],
            "avg_work_hours": (
                round(r["work_sum"] / work_count, 2)
                if work_count else None
            ),
            "late_count": r["late_count"],
            "total_late_minutes": r["total_late_minutes"],
            "leave_count": r["leave_count"],
            "on_duty_count": r["on_duty_count"],
        })

    return {"items": items}

@app.get("/analytics/leaderboard/late")
def late_arrival_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    limit: int = Query(default=10, ge=1, le=50),
    department: Optional[str] = None,
):
    from calendar import monthrange

    try:
        year, mon = map(int, month.split("-"))
        last_day = datetime(
            year, mon, monthrange(year, mon)[1]
        ).date().isoformat()
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid month")

    pipeline = [
        {
            "$match": {
                "date": {"$gte": month + "-01", "$lte": last_day},
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
            }
        },
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]

    if department:
        pipeline.append({
            "$match": {"employee.department": department}
        })

    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}}
                },
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {
            "$project": {
                "_id": 0,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "rank": 1,
            }
        },
        {"$sort": {"rank": 1, "emp_code": 1}},
    ])

    try:
        items = list(db.attendance_logs.aggregate(pipeline))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Leaderboard aggregation failed: {str(exc)}",
        )

    return {
        "month": month,
        "items": items,
    }

@app.get("/analytics/departments/{department}/trend")
def department_daily_trend(
    department: str,
    from_date: str = Query(..., alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    to_date: str = Query(..., alias="to", pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    from decimal import Decimal, ROUND_HALF_UP

    try:
        start_date = datetime.strptime(from_date, "%Y-%m-%d").date()
        end_date = datetime.strptime(to_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date")

    date_range = (end_date - start_date).days

    if date_range < 0:
        raise HTTPException(
            status_code=422,
            detail="'to' must be on or after 'from'"
        )

    if date_range > 91:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days"
        )

    if not db.employees.find_one({"department": department}):
        raise HTTPException(status_code=404, detail="Department not found")

    start_datetime = datetime.combine(start_date, datetime.min.time())

    pipeline = [
        {"$match": {"department": department}},
        {
            "$addFields": {
                "calendar": {
                    "$map": {
                        "input": {"$range": [0, date_range + 1]},
                        "as": "day",
                        "in": {
                            "$dateToString": {
                                "date": {
                                    "$dateAdd": {
                                        "startDate": start_datetime,
                                        "unit": "day",
                                        "amount": "$$day"
                                    }
                                },
                                "format": "%Y-%m-%d"
                            }
                        }
                    }
                }
            }
        },
        {"$unwind": "$calendar"},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {
                    "employee_code": "$emp_code",
                    "attendance_date": "$calendar"
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$employee_code"]},
                                    {"$eq": ["$date", "$$attendance_date"]}
                                ]
                            }
                        }
                    },
                    {
                        "$project": {
                            "_id": 0,
                            "status": 1,
                            "half_day": 1,
                            "late_minutes": 1
                        }
                    }
                ],
                "as": "attendance"
            }
        },
        {
            "$unwind": {
                "path": "$attendance",
                "preserveNullAndEmptyArrays": True
            }
        },
        {
            "$group": {
                "_id": "$calendar",
                "headcount": {
                    "$sum": {
                        "$cond": [
                            {"$lte": ["$joined_on", "$calendar"]},
                            1, 0
                        ]
                    }
                },
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
    {"$lte": ["$joined_on", "$calendar"]},
    {
        "$in": [
            "$attendance.status",
            ["PRESENT", "WFH", "ON_DUTY"]
        ]
    }
]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$attendance.half_day", True]},
                                    0.5, 1.0
                                ]
                            },
                            0
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$lte": ["$joined_on", "$calendar"]},
                                    {
                                        "$gt": [
                                            {"$ifNull": ["$attendance.late_minutes", 0]},
                                            0
                                        ]
                                    }
                                ]
                            },
                            1, 0
                        ]
                    }
                }
            }
        },
        
        {"$sort": {"_id": 1}},
        {
            "$addFields": {
                "is_working_day": {
                    "$in": [
                        {
                            "$dayOfWeek": {
                                "$dateFromString": {"dateString": "$_id"}
                            }
                        },
                        [2, 3, 4, 5, 6]
                    ]
                }
            }
        },
        {
            "$addFields": {
                "present_count": {
                    "$cond": ["$is_working_day", "$present_count", 0]
                },
                "late_count": {
                    "$cond": ["$is_working_day", "$late_count", 0]
                }
            }
        },
        {
            "$addFields": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]}
                            ]
                        },
                        {
                            "$round": [
                                {
                                    "$multiply": [
                                        {"$divide": ["$present_count", "$headcount"]},
                                        100
                                    ]
                                },
                                4
                            ]
                        },
                        None
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"_id": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]}
                    }
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": "$_id",
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "is_working_day": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1
            }
        }

    ]

    daily_trends = list(db.employees.aggregate(pipeline))

    for day_data in daily_trends:
        for metric in ("attendance_rate", "moving_avg_7d"):
            value = day_data.get(metric)

            if value is not None:
                day_data[metric] = float(
                    Decimal(str(value)).quantize(
                        Decimal("0.0001"),
                        rounding=ROUND_HALF_UP
                    )
                )

    return {
        "department": department,
        "from": from_date,
        "to": to_date,
        "items": daily_trends
    }


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(default=10, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = Query(default=None, alias="from"),
    to_date: Optional[str] = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    if endpoint not in {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }:
        raise HTTPException(status_code=422, detail="Unknown endpoint")

    
    if endpoint == "attendance_list":
        filters = {}

        if emp_code:
            filters["emp_code"] = emp_code

        if status:
            filters["status"] = status

        if month:
            try:
                month_start = datetime.strptime(month, "%Y-%m")
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail="Invalid month. Use YYYY-MM",
                )

            if month_start.month == 12:
                next_month = month_start.replace(
                    year=month_start.year + 1,
                    month=1,
                )
            else:
                next_month = month_start.replace(
                    month=month_start.month + 1,
                )

            filters["date"] = {
                "$gte": month_start.strftime("%Y-%m-%d"),
                "$lt": next_month.strftime("%Y-%m-%d"),
            }

        elif date_from or date_to:
            date_filter = {}

            if date_from:
                date_filter["$gte"] = date_from

            if date_to:
                date_filter["$lte"] = date_to

            filters["date"] = date_filter

        explain = db.command({
            "explain": {
                "find": "attendance_logs",
                "filter": filters,
                "sort": {"date": -1, "emp_code": 1},
                "skip": (page - 1) * page_size,
                "limit": min(limit or page_size, 50),
            },
            "verbosity": "executionStats",
        })

        collection = "attendance_logs"

    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(
                status_code=422,
                detail="emp_code and month are required",
            )

        try:
            year, mon = map(int, month.split("-"))
            from calendar import monthrange
            last_day = datetime(
                year, mon, monthrange(year, mon)[1]
            ).date().isoformat()
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail="Invalid month")

        filters = {
            "emp_code": emp_code,
            "date": {"$gte": month + "-01", "$lte": last_day},
        }

        explain = db.command({
            "explain": {
                "find": "attendance_logs",
                "filter": filters,
                "projection": {
                    "_id": 0,
                    "date": 1,
                    "status": 1,
                    "half_day": 1,
                    "late_minutes": 1,
                    "overtime_minutes": 1,
                },
            },
            "verbosity": "executionStats",
        })
        collection = "attendance_logs"

    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(
                status_code=422,
                detail="month is required",
            )

        try:
            year, mon = map(int, month.split("-"))
            from calendar import monthrange
            last_day = datetime(
                year, mon, monthrange(year, mon)[1]
            ).date().isoformat()
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail="Invalid month")

        match = {"joined_on": {"$lte": last_day}}
        if department:
            match["department"] = department

        pipeline = [
            {"$match": match},
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let": {"code": "$emp_code"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$emp_code", "$$code"]},
                                        {"$gte": ["$date", month + "-01"]},
                                        {"$lte": ["$date", last_day]},
                                    ]
                                }
                            }
                        }
                    ],
                    "as": "logs",
                }
            },
        ]

        explain = db.command({
            "explain": {
                "aggregate": "employees",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
                })
        collection = "employees"

    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(
                status_code=422,
                detail="month is required",
            )

        try:
            year, mon = map(int, month.split("-"))
            from calendar import monthrange
            last_day = datetime(
                year, mon, monthrange(year, mon)[1]
            ).date().isoformat()
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail="Invalid month")

        
        
        pipeline = [
            {
                "$match": {
                    "date": {"$gte": month + "-01", "$lte": last_day},
                    "late_minutes": {"$gt": 0},
                }
            },
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {"$sum": "$late_minutes"},
                }
            },
            {"$match": {"total_late_minutes": {"$gt": 0}}},
            {"$sort": {"total_late_minutes": -1, "_id": 1}},
            {
                "$lookup": {
                    "from": "employees",
                    "localField": "_id",
                    "foreignField": "emp_code",
                    "as": "employee",
                }
            },
            {"$unwind": "$employee"},
        ]

        if department:
            pipeline.append({
                "$match": {"employee.department": department}
            })

        pipeline.extend([
            {
                "$setWindowFields": {
                    "sortBy": {"total_late_minutes": -1},
                    "output": {"rank": {"$rank": {}}},
                }
            },
            {"$match": {"rank": {"$lte": limit}}},
            {
                "$project": {
                    "_id": 0,
                    "emp_code": "$_id",
                    "name": "$employee.name",
                    "department": "$employee.department",
                    "total_late_minutes": 1,
                    "rank": 1,
                }
            },
            {"$sort": {"rank": 1, "emp_code": 1}},
        ])

        explain = db.command({
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        })
        collection = "attendance_logs"

    
    else:
        if not department or not from_date or not to_date:
            raise HTTPException(
                status_code=422,
                detail="department, from and to are required",
            )

        try:
            start = datetime.strptime(from_date, "%Y-%m-%d").date()
            end = datetime.strptime(to_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid date")

        if end < start or (end - start).days > 91:
            raise HTTPException(status_code=422, detail="Invalid date range")

        if not db.employees.find_one({"department": department}):
            raise HTTPException(
                status_code=404,
                detail="Department not found",
            )

        start_datetime = datetime.combine(start, datetime.min.time())
        date_range = (end - start).days

        pipeline = [
            {"$match": {"department": department}},
            {
                "$addFields": {
                    "calendar": {
                        "$map": {
                            "input": {"$range": [0, date_range + 1]},
                            "as": "day",
                            "in": {
                                "$dateToString": {
                                    "date": {
                                        "$dateAdd": {
                                            "startDate": start_datetime,
                                            "unit": "day",
                                            "amount": "$$day",
                                        }
                                    },
                                    "format": "%Y-%m-%d",
                                }
                            },
                        }
                    }
                }
            },
            {"$unwind": "$calendar"},
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let": {
                        "employee_code": "$emp_code",
                        "attendance_date": "$calendar",
                    },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$emp_code", "$$employee_code"]},
                                        {"$eq": ["$date", "$$attendance_date"]},
                                    ]
                                }
                            }
                        },
                        {
                            "$project": {
                                "_id": 0,
                                "status": 1,
                                "half_day": 1,
                                "late_minutes": 1,
                            }
                        },
                    ],
                    "as": "attendance",
                }
            },
            {
                "$unwind": {
                    "path": "$attendance",
                    "preserveNullAndEmptyArrays": True,
                }
            },
            {
                "$group": {
                    "_id": "$calendar",
                    "headcount": {
                        "$sum": {
                            "$cond": [
                                {"$lte": ["$joined_on", "$calendar"]},
                                1,
                                0,
                            ]
                        }
                    },
                    "present_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {"$lte": ["$joined_on", "$calendar"]},
                                        {
                                            "$in": [
                                                "$attendance.status",
                                                ["PRESENT", "WFH", "ON_DUTY"],
                                            ]
                                        },
                                    ]
                                },
                                {
                                    "$cond": [
                                        {"$eq": ["$attendance.half_day", True]},
                                        0.5,
                                        1.0,
                                    ]
                                },
                                0,
                            ]
                        }
                    },
                    "late_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {"$lte": ["$joined_on", "$calendar"]},
                                        {
                                            "$gt": [
                                                {
                                                    "$ifNull": [
                                                        "$attendance.late_minutes",
                                                        0,
                                                    ]
                                                },
                                                0,
                                            ]
                                        },
                                    ]
                                },
                                1,
                                0,
                            ]
                        }
                    },
                }
            },
            {"$sort": {"_id": 1}},
            {
                "$addFields": {
                    "is_working_day": {
                        "$in": [
                            {
                                "$dayOfWeek": {
                                    "$dateFromString": {"dateString": "$_id"}
                                }
                            },
                            [2, 3, 4, 5, 6],
                        ]
                    }
                }
            },
            {
                "$addFields": {
                    "present_count": {
                        "$cond": ["$is_working_day", "$present_count", 0]
                    },
                    "late_count": {
                        "$cond": ["$is_working_day", "$late_count", 0]
                    },
                }
            },
            {
                "$addFields": {
                    "attendance_rate": {
                        "$cond": [
                            {
                                "$and": [
                                    "$is_working_day",
                                    {"$gt": ["$headcount", 0]},
                                ]
                            },
                            {
                                "$round": [
                                    {
                                        "$multiply": [
                                            {
                                                "$divide": [
                                                    "$present_count",
                                                    "$headcount",
                                                ]
                                            },
                                            100,
                                        ]
                                    },
                                    4,
                                ]
                            },
                            None,
                        ]
                    }
                }
            },
            {
                "$setWindowFields": {
                    "sortBy": {"_id": 1},
                    "output": {
                        "moving_avg_7d": {
                            "$avg": "$attendance_rate",
                            "window": {"documents": [-6, 0]},
                        }
                    },
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "date": "$_id",
                    "headcount": 1,
                    "present_count": 1,
                    "late_count": 1,
                    "is_working_day": 1,
                    "attendance_rate": 1,
                    "moving_avg_7d": 1,
                }
            },
        ]

        explain = db.command({
            "explain": {
                "aggregate": "employees",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        })
        collection = "employees"


    return {
        "endpoint": endpoint,
        "collection": collection,
        "explain": explain,
    }
