import type { RouteObject } from "react-router";
import { Root } from "./components/Root.tsx";
import { Landing, RequireAuth, Shell, ShellPending } from "./components/Shell.tsx";
import { ApplicationDetail } from "./pages/ApplicationDetail.tsx";
import { Applications } from "./pages/Applications.tsx";
import { Book } from "./pages/Book.tsx";
import { Login } from "./pages/Login.tsx";
import { NotFound } from "./pages/NotFound.tsx";
import { ReviewQueue } from "./pages/ReviewQueue.tsx";
import { Slots } from "./pages/Slots.tsx";
import { TestChat } from "./pages/TestChat.tsx";
import { Voice } from "./pages/Voice.tsx";

// The dashboards and the three stubs (/today, /overview, /stage) are lazy, so Recharts and each
// surface load only when opened. The booking page, the test chat and the voice page are public: they sit outside the
// auth provider, so they never ask who is logged in. Everything else needs a login: /stage is
// full-bleed with no chrome, the rest sit in the Shell. React Router ranks the static /stage above
// `*`, so it never falls into NotFound.
export const routes: RouteObject[] = [
  { path: "/book/:token", element: <Book /> },
  { path: "/test-chat", element: <TestChat /> },
  { path: "/voice", element: <Voice /> },
  {
    element: <Root />,
    HydrateFallback: ShellPending,
    children: [
      { path: "/login", element: <Login /> },
      {
        element: <RequireAuth />,
        children: [
          { path: "/stage", lazy: async () => ({ Component: (await import("./pages/Stage.tsx")).Stage }) },
          {
            element: <Shell />,
            children: [
              { index: true, element: <Landing /> },
              { path: "/today", lazy: async () => ({ Component: (await import("./pages/Today.tsx")).Today }) },
              { path: "/overview", lazy: async () => ({ Component: (await import("./dashboards/Overview.tsx")).Overview }) },
              { path: "/applications", element: <Applications /> },
              { path: "/applications/:id", element: <ApplicationDetail /> },
              { path: "/queue", element: <ReviewQueue /> },
              { path: "/slots", element: <Slots /> },
              { path: "/results", lazy: async () => ({ Component: (await import("./dashboards/Results.tsx")).Results }) },
              { path: "/economics", lazy: async () => ({ Component: (await import("./dashboards/Economics.tsx")).Economics }) },
              { path: "/conversations", lazy: async () => ({ Component: (await import("./pages/Conversations.tsx")).Conversations }) },
              { path: "/conversations/:id", lazy: async () => ({ Component: (await import("./pages/Conversation.tsx")).Conversation }) },
              { path: "*", element: <NotFound /> },
            ],
          },
        ],
      },
    ],
  },
];
