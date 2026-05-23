import { Link, NavLink, Outlet } from "react-router-dom";

export default function Layout() {
  return (
    <div className="min-h-screen flex flex-col">
      <header className="bg-gray-900 border-b border-gray-800 px-6 py-3 flex items-center gap-6">
        <Link to="/workflows" className="text-brand-500 font-bold text-lg tracking-tight">
          🛰 Planet CDC
        </Link>
        <nav className="flex gap-4 text-sm">
          {[
            { to: "/workflows", label: "Workflows" },
            { to: "/models", label: "Models" },
            { to: "/providers", label: "Providers" },
            { to: "/worker", label: "Worker" },
          ].map(({ to, label }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                isActive ? "text-white" : "text-gray-400 hover:text-gray-200"
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className="flex-1 overflow-auto">
        <Outlet />
      </main>
    </div>
  );
}
