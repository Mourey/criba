import type { AppEvent, ApplicationDetail, Message, TakeoverState } from "../api/types.ts";

export function applyControl(app: ApplicationDetail, state: TakeoverState): ApplicationDetail {
  return {
    ...app,
    control: state.control,
    control_user_id: state.control === "human" ? state.user_id : null,
    control_user_name: state.control === "human" ? state.user_name : null,
  };
}

export function appendMessage(app: ApplicationDetail, message: Message): ApplicationDetail {
  if (app.messages.some((m) => m.id === message.id)) return app;
  return { ...app, messages: [...app.messages, message].sort((a, b) => a.id - b.id) };
}

export function appendEvent(app: ApplicationDetail, event: AppEvent): ApplicationDetail {
  if (app.events.some((e) => e.id === event.id)) return app;
  return { ...app, events: [...app.events, event].sort((a, b) => a.id - b.id) };
}
