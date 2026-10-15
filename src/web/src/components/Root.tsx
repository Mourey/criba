import { Outlet } from "react-router";
import { AuthProvider } from "../auth/AuthProvider.tsx";

export function Root() {
  return (
    <AuthProvider>
      <Outlet />
    </AuthProvider>
  );
}
