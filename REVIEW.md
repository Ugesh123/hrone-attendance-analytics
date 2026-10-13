# REVIEW.md

## Starter Code Review

The following table records the starter-code risks and the corresponding
implementation areas to verify. A passing test suite does not, by itself,
prove every edge case listed here.

| # | Where | Issue identified in starter code | Fix / verification required |
|---|---|---|---|
| 1 | `health()` | The health endpoint may report success without checking MongoDB connectivity. | Check database connectivity before reporting healthy; verify behavior when MongoDB is unavailable. |
| 2 | `create_employee()` | Checking for an existing employee before inserting is vulnerable to concurrent requests. | Enforce uniqueness with a MongoDB unique index and handle duplicate-key errors. |
| 3 | `create_employee()` | Naive local timestamps can cause timezone ambiguity. | Use timezone-aware UTC timestamps for audit fields. |
| 4 | `list_employees()` | Incorrect pagination offsets can skip or repeat records. | Calculate the offset from the validated page and page size. |
| 5 | `list_employees()` | The reported total may not reflect the active department filter. | Apply the same filters to the count query and the paginated query. |
| 6 | `list_employees()` | Invalid page numbers or page sizes can produce unexpected queries. | Validate pagination inputs and reject invalid values. |
| 7 | `punch_in()` | Unknown employee codes must not create attendance records. | Validate employee existence and return an appropriate client error for unknown employees. |
| 8 | `punch_in()` | Timestamp handling must preserve epoch value `0` and use the required timezone rules. | Distinguish a missing timestamp from zero and validate timestamp conversion. |
| 9 | `punch_in()` | Overnight shifts can be assigned to the wrong attendance date if dates are derived incorrectly. | Apply the assignment's attendance-date and timezone rules consistently. |
| 10 | `punch_in()` | A check-then-insert flow alone cannot prevent duplicate daily attendance records. | Use the unique `(emp_code, date)` index and handle duplicate-key conflicts. |
| 11 | `punch_in()` | Invalid timestamps or attendance states can create inconsistent records. | Validate request values and reject invalid state transitions. |
| 12 | `list_attendance()` | Loading every matching document before pagination wastes memory. | Apply filtering, deterministic sorting, skip, and limit in MongoDB. |
| 13 | `list_attendance()` | Internal database fields should not leak into API responses unless the contract permits them. | Return only the response fields defined by the API contract. |
| 14 | `list_attendance()` | Sorting without a stable secondary key can make pagination inconsistent. | Add a deterministic tie-breaker to the sort. |
| 15 | `compute_late_minutes()` | Grace-period boundaries and overnight schedules require explicit handling. | Verify the calculation against the assignment's schedule and grace-period rules. |
| 16 | `compute_work_hours()` | Python's default rounding may differ from the contract's required rounding mode. | Use the specified rounding rule and test boundary values. |
| 17 | `compute_overtime()` | Overtime calculations must respect overnight schedules and the minimum qualifying duration. | Verify the 29-minute and 30-minute boundaries and overnight cases. |
| 18 | API routes | Punch-out, regularization, analytics, and explain functionality are required by the contract. | Compare registered routes and their response schemas against `openapi.yaml`. |

## Concurrency and database safeguards

- A unique employee-code index prevents duplicate employee records.
- A unique `(emp_code, date)` attendance index protects against duplicate daily attendance inserts.
- Punch-out should use a conditional update so competing requests cannot both close the same attendance record.
- Regularization should use a conditional update and append an audit-history entry as part of the same database operation.
- Index creation should be idempotent so application startup can safely create the required indexes.

## Existing automated test results

The current local test run completed with **12 passed** and one
`PendingDeprecationWarning` originating from Starlette's multipart import.
The warning is not a test failure.

These tests establish that the covered test cases pass. They do not establish
that every edge case in the table above has been independently tested.

## Not a defect by itself

- Using `emp_code` as the employee identifier is consistent with the data model; MongoDB `_id` can remain an internal field.
- Storing calendar dates as `YYYY-MM-DD` strings is valid when used consistently with the assignment's date rules.
- Returning `null` for `work_hours` before punch-out is appropriate when work duration cannot yet be calculated.
- Maintaining attendance history for manual corrections supports auditability.
