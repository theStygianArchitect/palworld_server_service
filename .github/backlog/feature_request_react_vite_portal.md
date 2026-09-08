---
name: Feature Request: Full React + Vite Single-Page Application (SPA) Management Portal
about: Transition Palworld Operations Suite frontend to a modular React + Vite + TypeScript SPA
labels: enhancement, frontend, architecture, backlog
---

# Feature Request: Transition to React + Vite Single-Page Application (SPA) Portal

## 🚀 Feature Proposal
Migrate the Palworld Operations Suite web frontend from monolithic server-served Jinja/HTML templates into a dedicated, modern Single-Page Application (SPA) built with **React 18/19**, **Vite**, **TypeScript**, and **Tailwind CSS**. The SPA will be developed in a standalone `frontend/` directory, build static assets into `app/static/dist/`, and be served seamlessly by FastAPI with client-side routing.

## 🎯 Problem / User Story
- **Current State**:
  - The web interface currently operates as a server-rendered monolithic template (`index.html` and `metrics.html`) using vanilla JavaScript DOM manipulations and CDN scripts.
  - While this design delivers **zero Node.js dependencies on the host** and instant bootstrapping, complex interactive features (e.g. multi-user session state, live chart zooming, interactive grid layout, reactive settings form validation, and dark/light theming) become harder to maintain as single HTML files scale past 2,500 lines.
- **User Story**:
  - *As a server administrator and community manager, I want a modular, desktop-grade Single-Page Application with instant client-side tab switching, interactive Prometheus chart zoom/brushing, role-based component hiding, and stateful form editing so that managing my Palworld dedicated server feels as smooth as enterprise cloud management consoles.*

## 💡 Proposed Solution & Architecture

### 1. Project Directory Structure
```
palworld_server_service/
├── frontend/                     # Dedicated Vite + React workspace
│   ├── src/
│   │   ├── api/                  # Typed REST & WebSocket client SDK
│   │   ├── components/           # Reusable UI primitives (Cards, Modals, Badges, Tabs)
│   │   ├── contexts/             # AuthContext (RBAC), TelemetryContext (WebSocket)
│   │   ├── pages/                # Route views (Dashboard, Observability, Feedback, Users)
│   │   ├── App.tsx               # Root application router
│   │   └── main.tsx              # React entrypoint
│   ├── package.json
│   ├── tsconfig.json
│   └── vite.config.ts            # Configured to output bundle to ../app/static/dist
├── app/
│   ├── api/                      # Existing FastAPI REST API endpoints
│   ├── static/dist/              # Built SPA assets (HTML, JS, CSS, SVG)
│   └── main.py                   # Serves index.html with SPA fallback route
```

### 2. Core Frontend Capabilities
- **State Management & Telemetry**:
  - Single persistent WebSocket connection shared across all routes via `TelemetryContext`.
  - React Query (`@tanstack/react-query`) for cached REST queries (`/api/settings`, `/api/metrics/history`, `/api/feedback`, `/api/users`) with automatic background refetching and optimistic mutations.
- **Observability Plane**:
  - High-performance canvas chart rendering with `@visx` or `Chart.js` / `uPlot` supporting time-scrubbing, synchronized crosshair tooltips across all panels, and custom Prometheus queries.
- **Role-Based Access Control (RBAC)**:
  - Strongly typed permissions (`server:settings`, `players:kick`, `players:ban`, `logs:view`, `admin:users`) controlling conditional rendering of action buttons, modals, and management panels.
- **Local Development Experience**:
  - Vite dev server running on `:5173` proxying `/api` and `/ws` to FastAPI running on `:8080` with hot module replacement (HMR).

## 🧱 12-Factor & Resilience Considerations
- **Stateless Build Artifacts**:
  - Production builds (`npm run build`) produce static hashed bundles in `app/static/dist/`.
  - Zero Node.js runtime required in production or systemd service execution; FastAPI serves static assets via `StaticFiles`.
- **Backward Compatibility**:
  - Fallback mechanism ensuring that if `app/static/dist/index.html` does not exist, FastAPI gracefully falls back to the embedded lightweight template.

## 🔄 Alternatives Considered
- **Option 1 (Retained for Current Milestone)**: Multi-page vanilla HTML + Tailwind templates served directly by FastAPI. Provides immediate availability, zero build toolchain requirements, and full operational capability without waiting for frontend refactoring.
- **HTMX + Alpine.js**: Evaluated as a middle ground between pure vanilla DOM and full React; rejected in favor of React due to richer ecosystem of interactive time-series graphing libraries and typed component design systems.
