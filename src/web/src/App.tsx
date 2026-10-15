import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider } from "react-router";
import { MotionProvider } from "./components/ui/MotionProvider.tsx";
import { routes } from "./routes.tsx";

const queryClient = new QueryClient();
const router = createBrowserRouter(routes);

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      {/* The one MotionProvider: every motion animation in the app honours reduced motion. */}
      <MotionProvider>
        <RouterProvider router={router} />
      </MotionProvider>
    </QueryClientProvider>
  );
}
