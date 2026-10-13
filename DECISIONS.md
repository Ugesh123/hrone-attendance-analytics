# Design Decisions

### 1. Indexes
I use a unique index on `emp_code` to prevent duplicate employees and a unique compound index on `(emp_code, date)` to prevent duplicate daily attendance records. The employee department and joining-date index helps department-related queries. Attendance indexes support date-based listing, late-minute queries, and status-based lookups. I would avoid creating an index for every individual field because extra indexes consume storage and slow down writes.

### 2. Punch-in race
When two identical punch-in requests arrive together, both may initially find that no attendance record exists. Both attempt to insert, but the unique `(emp_code, date)` index allows only one insert. The successful request returns `201`; the duplicate-key error is handled and the other request returns `409`.

### 3. Leaderboard ties
The leaderboard uses a ranking stage based on late minutes. I filter on `rank <= limit`, so ties at the cutoff are all returned. The result may contain more than the requested limit, avoiding the arbitrary exclusion of employees with equal scores.

### 4. Department headcount
The department summary starts from employees and looks up their attendance records. Therefore, employees with no attendance logs can still contribute to department headcount.

### 5. Scaling
For 100 times more data, I would validate query plans against a representative dataset, tune compound indexes for actual query patterns, and review aggregation stages to reduce unnecessary documents and fields early.

