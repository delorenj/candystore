import { Suspense, lazy } from "react";
import { Link, NavLink, Route, BrowserRouter as Router, Routes } from "react-router-dom";
import candystoreLogo from "./assets/candystore-logo.png";
import ProjectFeed from "./pages/ProjectFeed.jsx";

const EventDetail = lazy(() => import("./pages/EventDetail.jsx"));
const HeatMap = lazy(() => import("./pages/HeatMap.jsx"));
const SessionTimeline = lazy(() => import("./pages/SessionTimeline.jsx"));

const navItems = [
  { to: "/projects", label: "Feed" },
  { to: "/heatmap", label: "Heatmap" },
  { to: "/sessions", label: "Sessions" },
];

export default function App() {
  return (
    <Router>
      <div className="min-h-screen bg-ink text-zinc-100">
        <header className="border-b border-line bg-panel">
          <div className="mx-auto flex max-w-[110rem] flex-wrap items-center gap-4 px-4 py-3 sm:px-6">
            <Link
              to="/"
              className="focus-ring rounded bg-zinc-100 px-3 py-1.5 shadow-sm ring-1 ring-white/10 transition hover:bg-white"
              aria-label="Candystore home"
            >
              <img src={candystoreLogo} alt="Candystore" className="h-8 w-auto sm:h-9" />
            </Link>
            <nav className="flex items-center gap-1 text-sm">
              {navItems.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) =>
                    [
                      "focus-ring rounded px-3 py-2 text-zinc-300 hover:bg-zinc-800 hover:text-white",
                      isActive ? "bg-zinc-800 text-white" : "",
                    ].join(" ")
                  }
                >
                  {item.label}
                </NavLink>
              ))}
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-[110rem] px-4 py-4 sm:px-6">
          <Suspense fallback={<div className="text-sm text-zinc-400">loading</div>}>
            <Routes>
              <Route path="/" element={<ProjectFeed />} />
              <Route path="/projects" element={<ProjectFeed />} />
              <Route path="/projects/:slug" element={<ProjectFeed />} />
              <Route path="/events/:id" element={<EventDetail />} />
              <Route path="/heatmap" element={<HeatMap />} />
              <Route path="/sessions" element={<SessionTimeline />} />
              <Route path="/sessions/:id" element={<SessionTimeline />} />
            </Routes>
          </Suspense>
        </main>
      </div>
    </Router>
  );
}
