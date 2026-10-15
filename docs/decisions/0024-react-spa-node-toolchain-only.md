# 0024. The frontend is a static React app; Node is only the build toolchain

- Status: Accepted
- Date: 2026-10-09
- Resolves: the open item in 0022

## Context
The owner named React and Node for the frontend, while the backend must be Python (0022).

## Decision
- The web app is a React single-page app built with Vite. Node is used only to build it and to run the development server.
- The built files are served by a small static container (nginx). Every data call goes to the Python backend's API.
- There is no Node runtime in production.

## Alternatives considered
- **A Node server (for example Next.js) for server-side rendering or as an API layer:** a second backend language and a second API to secure, for no benefit on an internal dashboard behind a login.

## Consequences
- One API (Python) to secure and test.
- The frontend container holds static files only.
