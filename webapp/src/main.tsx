import { render } from "preact";
import "./styles.css";
import { App, ConfigProblem } from "./app";
import { createSource } from "./data";
import { interceptLinks, navigate } from "./router";

async function start() {
  const root = document.getElementById("app");
  if (!root) return;
  const result = await createSource();
  if ("problem" in result) {
    render(<ConfigProblem message={result.problem} />, root);
    return;
  }
  interceptLinks();
  if (!result.source.demo && "serviceWorker" in navigator) {
    // 푸시 알림을 받고, 알림을 누르면 그 실행 기록을 연다(public/sw.js)
    navigator.serviceWorker.register("/sw.js").catch((err) => console.warn("서비스 워커 등록 실패", err));
    navigator.serviceWorker.addEventListener("message", (e) => {
      const data = e.data as { type?: string; url?: unknown } | null;
      if (data?.type === "navigate" && typeof data.url === "string") navigate(data.url);
    });
  }
  render(<App source={result.source} />, root);
}

start();
