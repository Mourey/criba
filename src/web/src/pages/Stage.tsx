import { StageGate } from "../stage/StageGate.tsx";

// The Operator's demo stage, full-bleed outside the Shell. The role check and the lazy load of the
// panes live in the gate, so anyone else gets Page not found and never downloads the panes.
export function Stage() {
  return <StageGate />;
}
