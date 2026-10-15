import { Link } from "react-router";
// Card by file, not through ui/index.ts (see Shell.tsx).
import { Card } from "../components/ui/Card.tsx";
import "../components/ui/ui.css";
import "../styles/surfaces/shell.css";

export function NotFound() {
  return (
    <Card>
      <h1>Page not found</h1>
      <p>
        <Link to="/">Back to the start page</Link>
      </p>
    </Card>
  );
}
