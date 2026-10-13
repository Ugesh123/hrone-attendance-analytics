"""
Employee Attendance & Analytics API

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded, but real environment variables win)
"""
import json
import os
from fastapi.middleware.cors import CORSMiddleware
from calendar import monthrange
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal, Optional

from bson import json_util
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Path, Query
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, StrictInt, model_validator
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

# load_dotenv() does not override variables that are already set,
# so real environment variables take priority over .env
load_dotenv()

client = MongoClient(
    os.getenv("MONGO_URI", "mongodb://localhost:27017"),
    tz_aware=True,
    serverSelectionTimeoutMS=5000,
)
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
PRESENCE_STATUSES = ["PRESENT", "WFH", "ON_DUTY"]

TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"

Status = Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]
PresenceStatus = Literal["PRESENT", "WFH", "ON_DUTY"]

# StrictInt rejects floats, strings and booleans. The range rejects
# values that look like seconds and values past the year 2100.
EpochMillis = Annotated[StrictInt, Field(ge=100_000_000_000, le=4_102_444_800_000)]


db.employees.create_index("emp_code", unique=True)
db.employees.create_index([("department", 1), ("joined_on", 1)])
db.employees.create_index([("joined_on", 1)])


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

# Used by punch-out to find the employee's latest punch-in quickly
db.attendance_logs.create_index([("emp_code", 1), ("punch_in", -1)])


app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def validation_error(location, field, message):
    # Same 422 body FastAPI uses for its own validation errors
    return RequestValidationError(
        [{"type": "value_error", "loc": (location, field), "msg": message, "input": None}]
    )


def now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0)


def from_millis(ms):
    # R1: drop the milliseconds, keep whole seconds
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc)


def to_millis(value):
    if value is None or isinstance(value, int):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (value - EPOCH) // timedelta(milliseconds=1)


def round_half_up(value, places=2):
    if value is None:
        return None
    step = Decimal(10) ** -places
    return float(Decimal(str(value)).quantize(step, rounding=ROUND_HALF_UP))


def parse_day(value, field, location="query"):
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise validation_error(location, field, "Invalid date, expected YYYY-MM-DD")


def month_bounds(month):
    year, mon = map(int, month.split("-"))
    first = date(year, mon, 1)
    last = date(year, mon, monthrange(year, mon)[1])
    return first, last


def count_working_days(start, end):
    count = 0
    day = start
    while day <= end:
        if day.weekday() < 5:
            count += 1
        day += timedelta(days=1)
    return count


def parse_hhmm(value):
    hours, minutes = map(int, value.split(":"))
    return time(hours, minutes)


def is_overnight(emp):
    return emp["shift_end"] <= emp["shift_start"]


def shift_start_at(emp, day):
    return datetime.combine(day, parse_hhmm(emp["shift_start"]), tzinfo=IST)


def shift_end_at(emp, day):
    end = datetime.combine(day, parse_hhmm(emp["shift_end"]), tzinfo=IST)
    if is_overnight(emp):
        end += timedelta(days=1)
    return end


def attendance_day(punch_in, emp):
    # R1: IST date of the punch-in. For an overnight shift, a punch-in
    # before shift_end belongs to the shift that started the day before.
    local = punch_in.astimezone(IST)
    day = local.date()
    if is_overnight(emp) and local.time() < parse_hhmm(emp["shift_end"]):
        day -= timedelta(days=1)
    return day


def compute_late_minutes(punch_in, emp, day):
    # R2: late only when strictly more than 10:00 after shift start,
    # then count whole minutes from shift start
    seconds = (punch_in - shift_start_at(emp, day)).total_seconds()
    if seconds <= 10 * 60:
        return 0
    return int(seconds // 60)


def compute_work_hours(punch_in, punch_out):
    # R4: half-up rounding, Python's round() would give 9.12 for 9.125
    seconds = int((punch_out - punch_in).total_seconds())
    hours = Decimal(seconds) / Decimal(3600)
    return float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime(punch_out, emp, day):
    # R3: counted from the shift end of the attendance day (next day for overnight shifts)
    minutes = int((punch_out - shift_end_at(emp, day)).total_seconds() // 60)
    return minutes if minutes >= 30 else 0


def compute_derived(emp, day, status, punch_in, punch_out):
    derived = {"late_minutes": 0, "work_hours": None, "overtime_minutes": 0, "half_day": False}
    if status not in PRESENCE_STATUSES:
        return derived

    derived["late_minutes"] = compute_late_minutes(punch_in, emp, day)
    if punch_out is not None:
        work_hours = compute_work_hours(punch_in, punch_out)
        derived["work_hours"] = work_hours
        derived["overtime_minutes"] = compute_overtime(punch_out, emp, day)
        derived["half_day"] = work_hours < 4.50
    return derived


def employee_out(doc):
    return {
        "emp_code": doc["emp_code"],
        "name": doc["name"],
        "email": doc["email"],
        "department": doc["department"],
        "shift_start": doc["shift_start"],
        "shift_end": doc["shift_end"],
        "joined_on": doc["joined_on"],
        "created_at": to_millis(doc.get("created_at")),
    }


def record_out(doc):
    history = []
    for entry in doc.get("history") or []:
        changes = {}
        for field, change in entry.get("changes", {}).items():
            if field in ("punch_in", "punch_out"):
                change = {"from": to_millis(change.get("from")), "to": to_millis(change.get("to"))}
            changes[field] = change
        history.append({
            "at": to_millis(entry.get("at")),
            "by": entry.get("by"),
            "reason": entry.get("reason"),
            "changes": changes,
        })

    # Older records can miss half_day / history / late_minutes; treat as defaults
    return {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": to_millis(doc.get("punch_in")),
        "punch_out": to_millis(doc.get("punch_out")),
        "work_hours": doc.get("work_hours"),
        "late_minutes": doc.get("late_minutes") or 0,
        "overtime_minutes": doc.get("overtime_minutes") or 0,
        "half_day": doc.get("half_day") or False,
        "history": history,
    }


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=TIME_PATTERN)
    shift_end: str = Field(default="18:30", pattern=TIME_PATTERN)
    joined_on: str = Field(pattern=DATE_PATTERN)

    @model_validator(mode="after")
    def check_dates_and_shift(self):
        date.fromisoformat(self.joined_on)  # rejects dates like 2026-02-30
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[EpochMillis] = None
    status: PresenceStatus = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[EpochMillis] = None


class RegularizeIn(BaseModel):
    status: Optional[Status] = None
    punch_in: Optional[EpochMillis] = None
    punch_out: Optional[EpochMillis] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)


# --------------------------------------------------------------------------- #
# System and employees
# --------------------------------------------------------------------------- #

@app.get("/health")
def health():
    try:
        client.admin.command("ping")
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)

    # The unique index decides; no check-then-insert race
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="emp_code already exists")

    return employee_out(doc)


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    filters = {}
    if department is not None:
        filters["department"] = department

    total = db.employees.count_documents(filters)
    cursor = (
        db.employees.find(filters)
        .sort("emp_code", ASCENDING)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    return {
        "items": [employee_out(doc) for doc in cursor],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# --------------------------------------------------------------------------- #
# Attendance
# --------------------------------------------------------------------------- #

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    punched = from_millis(body.punched_at) if body.punched_at is not None else now_utc()
    day = attendance_day(punched, emp)

    record = {
        "emp_code": body.emp_code,
        "date": day.isoformat(),
        "status": body.status,
        "punch_in": punched,
        "punch_out": None,
        **compute_derived(emp, day, body.status, punched, None),
        "history": [],
    }

    # Two identical punch-ins: the unique (emp_code, date) index lets only one insert win
    try:
        db.attendance_logs.insert_one(record)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Already punched in for this date")

    return record_out(record)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # punched_at is already validated by PunchOutIn (strict epoch milliseconds)
    if body.punched_at is not None:
        ts = from_millis(body.punched_at)
    else:
        ts = now_utc()

    # The employee's most recent record whose punch-in is at or before ts.
    # If that record is already closed, this punch-out is a duplicate (409).
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": ts},
        },
        sort=[("punch_in", -1)],
    )

    if not record:
        raise HTTPException(
            status_code=404,
            detail="No punch-in record found",
        )

    if record.get("punch_out") is not None:
        raise HTTPException(
            status_code=409,
            detail="Attendance already punched out",
        )

    punch_in_time = record["punch_in"]
    duration = (ts - punch_in_time).total_seconds()

    if duration <= 0:
        raise validation_error("body", "punched_at", "Punch-out must be after punch-in")

    if duration > 86400:
        raise validation_error("body", "punched_at", "Punch-out must be within 24 hours")

    attendance_date = date.fromisoformat(record["date"])
    work_hours = compute_work_hours(punch_in_time, ts)
    overtime = compute_overtime(ts, emp, attendance_date)

    # Only one of two simultaneous punch-outs can still match punch_out: None
    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_in": record["punch_in"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "overtime_minutes": overtime,
                "half_day": work_hours < 4.50,
            }
        },
    )

    if result.modified_count == 0:
        raise HTTPException(
            status_code=409,
            detail="Attendance record was already closed",
        )

    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    return record_out(updated)


def attendance_filters(emp_code, date_from, date_to, status):
    filters = {}
    if emp_code is not None:
        filters["emp_code"] = emp_code
    if status is not None:
        filters["status"] = status

    date_range = {}
    if date_from:
        parse_day(date_from, "date_from")
        date_range["$gte"] = date_from
    if date_to:
        parse_day(date_to, "date_to")
        date_range["$lte"] = date_to
    if date_from and date_to and date_from > date_to:
        raise validation_error("query", "date_from", "date_from must be on or before date_to")
    if date_range:
        filters["date"] = date_range

    return filters


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = Query(default=None, pattern=DATE_PATTERN),
    date_to: Optional[str] = Query(default=None, pattern=DATE_PATTERN),
    status: Optional[Status] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    filters = attendance_filters(emp_code, date_from, date_to, status)

    if filters:
        total = db.attendance_logs.count_documents(filters)
    else:
        total = db.attendance_logs.estimated_document_count()

    cursor = (
        db.attendance_logs.find(filters)
        .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    return {
        "items": [record_out(doc) for doc in cursor],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    body: RegularizeIn,
    emp_code: str,
    date: str = Path(pattern=DATE_PATTERN),
):
    day = parse_day(date, "date", "path")

    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    old = {
        "status": record["status"],
        "punch_in": record.get("punch_in"),
        "punch_out": record.get("punch_out"),
        "work_hours": record.get("work_hours"),
        "late_minutes": record.get("late_minutes") or 0,
        "overtime_minutes": record.get("overtime_minutes") or 0,
        "half_day": record.get("half_day") or False,
    }

    status = body.status or old["status"]

    if status in ("ABSENT", "LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise validation_error("body", "status", "ABSENT and LEAVE cannot have punch times")
        new_in = None
        new_out = None
    else:
        new_in = from_millis(body.punch_in) if body.punch_in is not None else old["punch_in"]
        new_out = from_millis(body.punch_out) if body.punch_out is not None else old["punch_out"]

        if new_in is None:
            raise validation_error("body", "punch_in", "A presence status needs a punch_in")
        if attendance_day(new_in, emp) != day:
            raise validation_error("body", "punch_in", "punch_in must stay on the attendance date")
        if new_out is not None:
            seconds = (new_out - new_in).total_seconds()
            if seconds <= 0 or seconds > 24 * 3600:
                raise validation_error("body", "punch_out", "punch_out must be after punch_in and within 24 hours")

    new = {"status": status, "punch_in": new_in, "punch_out": new_out}
    if all(old[field] == new[field] for field in new):
        raise validation_error("body", "status", "Request does not change the record")

    new.update(compute_derived(emp, day, status, new_in, new_out))

    changes = {}
    for field, value in new.items():
        if old[field] != value:
            changes[field] = {"from": old[field], "to": value}

    history_entry = {
        "at": now_utc(),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    # Every successful correction changes status, punch_in or punch_out.
    # If another request changed them after we read the record, nothing
    # matches here and this request gets 409 instead of losing its history entry.
    updated = db.attendance_logs.find_one_and_update(
        {
            "_id": record["_id"],
            "status": old["status"],
            "punch_in": old["punch_in"],
            "punch_out": old["punch_out"],
        },
        {"$set": new, "$push": {"history": history_entry}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=409, detail="Record was changed by another request, try again")

    return record_out(updated)


# --------------------------------------------------------------------------- #
# Analytics pipelines
# Each endpoint and /admin/explain build their pipeline from the same function.
# --------------------------------------------------------------------------- #

def present_day_value(prefix):
    # R5-R7: a presence status on Mon-Fri counts 1, or 0.5 for a half day.
    # prefix is "" for a log at the root, or "logs." after a $lookup + $unwind.
    weekday = {"$isoDayOfWeek": {"$dateFromString": {"dateString": f"${prefix}date"}}}
    return {
        "$cond": [
            {"$and": [
                {"$in": [f"${prefix}status", PRESENCE_STATUSES]},
                {"$lte": [weekday, 5]},
            ]},
            {"$cond": [{"$eq": [f"${prefix}half_day", True]}, 0.5, 1]},
            0,
        ]
    }


def half_up_expr(value, places):
    # MongoDB's $round rounds halves to even, so round half-up by hand
    factor = 10 ** places
    return {"$divide": [{"$floor": {"$add": [{"$multiply": [value, factor]}, 0.5]}}, factor]}


def employee_monthly_pipeline(emp_code, month):
    first, last = month_bounds(month)
    return [
        {"$match": {"emp_code": emp_code, "date": {"$gte": first.isoformat(), "$lte": last.isoformat()}}},
        {"$group": {
            "_id": None,
            "present_days": {"$sum": present_day_value("")},
            "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": "$late_minutes"},
            "total_overtime_minutes": {"$sum": "$overtime_minutes"},
        }},
    ]


def department_summary_pipeline(month, department):
    first, last = month_bounds(month)

    # Start from employees so people with no logs still count in headcount (R9)
    match = {"joined_on": {"$lte": last.isoformat()}}
    if department is not None:
        match["department"] = department

    return [
        {"$match": match},
        {"$lookup": {
            "from": "attendance_logs",
            "localField": "emp_code",
            "foreignField": "emp_code",
            "pipeline": [
                {"$match": {"date": {"$gte": first.isoformat(), "$lte": last.isoformat()}}},
                {"$project": {"_id": 0, "date": 1, "status": 1, "half_day": 1, "work_hours": 1, "late_minutes": 1}},
            ],
            "as": "logs",
        }},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$group": {
            "_id": "$department",
            "employees": {"$addToSet": "$emp_code"},
            "present_days": {"$sum": present_day_value("logs.")},
            # $avg skips nulls, so this is the mean over present records that have work_hours
            "avg_work_hours": {"$avg": {
                "$cond": [{"$in": ["$logs.status", PRESENCE_STATUSES]}, "$logs.work_hours", None]
            }},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$logs.late_minutes", 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": "$logs.late_minutes"},
            "leave_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]}},
            "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]}},
        }},
        {"$sort": {"_id": 1}},
    ]


def late_leaderboard_pipeline(month, limit, department):
    first, last = month_bounds(month)
    pipeline = [
        {"$match": {
            "date": {"$gte": first.isoformat(), "$lte": last.isoformat()},
            "late_minutes": {"$gt": 0},
        }},
        {"$group": {
            "_id": "$emp_code",
            "total_late_minutes": {"$sum": "$late_minutes"},
            "late_count": {"$sum": 1},
        }},
        # Logs whose employee does not exist are dropped by the $unwind
        {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "employee"}},
        {"$unwind": "$employee"},
    ]
    if department is not None:
        pipeline.append({"$match": {"employee.department": department}})

    pipeline += [
        # $rank gives standard competition ranking: 1, 2, 2, 4
        {"$setWindowFields": {
            "sortBy": {"total_late_minutes": -1},
            "output": {"rank": {"$rank": {}}},
        }},
        # limit applies to the rank, so ties at the cutoff are all kept
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {"$project": {
            "_id": 0,
            "rank": 1,
            "emp_code": "$_id",
            "name": "$employee.name",
            "department": "$employee.department",
            "total_late_minutes": 1,
            "late_count": 1,
        }},
    ]
    return pipeline


def department_trend_pipeline(department, from_day, to_day):
    start = from_day.isoformat()
    end = to_day.isoformat()
    first_day = datetime.combine(from_day, time(), tzinfo=timezone.utc)
    day_after_end = datetime.combine(to_day + timedelta(days=1), time(), tzinfo=timezone.utc)

    present_value = {
        "$cond": [
            {"$in": ["$$log.status", PRESENCE_STATUSES]},
            {"$cond": [{"$eq": ["$$log.half_day", True]}, 0.5, 1]},
            0,
        ]
    }
    late_value = {"$cond": [{"$gt": ["$$log.late_minutes", 0]}, 1, 0]}

    return [
        {"$match": {"department": department}},
        {"$lookup": {
            "from": "attendance_logs",
            "localField": "emp_code",
            "foreignField": "emp_code",
            "pipeline": [
                {"$match": {"date": {"$gte": start, "$lte": end}}},
                {"$project": {"_id": 0, "date": 1, "status": 1, "half_day": 1, "late_minutes": 1}},
            ],
            "as": "logs",
        }},
        # Each employee gives one "joined" event on the first day they count
        # (joined_on, or the start of the range) plus one event per log.
        {"$project": {"events": {"$concatArrays": [
            [{"date": {"$max": ["$joined_on", start]}, "joined": 1, "present": 0, "late": 0}],
            {"$map": {
                "input": "$logs",
                "as": "log",
                "in": {"date": "$$log.date", "joined": 0, "present": present_value, "late": late_value},
            }},
        ]}}},
        {"$unwind": "$events"},
        {"$group": {
            "_id": "$events.date",
            "joined": {"$sum": "$events.joined"},
            "present_count": {"$sum": "$events.present"},
            "late_count": {"$sum": "$events.late"},
        }},
        {"$addFields": {"day": {"$dateFromString": {"dateString": "$_id"}}}},
        # Fill in the days that have no events, so there is one row per calendar day
        {"$densify": {"field": "day", "range": {"step": 1, "unit": "day", "bounds": [first_day, day_after_end]}}},
        # Headcount on a day = everyone who joined on or before that day (R9)
        {"$setWindowFields": {
            "sortBy": {"day": 1},
            "output": {"headcount": {"$sum": "$joined", "window": {"documents": ["unbounded", "current"]}}},
        }},
        # Drop the join events of people who join after the range
        {"$match": {"day": {"$lt": day_after_end}}},
        {"$addFields": {
            "present_count": {"$ifNull": ["$present_count", 0]},
            "late_count": {"$ifNull": ["$late_count", 0]},
            "is_working_day": {"$lte": [{"$isoDayOfWeek": "$day"}, 5]},
        }},
        # Decimal keeps the half-up rounding exact
        {"$addFields": {"attendance_rate": {"$cond": [
            {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
            half_up_expr({"$divide": [{"$toDecimal": "$present_count"}, "$headcount"]}, 4),
            None,
        ]}}},
        # $avg skips null rates; the window only sees rows inside the range
        {"$setWindowFields": {
            "sortBy": {"day": 1},
            "output": {"moving_avg_7d": {"$avg": "$attendance_rate", "window": {"documents": [-6, 0]}}},
        }},
        {"$project": {
            "_id": 0,
            "date": {"$dateToString": {"date": "$day", "format": "%Y-%m-%d"}},
            "is_working_day": 1,
            "headcount": 1,
            "present_count": 1,
            "late_count": 1,
            "attendance_rate": {"$toDouble": "$attendance_rate"},
            "moving_avg_7d": {"$toDouble": half_up_expr("$moving_avg_7d", 4)},
        }},
    ]


def check_trend_range(from_date, to_date):
    from_day = parse_day(from_date, "from")
    to_day = parse_day(to_date, "to")
    if to_day < from_day:
        raise validation_error("query", "to", "'to' must be on or after 'from'")
    if (to_day - from_day).days + 1 > 92:
        raise validation_error("query", "to", "Range cannot be longer than 92 days")
    return from_day, to_day


# --------------------------------------------------------------------------- #
# Analytics endpoints
# --------------------------------------------------------------------------- #

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    totals = next(db.attendance_logs.aggregate(employee_monthly_pipeline(emp_code, month)), None) or {}

    # R7: Mon-Fri from joined_on (or the 1st of the month) to the end of the month
    first, last = month_bounds(month)
    start = max(first, date.fromisoformat(emp["joined_on"]))
    working_days = count_working_days(start, last)

    present_days = totals.get("present_days", 0)
    attendance_pct = None
    if working_days > 0:
        attendance_pct = round_half_up(present_days / working_days * 100, 2)

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": totals.get("leave_days", 0),
        "late_count": totals.get("late_count", 0),
        "total_late_minutes": totals.get("total_late_minutes", 0),
        "total_overtime_minutes": totals.get("total_overtime_minutes", 0),
        "attendance_pct": attendance_pct,
    }


@app.get("/analytics/departments/summary")
def department_summary(month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), department: Optional[str] = None):
    items = []
    for row in db.employees.aggregate(department_summary_pipeline(month, department)):
        items.append({
            "department": row["_id"],
            "headcount": len(row["employees"]),
            "present_days": row["present_days"],
            "avg_work_hours": round_half_up(row["avg_work_hours"], 2),
            "late_count": row["late_count"],
            "total_late_minutes": row["total_late_minutes"],
            "leave_count": row["leave_count"],
            "on_duty_count": row["on_duty_count"],
        })
    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(default=10, ge=1, le=50),
    department: Optional[str] = None,
):
    items = list(db.attendance_logs.aggregate(late_leaderboard_pipeline(month, limit, department)))
    return {"month": month, "items": items}


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(alias="from", pattern=DATE_PATTERN),
    to_date: str = Query(alias="to", pattern=DATE_PATTERN),
):
    from_day, to_day = check_trend_range(from_date, to_date)

    if not db.employees.find_one({"department": department}):
        raise HTTPException(status_code=404, detail="Department not found")

    items = list(db.employees.aggregate(department_trend_pipeline(department, from_day, to_day)))
    return {"department": department, "items": items}


# --------------------------------------------------------------------------- #
# Admin
# --------------------------------------------------------------------------- #

def require(name, value):
    if value is None:
        raise validation_error("query", name, "Field required")
    return value


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: Literal["attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"],
    emp_code: Optional[str] = None,
    month: Optional[str] = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
    limit: int = Query(default=10, ge=1, le=50),
    date_from: Optional[str] = Query(default=None, pattern=DATE_PATTERN),
    date_to: Optional[str] = Query(default=None, pattern=DATE_PATTERN),
    status: Optional[Status] = None,
    from_date: Optional[str] = Query(default=None, alias="from", pattern=DATE_PATTERN),
    to_date: Optional[str] = Query(default=None, alias="to", pattern=DATE_PATTERN),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    if endpoint == "attendance_list":
        collection = "attendance_logs"
        command = {
            "find": collection,
            "filter": attendance_filters(emp_code, date_from, date_to, status),
            "sort": {"date": -1, "emp_code": 1},
            "skip": (page - 1) * page_size,
            "limit": page_size,
        }
    elif endpoint == "employee_monthly":
        collection = "attendance_logs"
        pipeline = employee_monthly_pipeline(require("emp_code", emp_code), require("month", month))
        command = {"aggregate": collection, "pipeline": pipeline, "cursor": {}}
    elif endpoint == "department_summary":
        collection = "employees"
        pipeline = department_summary_pipeline(require("month", month), department)
        command = {"aggregate": collection, "pipeline": pipeline, "cursor": {}}
    elif endpoint == "late_leaderboard":
        collection = "attendance_logs"
        pipeline = late_leaderboard_pipeline(require("month", month), limit, department)
        command = {"aggregate": collection, "pipeline": pipeline, "cursor": {}}
    else:
        collection = "employees"
        require("department", department)
        from_day, to_day = check_trend_range(require("from", from_date), require("to", to_date))
        pipeline = department_trend_pipeline(department, from_day, to_day)
        command = {"aggregate": collection, "pipeline": pipeline, "cursor": {}}

    explain = db.command("explain", command, verbosity="executionStats")

    # The raw output has BSON types (Timestamp, ObjectId, Int64) that FastAPI
    # cannot serialize, so convert it to plain JSON first
    return {
        "endpoint": endpoint,
        "collection": collection,
        "explain": json.loads(json_util.dumps(explain)),
    }
