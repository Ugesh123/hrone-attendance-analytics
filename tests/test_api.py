from app.main import db
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200


def test_list_employees():
    response = client.get("/employees")
    assert response.status_code == 200


def test_list_attendance():
    response = client.get("/attendance")
    assert response.status_code == 200


def test_employee_monthly_requires_employee():
    response = client.get(
        "/analytics/employees/TEST_NONEXISTENT/monthly",
        params={"month": "2026-10"},
    )
    assert response.status_code == 404


def test_department_summary():
    response = client.get(
        "/analytics/departments/summary",
        params={"month": "2026-10"},
    )
    assert response.status_code == 200


def test_late_leaderboard():
    response = client.get(
        "/analytics/leaderboard/late",
        params={"month": "2026-10"},
    )
    assert response.status_code == 200


def test_department_trend():
    response = client.get(
        "/analytics/departments/Engineering/trend",
        params={
            "from": "2026-10-01",
            "to": "2026-10-07",
        },
    )
    assert response.status_code == 200





def test_explain_attendance_list():
    response = client.get(
        "/admin/explain/attendance_list",
        params={"month": "2026-10", "limit": 10},
    )
    assert response.status_code == 200
    assert "explain" in response.json()


def test_explain_employee_monthly():
    response = client.get(
        "/admin/explain/employee_monthly",
        params={"emp_code": "EMP0001", "month": "2026-10"},
    )
    assert response.status_code == 200
    assert "explain" in response.json()

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import time

from app.main import db


def test_concurrent_duplicate_punch_in():
    emp_code = "TEST_CONCURRENCY_001"
    attendance_date = datetime.now().strftime("%Y-%m-%d")

    # Avoid conflicting with a record already present today.
    if db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": attendance_date,
    }):
        return

    employee = {
        "emp_code": emp_code,
        "name": "Concurrency Test",
        "email": "concurrency-test@example.com",
        "department": "Test",
        "shift_start": "00:00",
        "shift_end": "23:59",
        "joined_on": attendance_date,
    }

    db.employees.update_one(
        {"emp_code": emp_code},
        {"$setOnInsert": employee},
        upsert=True,
    )

    # Use the same timestamp for every simultaneous request.
    punched_at = int(time.time() * 1000)
    payload = {
        "emp_code": emp_code,
        "punched_at": punched_at,
        "status": "PRESENT",
    }

    def send_request():
        return client.post("/attendance/punch-in", json=payload)

    with ThreadPoolExecutor(max_workers=10) as executor:
        responses = list(executor.map(
            lambda _: send_request(), range(10)
        ))

    statuses = [response.status_code for response in responses]

    assert statuses.count(201) == 1, statuses
    assert statuses.count(409) == 9, statuses

    count = db.attendance_logs.count_documents({
        "emp_code": emp_code,
        "date": datetime.fromtimestamp(
            punched_at / 1000, timezone.utc
        ).astimezone(
            timezone(__import__("datetime").timedelta(hours=5, minutes=30))
        ).date().isoformat(),
    })
    assert count == 1


def test_concurrent_punch_out():
    from concurrent.futures import ThreadPoolExecutor
    import time
    from datetime import datetime, timezone, timedelta

    emp_code = "TEST_PUNCHOUT_001"
    attendance_date = datetime.now().strftime("%Y-%m-%d")

    # Ensure a fresh test employee exists.
    db.employees.update_one(
        {"emp_code": emp_code},
        {"$setOnInsert": {
            "emp_code": emp_code,
            "name": "Punchout Test",
            "email": "punchout-test@example.com",
            "department": "Test",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": attendance_date,
        }},
        upsert=True,
    )

    # Use a past timestamp so punch-in is already open for punch-out.
    punched_in = int((time.time() - 3600) * 1000)
    punch_in_response = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": emp_code,
            "punched_at": punched_in,
            "status": "PRESENT",
        },
    )

    if punch_in_response.status_code == 409:
        # Existing test record; don't alter it.
        return

    assert punch_in_response.status_code == 201

    punched_out = int(time.time() * 1000)
    payload = {"emp_code": emp_code, "punched_at": punched_out}

    def send_request(_):
        return client.post("/attendance/punch-out", json=payload)

    with ThreadPoolExecutor(max_workers=10) as executor:
        responses = list(executor.map(send_request, range(10)))

    statuses = [response.status_code for response in responses]

    assert statuses.count(200) == 1, statuses
    assert statuses.count(409) == 9, statuses

def test_concurrent_regularization_history():
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timezone, timedelta
    import time

    emp_code = "TEST_REGULARIZE_001"
    today = datetime.now(
        timezone(timedelta(hours=5, minutes=30))
    ).date().isoformat()

    db.employees.update_one(
        {"emp_code": emp_code},
        {"$setOnInsert": {
            "emp_code": emp_code,
            "name": "Regularization Test",
            "email": "regularization-test@example.com",
            "department": "Test",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": today,
        }},
        upsert=True,
    )

    # Avoid reusing an existing attendance record.
    if db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": today,
    }):
        return

    response = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": emp_code,
            "punched_at": int(time.time() * 1000),
            "status": "PRESENT",
        },
    )
    assert response.status_code == 201, response.text

    url = f"/attendance/{emp_code}/{today}"

    def regularize(i):
        return client.patch(
            url,
            json={
                "reason": f"Concurrent correction {i}",
                "regularized_by": f"tester{i}",
                "status": "WFH",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(regularize, [1, 2]))

    statuses = [r.status_code for r in responses]
    assert statuses.count(200) == 1, statuses
    assert statuses[0] in (200, 409, 422)
    assert statuses[1] in (200, 409, 422)

    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": today,
    })
    assert record is not None
    assert len(record.get("history", [])) == 1
