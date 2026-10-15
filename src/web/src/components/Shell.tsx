import { Navigate, Outlet, useLocation, useNavigate } from "react-router";
import { landingPath, useAuth } from "../auth/context.ts";
import { useNavOpen } from "../lib/navState.ts";
import "../styles/surfaces/shell.css";
import { LeftNav } from "./LeftNav.tsx";
// Primitives by file, not through ui/index.ts: the index also loads MotionProvider, and this module is
// loaded by a Vitest setup file (tests/warm-lazy-routes.tsx) before ui.test.tsx mocks motion/react.
import { Button } from "./ui/Button.tsx";
import { ErrorState } from "./ui/ErrorState.tsx";
import { Skeleton } from "./ui/Skeleton.tsx";
import "./ui/ui.css";

/** Gate for every signed-in route. It renders no chrome: /stage is full-bleed, the rest sit in the Shell. */
export function RequireAuth() {
  const { me, loading, error, refetch } = useAuth();
  const location = useLocation();
  if (loading) return <ShellPending />;
  if (!me && error) {
    return (
      <main className="shell-state">
        <ErrorState message="Could not check who is logged in." onRetry={refetch} />
      </main>
    );
  }
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  return <Outlet />;
}

/** The skeleton shown before the first answer from the server (also the router's HydrateFallback). */
export function ShellPending() {
  return (
    <main className="shell-state">
      <Skeleton shape="block" />
    </main>
  );
}

/** `/` goes to the real role's landing page; "Preview as Recruiter" does not change it. */
export function Landing() {
  const { realRole } = useAuth();
  return realRole ? <Navigate to={landingPath(realRole)} replace /> : null;
}

export function Shell() {
  const { me, role, realRole, isPreviewingRecruiter, setPreviewRecruiter, logout } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [navOpen, toggleNav] = useNavOpen();
  if (!me) return null;

  const onLogout = async () => {
    await logout();
    navigate("/login", { replace: true });
  };

  const classes = ["shell", role === "recruiter" && "client-accent", navOpen ? "shell-nav-open" : "shell-nav-collapsed"];
  return (
    <div className={classes.filter(Boolean).join(" ")}>
      <div className="shell-frame">
        <LeftNav
          role={role}
          realRole={realRole}
          isPreviewingRecruiter={isPreviewingRecruiter}
          userName={me.user.name}
          open={navOpen}
          onToggle={toggleNav}
          onPreview={() => setPreviewRecruiter(true)}
          onLogout={onLogout}
        />
        <div className="shell-main">
          {isPreviewingRecruiter && (
            <div className="preview-banner" role="status">
              Previewing the Recruiter view. Economics views and cost fields are hidden.{" "}
              <Button className="shell-banner-action" onClick={() => setPreviewRecruiter(false)}>
                Back to Operator view
              </Button>
            </div>
          )}
          <main className="page">
            <div key={pathname} className="shell-route">
              <Outlet />
            </div>
          </main>
        </div>
      </div>
    </div>
  );
}
