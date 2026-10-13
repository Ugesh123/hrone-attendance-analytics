import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity, Bell,
  CalendarDays, CheckCircle2, ChevronDown, Clock3, LayoutDashboard,
  LogIn, LogOut, RefreshCw, Search, Settings, ShieldCheck,
  Users, X, Zap, CircleAlert, Building2, TrendingUp,
} from "lucide-react";
import {
  Area, AreaChart, CartesianGrid, ResponsiveContainer,
  Tooltip, XAxis, YAxis, BarChart, Bar,
} from "recharts";

const API = "http://localhost:8000";

type Employee = {
  emp_code: string;
  name: string;
  department: string;
  email?: string;
  shift_start?: string;
  shift_end?: string;
  joined_on?: string;
};

type Attendance = {
  emp_code: string;
  date: string;
  status?: string;
  punch_in?: number | null;
  punch_out?: number | null;
  late_minutes?: number;
  work_hours?: number | null;
};

type Department = {
  department: string;
  headcount: number;
  present_days: number;
  avg_work_hours: number | null;
  late_count: number;
  total_late_minutes: number;
};

type Leader = {
  rank: number;
  emp_code: string;
  name: string;
  department: string;
  total_late_minutes: number;
  late_count: number;
};

const today = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

const monthNow = () => today().slice(0, 7);

function clock(value?: number | null) {
  if (!value) return "—";
  return new Date(value).toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function initials(name = "") {
  return name.split(" ").filter(Boolean).slice(0, 2)
    .map((s) => s[0]).join("").toUpperCase() || "NA";
}

function App() {
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [attendance, setAttendance] = useState<Attendance[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [leaders, setLeaders] = useState<Leader[]>([]);
  const [search, setSearch] = useState("");
  const [activeTab, setActiveTab] = useState("Overview");
  const [month, setMonth] = useState(monthNow());
  const [apiOnline, setApiOnline] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busyCode, setBusyCode] = useState("");
  const [notice, setNotice] = useState("");
  const [showEmployeeForm, setShowEmployeeForm] = useState(false);
  const [form, setForm] = useState({
    emp_code: "", name: "", department: "", email: "",
    shift_start: "09:00", shift_end: "18:00", joined_on: today(),
  });

  const request = useCallback(async (path: string, options?: RequestInit) => {
    const response = await fetch(`${API}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(options?.headers || {}),
      },
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(body.detail || body.message || `Request failed (${response.status})`);
    }
    return body;
  }, []);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const health = await request("/health");
      setApiOnline(health.status === "ok");

      const [empResult, attendanceResult, deptResult, leaderResult] =
        await Promise.allSettled([
          request("/employees?page=1&page_size=100"),
          request(`/attendance?date_from=${today()}&date_to=${today()}&page=1&page_size=100`),
          request(`/analytics/departments/summary?month=${month}`),
          request(`/analytics/leaderboard/late?month=${month}&limit=5`),
        ]);

      if (empResult.status === "fulfilled") {
        setEmployees(empResult.value.items || []);
      }
      if (attendanceResult.status === "fulfilled") {
        setAttendance(attendanceResult.value.items || []);
      }
      if (deptResult.status === "fulfilled") {
        setDepartments(deptResult.value.items || []);
      }
      if (leaderResult.status === "fulfilled") {
        setLeaders(leaderResult.value.items || []);
      }
    } catch {
      setApiOnline(false);
      setNotice("Cannot connect to the API. Start the backend on port 8000.");
    } finally {
      setLoading(false);
    }
  }, [month, request]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const presentCount = attendance.filter((a) =>
    ["PRESENT", "WFH", "ON_DUTY"].includes(a.status || ""),
  ).length;

  const lateCount = attendance.filter((a) => (a.late_minutes || 0) > 0).length;
  const completedCount = attendance.filter((a) => a.punch_out).length;
  const attendanceRate = employees.length
    ? Math.round((presentCount / employees.length) * 100)
    : 0;

  const filteredEmployees = useMemo(() => {
    const q = search.toLowerCase();
    return employees.filter((e) =>
      [e.name, e.emp_code, e.department, e.email]
        .some((v) => (v || "").toLowerCase().includes(q)),
    );
  }, [employees, search]);

  const deptChart = departments.map((d) => ({
    name: d.department,
    Present: d.present_days,
    Late: d.late_count,
  }));

  async function punch(empCode: string, action: "in" | "out") {
    if (!empCode.trim()) {
      setNotice("Enter an employee code first.");
      return;
    }
    setBusyCode(empCode);
    setNotice("");
    try {
      await request(`/attendance/punch-${action}`, {
        method: "POST",
        body: JSON.stringify({ emp_code: empCode.trim() }),
      });
      setNotice(`Punch-${action} successful for ${empCode}.`);
      await loadData();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Attendance action failed.");
    } finally {
      setBusyCode("");
    }
  }

  async function addEmployee(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      await request("/employees", {
        method: "POST",
        body: JSON.stringify(form),
      });
      setShowEmployeeForm(false);
      setForm({
        emp_code: "", name: "", department: "", email: "",
        shift_start: "09:00", shift_end: "18:00", joined_on: today(),
      });
      setNotice("Employee created successfully.");
      await loadData();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not create employee.");
    }
  }

  return (
    <div className="app-shell">
      <style>{styles}</style>

      <aside className="sidebar">
        <div className="brand">
          <div className="brand-icon"><Zap size={23} fill="currentColor" /></div>
          <div><strong>HROne</strong><span>PEOPLE PLATFORM</span></div>
        </div>

        <div className="workspace-label">WORKSPACE</div>
        <div className="workspace">
          <div className="workspace-avatar">H</div>
          <div className="workspace-text"><b>HROne Company</b><span>HR Administration</span></div>
          <ChevronDown size={15} />
        </div>

        <div className="nav-label">MENU</div>
        {[
          { name: "Overview", icon: LayoutDashboard },
          { name: "Employees", icon: Users },
          { name: "Attendance", icon: CalendarDays },
          { name: "Analytics", icon: TrendingUp },
        ].map(({ name, icon: Icon }) => (
          <button key={name} className={`nav-item ${activeTab === name ? "active" : ""}`}
            onClick={() => setActiveTab(name)}>
            <Icon size={18} /> <span>{name}</span>
            {name === "Attendance" && <span className="nav-dot" />}
          </button>
        ))}

        <div className="nav-label second-label">MANAGEMENT</div>
        <button className="nav-item" onClick={() => setNotice("Admin tools are available through the backend API.")}>
          <ShieldCheck size={18} /><span>Administration</span>
        </button>
        <button className="nav-item" onClick={() => setNotice("Settings page is not configured yet.")}>
          <Settings size={18} /><span>Settings</span>
        </button>

        <div className="sidebar-bottom">
          <div className="help-card">
            <div className="help-icon"><Activity size={18} /></div>
            <b>System status</b>
            <p>Attendance API connection</p>
            <div className="status-line">
              <span className={`status-dot ${apiOnline ? "online" : ""}`} />
              {apiOnline ? "Connected" : "Disconnected"}
            </div>
          </div>
          <div className="profile">
            <div className="profile-avatar">AD</div>
            <div className="profile-info"><b>HR Admin</b><span>Administrator</span></div>
            <ChevronDown size={16} />
          </div>
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="breadcrumbs"><span>Workspace</span><span>/</span><b>{activeTab}</b></div>
          <div className="top-actions">
            <div className="today-label"><CalendarDays size={16} /> {new Date().toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" })}</div>
            <button className="icon-button" aria-label="Notifications" onClick={() => setNotice("You're all caught up.")}><Bell size={19} /><i /></button>
            <div className="top-avatar">AD</div>
          </div>
        </header>

        <div className="page">
          <div className="page-heading">
            <div>
              <div className="eyebrow">PEOPLE & OPERATIONS</div>
              <h1>{activeTab === "Overview" ? "Good morning, Admin" : activeTab}</h1>
              <p className="subtitle">Here's what's happening with your team today.</p>
            </div>
            <div className="heading-actions">
              <button className="button secondary" onClick={() => void loadData()}><RefreshCw size={16} /> Refresh</button>
              <button className="button primary" onClick={() => setShowEmployeeForm(true)}><Users size={16} /> Add employee</button>
            </div>
          </div>

          {!apiOnline && (
            <div className="alert-banner"><CircleAlert size={18} />
              <span>Backend not connected. Start FastAPI at <b>localhost:8000</b> to load real data.</span>
              <button onClick={() => void loadData()}><RefreshCw size={15} /> Retry</button>
            </div>
          )}

          {notice && (
            <div className="notice-banner">
              <span>{notice}</span>
              <button aria-label="Dismiss" onClick={() => setNotice("")}><X size={17} /></button>
            </div>
          )}

          {activeTab === "Overview" && (
            <>
              <div className="section-heading">
                <div><h2>Attendance overview</h2><p>Daily workforce snapshot</p></div>
                <div className="month-picker"><CalendarDays size={16} /><input aria-label="Analytics month" type="month" value={month} onChange={(e) => setMonth(e.target.value)} /></div>
              </div>

              <div className="stats-grid">
                <StatCard title="Total employees" value={employees.length} caption="Registered workforce" icon={<Users size={20} />} tone="purple" />
                <StatCard title="Present today" value={presentCount} caption={`${attendanceRate}% of employees`} icon={<CheckCircle2 size={20} />} tone="green" />
                <StatCard title="Late arrivals" value={lateCount} caption="Today's late check-ins" icon={<Clock3 size={20} />} tone="orange" />
                <StatCard title="Completed shifts" value={completedCount} caption="Punched in and out" icon={<LogOut size={20} />} tone="blue" />
              </div>

              <div className="content-grid">
                <section className="panel trend-panel">
                  <div className="panel-heading"><div><h3>Department overview</h3><p>Monthly attendance activity</p></div><span className="chart-badge"><TrendingUp size={15} /> Analytics</span></div>
                  {deptChart.length ? (
                    <div className="chart-wrap">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={deptChart} margin={{ top: 10, right: 5, left: -20, bottom: 5 }}>
                          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#edf0f5" />
                          <XAxis dataKey="name" tick={{ fontSize: 11, fill: "#81899a" }} axisLine={false} tickLine={false} />
                          <YAxis tick={{ fontSize: 11, fill: "#81899a" }} axisLine={false} tickLine={false} />
                          <Tooltip cursor={{ fill: "#f5f6fb" }} />
                          <Bar dataKey="Present" fill="#6658e8" radius={[5, 5, 0, 0]} maxBarSize={34} />
                          <Bar dataKey="Late" fill="#f4a261" radius={[5, 5, 0, 0]} maxBarSize={34} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  ) : (
                    <EmptyState loading={loading} message="Department analytics will appear here when API data is available." />
                  )}
                  <div className="chart-legend"><span><i className="legend-purple" /> Present days</span><span><i className="legend-orange" /> Late arrivals</span></div>
                </section>

                <section className="panel quick-panel">
                  <div className="panel-heading"><div><h3>Quick actions</h3><p>Attendance shortcuts</p></div><div className="quick-icon"><Zap size={18} /></div></div>
                  <button className="quick-action" onClick={() => {
                    const code = window.prompt("Enter employee code to punch in:");
                    if (code) void punch(code, "in");
                  }}><div className="quick-action-icon green-bg"><LogIn size={19} /></div><div><b>Punch in</b><span>Record employee arrival</span></div><span className="action-arrow">↗</span></button>
                  <button className="quick-action" onClick={() => {
                    const code = window.prompt("Enter employee code to punch out:");
                    if (code) void punch(code, "out");
                  }}><div className="quick-action-icon blue-bg"><LogOut size={19} /></div><div><b>Punch out</b><span>Record employee departure</span></div><span className="action-arrow">↗</span></button>
                  <button className="quick-action" onClick={() => setActiveTab("Employees")}><div className="quick-action-icon purple-bg"><Users size={19} /></div><div><b>View employees</b><span>Manage your workforce</span></div><span className="action-arrow">↗</span></button>
                  <div className="quick-footer"><span className={`status-dot ${apiOnline ? "online" : ""}`} /> {apiOnline ? "Live data connected" : "Waiting for backend connection"}</div>
                </section>
              </div>

              <section className="panel table-panel">
                <div className="panel-heading table-heading">
                  <div><h3>Employee directory</h3><p>Team members and today's attendance</p></div>
                  <button className="text-button" onClick={() => setActiveTab("Employees")}>View all employees ↗</button>
                </div>
                <EmployeeTable employees={employees.slice(0, 5)} attendance={attendance} loading={loading} />
              </section>

              <div className="bottom-grid">
                <section className="panel department-panel">
                  <div className="panel-heading"><div><h3>Department summary</h3><p>Monthly workforce breakdown</p></div><Building2 size={19} className="muted-icon" /></div>
                  {departments.length ? departments.slice(0, 4).map((d) => (
                    <div className="department-row" key={d.department}>
                      <div className="department-avatar">{initials(d.department)}</div>
                      <div className="department-info"><b>{d.department}</b><span>{d.headcount} employees</span></div>
                      <div className="department-metric"><b>{d.present_days}</b><span>present days</span></div>
                    </div>
                  )) : <EmptyState loading={loading} message="No department data yet." />}
                </section>

                <section className="panel leaderboard-panel">
                  <div className="panel-heading"><div><h3>Late arrivals</h3><p>Monthly leaderboard</p></div><Clock3 size={19} className="muted-icon" /></div>
                  {leaders.length ? leaders.slice(0, 4).map((l) => (
                    <div className="leader-row" key={l.emp_code}>
                      <div className="rank-number">{l.rank}</div>
                      <div className="leader-avatar">{initials(l.name)}</div>
                      <div className="leader-info"><b>{l.name}</b><span>{l.department}</span></div>
                      <div className="late-minutes">{l.total_late_minutes} min</div>
                    </div>
                  )) : <EmptyState loading={loading} message="No late arrivals recorded for this month." />}
                </section>
              </div>
            </>
          )}

          {activeTab === "Employees" && (
            <section className="panel table-panel">
              <div className="panel-heading table-heading">
                <div><h3>All employees</h3><p>{employees.length} registered team members</p></div>
                <button className="button primary" onClick={() => setShowEmployeeForm(true)}><Users size={16} /> Add employee</button>
              </div>
              <div className="search-wrap"><Search size={17} /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search by name, code or department..." /></div>
              <EmployeeTable employees={filteredEmployees} attendance={attendance} loading={loading} showActions />
            </section>
          )}

          {activeTab === "Attendance" && (
            <section className="panel table-panel">
              <div className="panel-heading table-heading"><div><h3>Today's attendance</h3><p>Daily punch-in and punch-out records</p></div></div>
              <EmployeeTable employees={employees} attendance={attendance} loading={loading} showActions />
              <div className="attendance-actions">
                <button className="button primary" onClick={() => { const c = window.prompt("Employee code:"); if (c) void punch(c, "in"); }}><LogIn size={16} /> Punch in</button>
                <button className="button secondary" onClick={() => { const c = window.prompt("Employee code:"); if (c) void punch(c, "out"); }}><LogOut size={16} /> Punch out</button>
              </div>
            </section>
          )}

          {activeTab === "Analytics" && (
            <>
              <div className="section-heading"><div><h2>Workforce analytics</h2><p>Department performance for {month}</p></div><div className="month-picker"><CalendarDays size={16} /><input aria-label="Analytics month" type="month" value={month} onChange={(e) => setMonth(e.target.value)} /></div></div>
              <div className="stats-grid">
                <StatCard title="Departments" value={departments.length} caption="With monthly records" icon={<Building2 size={20} />} tone="purple" />
                <StatCard title="Monthly present days" value={departments.reduce((n, d) => n + d.present_days, 0)} caption="Across all departments" icon={<CheckCircle2 size={20} />} tone="green" />
                <StatCard title="Late check-ins" value={departments.reduce((n, d) => n + d.late_count, 0)} caption="This month" icon={<Clock3 size={20} />} tone="orange" />
                <StatCard title="Late minutes" value={departments.reduce((n, d) => n + d.total_late_minutes, 0)} caption="Total recorded minutes" icon={<Activity size={20} />} tone="blue" />
              </div>
              <section className="panel trend-panel analytics-chart">
                <div className="panel-heading"><div><h3>Department comparison</h3><p>Present days versus late arrivals</p></div></div>
                {deptChart.length ? <div className="chart-wrap"><ResponsiveContainer width="100%" height="100%"><AreaChart data={deptChart}><CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#edf0f5" /><XAxis dataKey="name" tick={{ fontSize: 11 }} axisLine={false} tickLine={false} /><YAxis tick={{ fontSize: 11 }} axisLine={false} tickLine={false} /><Tooltip /><Area type="monotone" dataKey="Present" stroke="#6658e8" fill="#6658e8" fillOpacity={0.12} /><Area type="monotone" dataKey="Late" stroke="#f4a261" fill="#f4a261" fillOpacity={0.12} /></AreaChart></ResponsiveContainer></div> : <EmptyState loading={loading} message="Analytics will appear when the backend returns data." />}
              </section>
              <section className="panel table-panel"><div className="panel-heading"><div><h3>Department metrics</h3><p>Detailed monthly breakdown</p></div></div><div className="table-scroll"><table><thead><tr><th>Department</th><th>Headcount</th><th>Present days</th><th>Avg hours</th><th>Late records</th><th>Late minutes</th></tr></thead><tbody>{departments.map((d) => <tr key={d.department}><td><b>{d.department}</b></td><td>{d.headcount}</td><td>{d.present_days}</td><td>{d.avg_work_hours ?? "—"}</td><td>{d.late_count}</td><td>{d.total_late_minutes}</td></tr>)}</tbody></table></div></section>
            </>
          )}

          <footer className="footer"><span>© {new Date().getFullYear()} HROne · Employee Operations</span><span><span className={`status-dot ${apiOnline ? "online" : ""}`} /> {apiOnline ? "All systems operational" : "API offline"}</span></footer>
        </div>
      </main>

      {showEmployeeForm && (
        <div className="modal-backdrop" onClick={() => setShowEmployeeForm(false)}>
          <form className="employee-modal" onSubmit={addEmployee} onClick={(e) => e.stopPropagation()}>
            <div className="modal-heading"><div><h2>Add employee</h2><p>Register a new team member</p></div><button type="button" className="icon-button" onClick={() => setShowEmployeeForm(false)}><X size={18} /></button></div>
            <label>Employee code<input required value={form.emp_code} onChange={(e) => setForm({ ...form, emp_code: e.target.value })} placeholder="EMP001" /></label>
            <label>Full name<input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Employee name" /></label>
            <label>Department<input required value={form.department} onChange={(e) => setForm({ ...form, department: e.target.value })} placeholder="Engineering" /></label>
            <label>Email<input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="name@company.com" /></label>
            <div className="form-two"><label>Shift start<input type="time" required value={form.shift_start} onChange={(e) => setForm({ ...form, shift_start: e.target.value })} /></label><label>Shift end<input type="time" required value={form.shift_end} onChange={(e) => setForm({ ...form, shift_end: e.target.value })} /></label></div>
            <label>Joining date<input type="date" required value={form.joined_on} onChange={(e) => setForm({ ...form, joined_on: e.target.value })} /></label>
            <div className="modal-actions"><button type="button" className="button secondary" onClick={() => setShowEmployeeForm(false)}>Cancel</button><button type="submit" className="button primary">Create employee</button></div>
          </form>
        </div>
      )}
      {busyCode && <div className="working-pill"><RefreshCw size={15} className="spin" /> Processing {busyCode}...</div>}
    </div>
  );
}

function StatCard({ title, value, caption, icon, tone }: {
  title: string; value: number; caption: string; icon: React.ReactNode; tone: string;
}) {
  return <div className="stat-card"><div className="stat-top"><span>{title}</span><div className={`stat-icon ${tone}`}>{icon}</div></div><div className="stat-value">{value.toLocaleString("en-IN")}</div><div className="stat-caption"><span className="caption-dot" />{caption}</div></div>;
}

function EmptyState({ loading, message }: { loading: boolean; message: string }) {
  return <div className="empty-state"><div className="empty-icon">{loading ? <RefreshCw size={21} className="spin" /> : <Activity size={21} />}</div><p>{loading ? "Loading data..." : message}</p></div>;
}

function EmployeeTable({ employees, attendance, loading, showActions = false }: {
  employees: Employee[]; attendance: Attendance[]; loading: boolean; showActions?: boolean;
}) {
  const attendanceByEmployee = new Map(attendance.map((a) => [a.emp_code, a]));
  if (!employees.length) return <EmptyState loading={loading} message="No employees found. Add an employee or connect the backend." />;

  return <div className="table-scroll"><table><thead><tr><th>EMPLOYEE</th><th>DEPARTMENT</th><th>PUNCH IN</th><th>PUNCH OUT</th><th>STATUS</th>{showActions && <th>ACTIONS</th>}</tr></thead><tbody>
    {employees.map((e) => {
      const a = attendanceByEmployee.get(e.emp_code);
      const status = a?.status || "NOT MARKED";
      const statusClass = ["PRESENT", "WFH", "ON_DUTY"].includes(status) ? "present" : status === "LEAVE" ? "leave" : status === "ABSENT" ? "absent" : "unmarked";
      return <tr key={e.emp_code}>
        <td><div className="employee-cell"><div className="employee-avatar">{initials(e.name)}</div><div className="employee-name"><b>{e.name}</b><span>{e.emp_code}</span></div></div></td>
        <td><span className="department-tag">{e.department}</span></td>
        <td className="time-cell">{clock(a?.punch_in)}</td><td className="time-cell">{clock(a?.punch_out)}</td>
        <td><span className={`status-badge ${statusClass}`}><i />{status.replace("_", " ")}</span></td>
        {showActions && <td><span className="row-actions"><button title="Punch in" onClick={() => window.dispatchEvent(new CustomEvent("hr-punch-in", { detail: e.emp_code }))}><LogIn size={15} /></button><button title="Punch out" onClick={() => window.dispatchEvent(new CustomEvent("hr-punch-out", { detail: e.emp_code }))}><LogOut size={15} /></button></span></td>}
      </tr>;
    })}
  </tbody></table></div>;
}

const styles = `
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@400;500;600;700;800&display=swap');
:root{font-family:'DM Sans',sans-serif;color:#25263a;background:#f7f8fc;font-synthesis:none;text-rendering:optimizeLegibility}
*{box-sizing:border-box}body{margin:0;min-width:320px}.app-shell{min-height:100vh;background:#f7f8fc}.sidebar{width:250px;background:#fff;border-right:1px solid #eceef4;position:fixed;inset:0 auto 0 0;padding:25px 17px;display:flex;flex-direction:column;z-index:10}.brand{display:flex;align-items:center;gap:11px;padding:0 9px 31px}.brand-icon{height:39px;width:39px;border-radius:12px;background:#6758e8;color:white;display:grid;place-items:center;box-shadow:0 5px 12px #6758e82b}.brand strong{display:block;font:800 22px Manrope,sans-serif;letter-spacing:-.8px}.brand span{font-size:9px;letter-spacing:1.7px;color:#8b8fa1;font-weight:700}.workspace-label,.nav-label{font-size:10px;letter-spacing:1.4px;color:#9b9fb0;font-weight:700;padding:0 10px 10px}.workspace{display:flex;align-items:center;gap:10px;background:#f8f8fc;border:1px solid #eeeef5;border-radius:10px;padding:11px;margin-bottom:28px}.workspace-avatar{background:#e5e2ff;color:#6151db;font-weight:800;width:34px;height:34px;border-radius:9px;display:grid;place-items:center}.workspace-text{min-width:0;flex:1}.workspace-text b,.workspace-text span{display:block;white-space:nowrap}.workspace-text b{font-size:11px}.workspace-text span{font-size:10px;color:#9699aa;margin-top:3px}.workspace>svg{color:#999caf}.nav-label{margin-bottom:5px}.nav-item{height:43px;border:0;background:transparent;display:flex;align-items:center;gap:12px;padding:0 12px;border-radius:9px;width:100%;color:#7f8396;font:500 13px 'DM Sans',sans-serif;text-align:left;margin-bottom:4px;cursor:pointer}.nav-item svg{color:#9296a8}.nav-item.active{background:#f0efff;color:#5e50dc;font-weight:700}.nav-item.active svg{color:#6758e8}.nav-item span:nth-child(2){flex:1}.nav-dot{width:7px;height:7px;background:#ed9d57;border-radius:50%}.second-label{margin-top:24px}.sidebar-bottom{margin-top:auto}.help-card{background:#f7f7fd;border:1px solid #efeff7;border-radius:12px;padding:14px;margin-bottom:18px}.help-icon{color:#6758e8;margin-bottom:10px}.help-card>b{font-size:12px}.help-card p{font-size:10px;color:#9295a6;margin:5px 0 11px}.status-line{font-size:11px;font-weight:600;display:flex;align-items:center;gap:7px}.status-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#e9a04d;flex-shrink:0}.status-dot.online{background:#25b887}.profile{border-top:1px solid #eeeef4;padding:18px 2px 0;display:flex;align-items:center;gap:10px}.profile-avatar,.top-avatar{width:35px;height:35px;border-radius:50%;background:#e5e2ff;color:#6254df;display:grid;place-items:center;font-size:11px;font-weight:800}.profile-info{flex:1}.profile-info b,.profile-info span{display:block}.profile-info b{font-size:12px}.profile-info span{font-size:10px;color:#9598a9;margin-top:3px}.profile>svg{color:#9296a8}.main-content{margin-left:250px;min-height:100vh}.topbar{height:73px;background:#fff;border-bottom:1px solid #eceef4;display:flex;align-items:center;justify-content:space-between;padding:0 36px}.breadcrumbs{display:flex;align-items:center;gap:11px;color:#9a9daf;font-size:12px}.breadcrumbs b{color:#414258;font-weight:600}.top-actions{display:flex;align-items:center;gap:23px}.today-label{display:flex;align-items:center;gap:8px;font-size:11px;color:#777b90}.today-label svg{color:#999caf}.icon-button{position:relative;border:0;background:transparent;color:#777b90;display:grid;place-items:center;cursor:pointer;padding:7px}.icon-button i{position:absolute;right:5px;top:4px;width:6px;height:6px;border-radius:50%;background:#f29a5b;border:1px solid white}.top-avatar{background:#f6dfd2;color:#a75c36}.page{max-width:1500px;margin:auto;padding:31px 36px 20px}.page-heading{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:29px}.eyebrow{font-size:9px;font-weight:800;letter-spacing:1.8px;color:#786bea;margin-bottom:9px}.page-heading h1{font:800 27px Manrope,sans-serif;letter-spacing:-1px;margin:0;color:#27283e}.subtitle{font-size:12px;color:#9295a7;margin:8px 0 0}.heading-actions{display:flex;gap:10px}.button{border:1px solid #e6e7f0;border-radius:8px;padding:10px 14px;display:inline-flex;align-items:center;justify-content:center;gap:8px;font:600 11px 'DM Sans',sans-serif;cursor:pointer;white-space:nowrap}.button.primary{background:#6658e8;border-color:#6658e8;color:#fff;box-shadow:0 4px 9px #6658e826}.button.secondary{background:#fff;color:#62657a}.button:hover{filter:brightness(.98);transform:translateY(-1px)}.alert-banner,.notice-banner{display:flex;align-items:center;gap:10px;padding:12px 14px;border-radius:9px;margin-bottom:19px;font-size:11px}.alert-banner{background:#fff7ed;color:#9b5b25;border:1px solid #f9e4cd}.alert-banner span{flex:1}.alert-banner button{border:0;background:transparent;color:#9b5b25;display:flex;align-items:center;gap:5px;cursor:pointer}.notice-banner{background:#effbf6;border:1px solid #d7f4e7;color:#197b5c}.notice-banner span{flex:1}.notice-banner button{border:0;background:transparent;color:inherit;cursor:pointer}.section-heading{display:flex;align-items:center;justify-content:space-between;margin-bottom:15px}.section-heading h2{font:700 16px Manrope,sans-serif;margin:0}.section-heading p{font-size:11px;color:#999bad;margin:5px 0 0}.month-picker{display:flex;align-items:center;gap:8px;background:#fff;border:1px solid #e8e9f1;border-radius:8px;padding:8px 10px;color:#888ca0}.month-picker input{border:0;outline:none;background:transparent;font:500 11px 'DM Sans',sans-serif;color:#62657a;width:112px}.stats-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin-bottom:22px}.stat-card{background:#fff;border:1px solid #eceef4;border-radius:12px;padding:18px 18px 16px;min-width:0;box-shadow:0 2px 5px #2b2e4a03}.stat-top{display:flex;align-items:center;justify-content:space-between;gap:5px}.stat-top>span{font-size:11px;color:#81859a;font-weight:500}.stat-icon{width:38px;height:38px;border-radius:10px;display:grid;place-items:center;flex-shrink:0}.stat-icon.purple{background:#f0edff;color:#6758e8}.stat-icon.green{background:#e7f8f0;color:#26a77d}.stat-icon.orange{background:#fff1e5;color:#e8954b}.stat-icon.blue{background:#e9f3ff;color:#428be1}.stat-value{font:800 29px Manrope,sans-serif;letter-spacing:-1px;margin-top:13px;color:#2b2c42}.stat-caption{font-size:10px;color:#9a9daf;margin-top:8px;display:flex;align-items:center;gap:6px}.caption-dot{width:5px;height:5px;border-radius:50%;background:#b3b5c4}.content-grid{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(260px,1fr);gap:18px;margin-bottom:20px}.panel{background:#fff;border:1px solid #eceef4;border-radius:12px;min-width:0;box-shadow:0 2px 5px #2b2e4a03}.trend-panel,.quick-panel,.department-panel,.leaderboard-panel{padding:20px}.panel-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}.panel-heading h3{font:700 13px Manrope,sans-serif;margin:0;color:#303147}.panel-heading p{font-size:10px;color:#9a9daf;margin:5px 0 0}.chart-badge{display:flex;align-items:center;gap:5px;background:#f0efff;color:#6859df;padding:7px 9px;border-radius:7px;font-size:10px;font-weight:600}.chart-wrap{height:235px;margin-top:18px;width:100%}.chart-legend{display:flex;gap:17px;border-top:1px solid #f0f1f6;padding-top:13px;margin-top:6px}.chart-legend span{display:flex;align-items:center;gap:7px;color:#9295a7;font-size:10px}.chart-legend i{width:7px;height:7px;border-radius:2px}.legend-purple{background:#6658e8}.legend-orange{background:#f4a261}.quick-icon{color:#695be8;background:#f0efff;width:34px;height:34px;border-radius:9px;display:grid;place-items:center}.quick-action{width:100%;display:flex;align-items:center;gap:11px;padding:14px 0;border:0;border-bottom:1px solid #f0f1f6;background:white;text-align:left;cursor:pointer}.quick-action:last-of-type{border-bottom:0}.quick-action-icon{width:37px;height:37px;display:grid;place-items:center;border-radius:10px;flex-shrink:0}.green-bg{background:#e6f8ef;color:#24a77a}.blue-bg{background:#e9f3ff;color:#428be1}.purple-bg{background:#f0edff;color:#6758e8}.quick-action>div:nth-child(2){flex:1}.quick-action b,.quick-action span{display:block}.quick-action b{font-size:11px;color:#38394e}.quick-action div span{font-size:10px;color:#999cad;margin-top:4px}.action-arrow{color:#a0a3b3;font-size:15px}.quick-footer{border-top:1px solid #f0f1f6;padding-top:13px;margin-top:2px;display:flex;align-items:center;gap:7px;font-size:10px;color:#9295a7}.table-panel{padding:20px;margin-bottom:20px}.table-heading{margin-bottom:17px}.text-button{border:0;background:transparent;color:#6658e8;font:600 10px 'DM Sans',sans-serif;cursor:pointer}.table-scroll{width:100%;overflow-x:auto}table{border-collapse:collapse;width:100%;text-align:left;white-space:nowrap}thead{background:#fafafd}th{font-size:9px;letter-spacing:.7px;color:#9a9daf;font-weight:700;padding:12px 13px;border-top:1px solid #f0f1f6;border-bottom:1px solid #f0f1f6}td{padding:13px;border-bottom:1px solid #f0f1f6;font-size:11px;color:#666a7f}tbody tr:last-child td{border-bottom:0}.employee-cell{display:flex;align-items:center;gap:10px}.employee-avatar,.leader-avatar{height:33px;width:33px;display:grid;place-items:center;border-radius:10px;background:#efedff;color:#6a5be0;font-size:10px;font-weight:800;flex-shrink:0}.employee-name b,.employee-name span{display:block}.employee-name b{font-size:11px;color:#38394e}.employee-name span{font-size:10px;color:#a0a3b2;margin-top:4px}.department-tag{background:#f5f5fa;color:#777b90;border-radius:5px;padding:5px 7px;font-size:10px}.time-cell{font-variant-numeric:tabular-nums;color:#666a7d}.status-badge{display:inline-flex;align-items:center;gap:6px;padding:5px 8px;border-radius:5px;font-size:9px;font-weight:700}.status-badge i{height:5px;width:5px;border-radius:50%;background:currentColor}.status-badge.present{color:#21966e;background:#e8f8f1}.status-badge.leave{color:#9871d5;background:#f3edff}.status-badge.absent{color:#d56a6a;background:#fff0f0}.status-badge.unmarked{color:#888ca0;background:#f1f2f6}.row-actions{display:flex;gap:5px}.row-actions button{border:1px solid #e9eaf2;background:white;border-radius:6px;padding:6px;color:#777b90;cursor:pointer}.bottom-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:24px}.department-row,.leader-row{display:flex;align-items:center;gap:10px;padding:13px 0;border-bottom:1px solid #f0f1f6}.department-row:last-child,.leader-row:last-child{border-bottom:0}.department-avatar{width:34px;height:34px;border-radius:9px;background:#f0efff;color:#695be0;font-size:10px;font-weight:800;display:grid;place-items:center}.department-info,.leader-info{flex:1}.department-info b,.department-info span,.leader-info b,.leader-info span{display:block}.department-info b,.leader-info b{font-size:11px;color:#424359}.department-info span,.leader-info span{font-size:10px;color:#999cad;margin-top:4px}.department-metric{text-align:right}.department-metric b{font-size:12px;color:#414258}.department-metric span{display:block;font-size:9px;color:#a0a3b2;margin-top:4px}.muted-icon{color:#9b9eb0}.rank-number{font:700 11px Manrope,sans-serif;color:#a0a3b2;width:14px;text-align:center}.leader-avatar{border-radius:50%;background:#e8f4ff;color:#4487ca}.late-minutes{font-size:10px;color:#d68a44;background:#fff5e9;padding:6px 8px;border-radius:6px;font-weight:700}.empty-state{min-height:135px;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:9px;padding:20px;text-align:center}.empty-icon{color:#9a92e8;background:#f1efff;width:38px;height:38px;border-radius:11px;display:grid;place-items:center}.empty-state p{font-size:11px;line-height:1.6;color:#9a9daf;max-width:270px;margin:0}.search-wrap{display:flex;align-items:center;gap:9px;border:1px solid #e8e9f1;border-radius:8px;padding:10px 12px;color:#999cad;margin-bottom:17px;max-width:420px}.search-wrap input{width:100%;border:0;outline:0;background:transparent;font:12px 'DM Sans',sans-serif;color:#45465b}.attendance-actions{display:flex;gap:10px;padding-top:16px}.analytics-chart{padding:20px;margin-bottom:20px}.footer{display:flex;align-items:center;justify-content:space-between;color:#a1a4b3;font-size:10px;padding:4px 2px 12px}.footer>span:last-child{display:flex;align-items:center;gap:7px}.modal-backdrop{position:fixed;inset:0;background:#24253b70;z-index:30;display:flex;align-items:center;justify-content:center;padding:20px}.employee-modal{background:#fff;width:100%;max-width:460px;max-height:90vh;overflow:auto;padding:24px;border-radius:15px;box-shadow:0 20px 70px #17182b30}.modal-heading{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:20px}.modal-heading h2{font:800 20px Manrope,sans-serif;margin:0}.modal-heading p{font-size:11px;color:#999cad;margin:6px 0 0}.employee-modal label{display:flex;flex-direction:column;gap:7px;font-size:11px;font-weight:600;color:#55586e;margin-bottom:14px}.employee-modal input{border:1px solid #e4e6ef;border-radius:8px;padding:11px;font:12px 'DM Sans',sans-serif;outline:none;width:100%;background:#fff}.employee-modal input:focus{border-color:#887bf0;box-shadow:0 0 0 3px #887bf01c}.form-two{display:grid;grid-template-columns:1fr 1fr;gap:13px}.modal-actions{display:flex;justify-content:flex-end;gap:9px;margin-top:20px}.working-pill{position:fixed;bottom:22px;right:24px;z-index:50;background:#292a40;color:#fff;border-radius:9px;padding:12px 16px;display:flex;align-items:center;gap:9px;font-size:11px;box-shadow:0 8px 25px #25263a30}.spin{animation:spin 1s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
@media(max-width:1150px){.sidebar{width:220px}.main-content{margin-left:220px}.page{padding:25px 22px}.topbar{padding:0 22px}.stats-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.content-grid{grid-template-columns:minmax(0,1.3fr) minmax(240px,1fr)}}
@media(max-width:800px){.sidebar{width:65px;padding:20px 9px}.brand{padding:0 4px 28px}.brand>div:nth-child(2),.workspace-label,.workspace,.nav-label,.nav-item span,.sidebar-bottom{display:none}.nav-item{justify-content:center;padding:0}.nav-item svg{flex-shrink:0}.main-content{margin-left:65px}.topbar{padding:0 15px}.page{padding:22px 14px}.page-heading{align-items:flex-start;flex-direction:column}.heading-actions{width:100%}.content-grid,.bottom-grid{grid-template-columns:1fr}.today-label{display:none}.stats-grid{gap:10px}.stat-card{padding:13px}.stat-value{font-size:25px}.stat-top>span{font-size:10px}.table-panel{padding:14px}.footer{gap:12px;flex-wrap:wrap}.section-heading{gap:10px}.month-picker input{width:100px}}
@media(max-width:480px){.stats-grid{grid-template-columns:1fr 1fr}.stat-icon{width:31px;height:31px}.stat-value{font-size:23px}.page-heading h1{font-size:23px}.heading-actions .button{flex:1}.top-actions{gap:10px}.breadcrumbs{gap:7px}.trend-panel,.quick-panel,.department-panel,.leaderboard-panel{padding:15px}.form-two{grid-template-columns:1fr 1fr}}
`;

export default App;

