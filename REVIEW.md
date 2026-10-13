# REVIEW.md

## Starter Code Review

The following table summarizes the implementation fixes reviewed in `app/main.py`. The automated test suite passed 12 tests; this does not independently verify every possible edge case.

| # | Area | Issue | Implementation |
|---|---|---|---|
| 1 | `health()` | Health status must reflect database connectivity. | Added a MongoDB ping and return HTTP 503 when the database is unavailable. |
| 2 | `create_employee()` | Concurrent requests can create duplicate employees. | Use the unique employee-code index and handle duplicate-key errors with HTTP 409. |
| 3 | `create_employee()` | Naive timestamps create timezone ambiguity. | Store `created_at` as a timezone-aware UTC timestamp. |
| 4 | `list_employees()` | Pagination offsets must be consistent. | Calculate the offset from the validated page and page size. |
| 5 | `list_employees()` | Totals must respect department filters. | Apply the same employee filters to the count and listing queries. |
| 6 | `list_employees()` | Invalid pagination parameters can cause unexpected queries. | Validate page and page size through FastAPI query constraints. |
| 7 | `punch_in()` | Unknown employees must not receive attendance records. | Check employee existence and return HTTP 404 when missing. |
| 8 | `punch_in()` | Epoch timestamp zero must not be treated as a missing timestamp. | Check explicitly for `None` before converting epoch milliseconds. |
| 9 | `punch_in()` | Overnight shifts need consistent attendance dates. | Derive the attendance date through `attendance_day()` using the employee schedule. |
| 10 | `punch_in()` | Concurrent punch-ins must not create duplicate daily records. | Use the unique `(emp_code, date)` index and return HTTP 409 on duplicate-key conflicts. |
| 11 | Attendance operations | Invalid timestamps and state transitions must be rejected. | Validate employee existence, timestamp ordering, duration limits, and regularization inputs. |
| 12 | `list_attendance()` | Loading all records before pagination wastes memory. | Apply filters, sorting, skip, and limit in MongoDB. |
| 13 | `list_attendance()` | Database documents must be serialized to the API response contract. | Use the record serialization helper when returning attendance records. |
| 14 | `list_attendance()` | Pagination requires deterministic sorting. | Sort by date and employee code. |
| 15 | `compute_late_minutes()` | Grace periods and overnight schedules require explicit handling. | Implement the calculation in a dedicated helper; see the automated tests for covered boundaries. |
| 16 | `compute_work_hours()` | Rounding must follow the specified rule. | Use the dedicated half-up rounding helper. |
| 17 | `compute_overtime()` | Overtime must respect qualifying duration and shift schedules. | Implement overtime calculation in a dedicated helper; see the automated tests for covered boundaries. |
| 18 | API routes | Required attendance and analytics operations must be available. | Implement punch-out, regularization, monthly analytics, department summaries and trends, leaderboard, and explain routes. |

## Concurrency and database safeguards

- Unique indexes protect employee codes and daily attendance records against duplicates.
- Punch-out uses a conditional update so concurrent requests cannot both close the same record.
- Regularization uses a conditional update and appends an audit-history entry in the same database operation.
- Analytics endpoints and the admin explain endpoint reuse pipeline-building functions.

## Existing automated test results

The local test run completed with **12 passed** and one `PendingDeprecationWarning` from Starlette's multipart import. The warning is not a test failure.

Passing tests establish only that the covered cases pass. They do not prove every edge case or replace verification against a running MongoDB instance and the complete assignment requirements.

## Remaining verification

- Confirm database index definitions and startup behavior against the running environment.
- Verify analytics results against the assignment's sample data.
- Confirm all API response schemas against `openapi.yaml`.
