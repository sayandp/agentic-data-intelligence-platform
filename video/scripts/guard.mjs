// Refuse to film anything unless the dashboard on BASE is this project.
// Another project has squatted on :5173 before; a capture against it would
// put someone else's UI in the video.
export const BASE = process.env.APP_URL ?? "http://localhost:5173";
export const API = process.env.API_URL ?? "http://127.0.0.1:8000";

export async function assertThisApp(page) {
  await page.goto(`${BASE}/sources`);
  const title = await page.title();
  if (title !== "Agentic Data Intelligence Platform") {
    throw new Error(`ABORT: ${BASE} serves "${title}", not this app`);
  }
  // An app-specific element, not just a title another Vite app could share.
  await page.getByRole("heading", { name: "Sources", exact: true }).waitFor({ timeout: 20_000 });
  const health = await fetch(`${API}/health`).then((r) => r.status).catch(() => 0);
  if (health !== 200) throw new Error(`ABORT: backend ${API} is not healthy`);
}
